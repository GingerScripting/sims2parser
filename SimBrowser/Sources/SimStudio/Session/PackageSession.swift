import Foundation
import SwiftUI
import SimKit

/// One open package: its daemon, its index, the selected resource, and the
/// edits in flight. The daemon is the source of truth for bytes, dirtiness,
/// and history; this object mirrors what it says and never holds a byte the
/// daemon has not handed over as hex for display.
@MainActor
final class PackageSession: ObservableObject, Identifiable {
    enum Phase: Equatable {
        case opening
        case ready
        case failed(String)
    }

    let url: URL
    let id = UUID()

    @Published private(set) var phase: Phase = .opening
    @Published private(set) var summary: PackageSummary?
    @Published private(set) var rows: [ResourceRow] = []
    @Published private(set) var meta: Meta?
    @Published private(set) var bhavMeta: BhavMeta?
    @Published private(set) var detail: ResourceDetail?
    @Published private(set) var detailLoading = false
    @Published private(set) var busy = false
    @Published var errorMessage: String?
    @Published private(set) var progress: Progress?

    /// What the table has selected. The detail pane follows a single
    /// selection; the context menu and split act on the whole set.
    @Published var selectedTGIs: Set<TGI> = [] {
        didSet {
            let single = selectedTGIs.count == 1 ? selectedTGIs.first : nil
            if single != selection { selection = single }
        }
    }

    @Published private(set) var selection: TGI? {
        didSet { if selection != oldValue { loadDetail() } }
    }

    private var client: JSONRPCClient?
    private var detailTask: Task<Void, Never>?

    init(url: URL) {
        self.url = url
        trace("session \(id.uuidString.prefix(8)) init \(url.lastPathComponent)")
    }

    deinit {
        // The window closed and SwiftUI released its state object; the
        // daemon has nothing left to serve.
        client?.shutdown()
    }

    // MARK: Derived

    var isReadonly: Bool { summary?.readonly ?? true }
    var isDirty: Bool { summary?.dirty ?? false }
    var canUndo: Bool { summary?.canUndo ?? false }
    var canRedo: Bool { summary?.canRedo ?? false }
    var undoLabel: String { summary?.undoLabel.map { "Undo \($0)" } ?? "Undo" }
    var redoLabel: String { summary?.redoLabel.map { "Redo \($0)" } ?? "Redo" }
    var currentURL: URL { summary.map { URL(fileURLWithPath: $0.path) } ?? url }
    var title: String { currentURL.lastPathComponent }

    func typeName(_ type: UInt32) -> String { meta?.typeName(type) ?? hex8(type) }

    // MARK: Lifecycle

    /// Launch the daemon and open the package. Called once from the window.
    func start() async {
        trace("session \(id.uuidString.prefix(8)) start (client \(client == nil ? "nil" : "set"))")
        guard client == nil else { return }
        let python = PythonLocator.interpreter()
        let script = PythonLocator.script("s2studio.py", defaultsKey: "studioPath")
        do {
            let c = try JSONRPCClient(python: python, script: script)
            c.onEvent = { [weak self] value in
                guard let p = Progress(value) else { return }
                Task { @MainActor in self?.progress = p.done >= p.total ? nil : p }
            }
            client = c
            meta = try await c.call("meta", as: Meta.self)
            summary = try await c.call("open", ["path": .string(url.path)], as: PackageSummary.self)
            rows = try ResourceRow.rows(fromIndex: await c.callRaw("index"))
            phase = .ready
        } catch {
            phase = .failed("Could not open \(url.lastPathComponent) with \(python) \(script): "
                            + describe(error))
        }
    }

    func close() {
        detailTask?.cancel()
        client?.shutdown()
        client = nil
    }

    // MARK: Reads

    func reloadIndex() async {
        guard let c = client else { return }
        do {
            rows = try ResourceRow.rows(fromIndex: await c.callRaw("index"))
        } catch {
            report(error)
        }
    }

    private func loadDetail() {
        detailTask?.cancel()
        guard let tgi = selection, let c = client else {
            detail = nil
            return
        }
        detailLoading = true
        detailTask = Task {
            do {
                let d = try await c.call("get_resource", ["tgi": tgi.json], as: ResourceDetail.self)
                if !Task.isCancelled, selection == tgi {
                    detail = d
                }
            } catch {
                if !Task.isCancelled { report(error) }
            }
            if selection == tgi { detailLoading = false }
        }
    }

    /// Re-fetch the selected resource after an edit, keeping the selection.
    func refreshDetail() {
        loadDetail()
    }

    /// Fetched once, the first time a BHAV editor opens.
    func loadBhavMeta() async {
        guard bhavMeta == nil, let c = client else { return }
        do {
            bhavMeta = try await c.call("bhav_meta", as: BhavMeta.self)
        } catch {
            report(error)
        }
    }

    /// Insert, delete, or move an instruction in a draft. Pure on the
    /// daemon side: the package is untouched until the draft is applied.
    func bhavTransform(_ decoded: JSONValue, op: String, index: Int, to: Int? = nil) async -> BhavTransform? {
        guard let c = client else { return nil }
        var params: [String: JSONValue] = ["decoded": decoded, "op": .string(op), "index": .int(index)]
        if let to { params["to"] = .int(to) }
        do {
            return try await c.call("bhav_transform", params, as: BhavTransform.self)
        } catch {
            report(error)
            return nil
        }
    }

    // MARK: Edits

    @discardableResult
    func putDecoded(_ tgi: TGI, _ value: JSONValue) async -> Bool {
        await mutate {
            let r = try await $0.call("put_resource", ["tgi": tgi.json, "decoded": value], as: PutResult.self)
            self.summary = r.summary
            self.replaceRow(tgi: tgi, size: r.size)
        }
    }

    @discardableResult
    func putHex(_ tgi: TGI, _ bytes: [UInt8]) async -> Bool {
        await mutate {
            let r = try await $0.call("put_resource", ["tgi": tgi.json, "hex": .string(bytes.hexString)], as: PutResult.self)
            self.summary = r.summary
            self.replaceRow(tgi: tgi, size: r.size)
        }
    }

    func addResource(_ tgi: TGI, bytes: [UInt8], compressed: Bool = false) async -> Bool {
        await mutate {
            let r = try await $0.call("add_resource", ["tgi": tgi.json, "hex": .string(bytes.hexString),
                                                       "compressed": .bool(compressed)], as: RowResult.self)
            self.summary = r.summary
            self.rows.append(r.row)
            self.selectedTGIs = [r.row.tgi]
        }
    }

    func delete(_ tgis: [TGI]) async -> Bool {
        guard !tgis.isEmpty else { return false }
        return await mutate {
            self.summary = try await $0.call("delete_resource", ["tgis": .array(tgis.map(\.json))], as: PackageSummary.self)
            let gone = Set(tgis)
            self.rows.removeAll { gone.contains($0.tgi) }
            self.selectedTGIs.subtract(gone)
        }
    }

    func rename(_ tgi: TGI, to newTGI: TGI) async -> Bool {
        await mutate {
            let r = try await $0.call("rename_resource", ["tgi": tgi.json, "new_tgi": newTGI.json], as: RowResult.self)
            self.summary = r.summary
            if let i = self.rows.firstIndex(where: { $0.tgi == tgi }) { self.rows[i] = r.row }
            if self.selectedTGIs.contains(tgi) {
                self.selectedTGIs.remove(tgi)
                self.selectedTGIs.insert(r.row.tgi)
            }
        }
    }

    func setCompressed(_ tgis: [TGI], _ flag: Bool) async -> Bool {
        await mutate {
            self.summary = try await $0.call("set_compressed", ["tgis": .array(tgis.map(\.json)),
                                                                 "compressed": .bool(flag)], as: PackageSummary.self)
            await self.reloadIndex()
        }
    }

    func setAllCompressed(_ flag: Bool) async -> Bool {
        await mutate {
            self.summary = try await $0.call("set_compressed", ["all": .bool(true), "compressed": .bool(flag)],
                                             as: PackageSummary.self)
            await self.reloadIndex()
        }
    }

    func undo() async {
        await history("undo")
    }

    func redo() async {
        await history("redo")
    }

    private func history(_ method: String) async {
        await mutate {
            let r = try await $0.call(method, as: HistoryResult.self)
            self.summary = r.summary
            await self.reloadIndex()
            if let s = self.selection, r.tgis.contains(s) { self.refreshDetail() }
            else if self.selection == nil, let first = r.tgis.first,
                    self.rows.contains(where: { $0.tgi == first }) { self.selectedTGIs = [first] }
        }
    }

    // MARK: Files

    func save() async -> Bool {
        await mutate {
            self.summary = try await $0.call("save", as: PackageSummary.self)
        }
    }

    func saveAs(_ dest: URL) async -> Bool {
        await mutate {
            self.summary = try await $0.call("save_as", ["path": .string(dest.path)], as: PackageSummary.self)
        }
    }

    func exportBytes(_ tgi: TGI, to dest: URL) async -> Bool {
        await mutate {
            _ = try await $0.call("export_resource", ["tgi": tgi.json, "path": .string(dest.path)], as: JSONValue.self)
        }
    }

    func importBytes(_ tgi: TGI, from src: URL) async -> Bool {
        await mutate {
            let r = try await $0.call("import_resource", ["tgi": tgi.json, "path": .string(src.path)], as: PutResult.self)
            self.summary = r.summary
            self.replaceRow(tgi: tgi, size: r.size)
        }
    }

    // MARK: Object Workshop and tools

    func objects() async -> [ObjectInfo] {
        guard let c = client else { return [] }
        do { return try await c.call("objects", as: ObjectsResult.self).objects }
        catch { report(error); return [] }
    }

    func deriveGuid(seed: String) async -> UInt32? {
        guard let c = client else { return nil }
        struct R: Decodable { let guid: UInt32 }
        return try? await c.call("derive_guid", ["seed": .string(seed)], as: R.self).guid
    }

    func clone(guid: UInt32?, selectGuid: UInt32?, name: String?, description: String?,
               price: Int?, aggressive: Bool) async -> CloneResult? {
        guard let c = client else { return nil }
        var p: [String: JSONValue] = ["aggressive": .bool(aggressive)]
        if let guid { p["guid"] = .number(Double(guid)) }
        if let selectGuid { p["select_guid"] = .number(Double(selectGuid)) }
        if let name, !name.isEmpty { p["name"] = .string(name) }
        if let description, !description.isEmpty { p["description"] = .string(description) }
        if let price { p["price"] = .int(price) }
        busy = true
        defer { busy = false }
        do {
            let r = try await c.call("clone", p, as: CloneResult.self)
            summary = r.summary
            await reloadIndex()
            refreshDetail()
            return r
        } catch {
            report(error)
            return nil
        }
    }

    func scanGuids(_ guids: [UInt32]) async -> ScanResult? {
        guard let c = client else { return nil }
        do {
            return try await c.call("scan_guids", ["guids": .array(guids.map { .number(Double($0)) })],
                                    as: ScanResult.self, timeout: 600)
        } catch {
            report(error)
            return nil
        }
    }

    func merge(_ url: URL, replace: Bool) async -> MergeResult? {
        guard let c = client else { return nil }
        busy = true
        defer { busy = false }
        do {
            let r = try await c.call("merge", ["path": .string(url.path),
                                               "on_conflict": .string(replace ? "replace" : "skip")],
                                     as: MergeResult.self)
            summary = r.summary
            await reloadIndex()
            return r
        } catch {
            report(error)
            return nil
        }
    }

    func split(_ tgis: [TGI], to url: URL, remove: Bool) async -> SplitResult? {
        guard let c = client, !tgis.isEmpty else { return nil }
        busy = true
        defer { busy = false }
        do {
            let r = try await c.call("split", ["path": .string(url.path), "remove": .bool(remove),
                                               "tgis": .array(tgis.map(\.json))], as: SplitResult.self)
            summary = r.summary
            if remove {
                let gone = Set(tgis)
                rows.removeAll { gone.contains($0.tgi) }
                selectedTGIs.subtract(gone)
            }
            return r
        } catch {
            report(error)
            return nil
        }
    }

    func doctor(downloadsOnly: Bool, hashFiles: Bool) async -> DoctorResult? {
        guard let c = client else { return nil }
        do {
            return try await c.call("doctor", ["downloads_only": .bool(downloadsOnly),
                                               "no_hash": .bool(!hashFiles)],
                                    as: DoctorResult.self, timeout: 900)
        } catch {
            report(error)
            return nil
        }
    }

    func previewTexture(_ tgi: TGI) async throws -> TexturePreview {
        guard let c = client else { throw RPCFailure.transport("not open") }
        return try await c.call("preview_texture", ["tgi": tgi.json], as: TexturePreview.self)
    }

    func previewMesh(_ tgi: TGI) async throws -> MeshPreview {
        guard let c = client else { throw RPCFailure.transport("not open") }
        return try await c.call("preview_mesh", ["tgi": tgi.json], as: MeshPreview.self)
    }

    func exportTexture(_ tgi: TGI, to url: URL) async {
        guard let c = client else { return }
        do { _ = try await c.call("export_texture", ["tgi": tgi.json, "path": .string(url.path)], as: JSONValue.self) }
        catch { report(error) }
    }

    // MARK: Plumbing

    /// Run one daemon call, reporting failure through `errorMessage` and
    /// re-reading the selected resource afterwards so the detail pane never
    /// shows stale bytes.
    @discardableResult
    private func mutate(_ body: (JSONRPCClient) async throws -> Void) async -> Bool {
        guard let c = client else {
            errorMessage = "The package is not open."
            return false
        }
        busy = true
        defer { busy = false }
        do {
            try await body(c)
            refreshDetail()
            return true
        } catch {
            report(error)
            return false
        }
    }

    private func replaceRow(tgi: TGI, size: Int) {
        guard let i = rows.firstIndex(where: { $0.tgi == tgi }) else { return }
        let old = rows[i]
        rows[i] = ResourceRow(type: old.type, typeName: old.typeName, group: old.group,
                              instance: old.instance, instanceHi: old.instanceHi, size: size,
                              compressed: old.compressed, decodable: old.decodable, bhav: old.bhav, flags: old.flags)
    }

    private func report(_ error: Error) {
        errorMessage = describe(error)
    }

    private func describe(_ error: Error) -> String {
        if let f = error as? RPCFailure {
            switch f {
            case .remote(let e): return e.message
            case .transport(let s): return s
            }
        }
        return error.localizedDescription
    }
}

/// stderr breadcrumbs when SIMSTUDIO_TRACE is set; see JSONRPCClient.tracing.
func trace(_ message: @autoclosure () -> String) {
    if JSONRPCClient.tracing {
        FileHandle.standardError.write(Data((message() + "\n").utf8))
    }
}
