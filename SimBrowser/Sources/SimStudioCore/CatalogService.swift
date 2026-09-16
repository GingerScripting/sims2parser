import Foundation
import SwiftUI
import SimKit

/// The catalog behind the New Object screen. That window has no document
/// yet, so it runs a daemon of its own for `catalog`, `catalog_swatch` and
/// `project_new`; once the project exists on disk the daemon is shut down
/// and the project opens in an ordinary window with its own session.
@MainActor
public final class CatalogService: ObservableObject {
    @Published public private(set) var entries: [CatalogEntry] = []
    @Published public private(set) var functionSort: [SortBit] = []
    @Published public private(set) var roomSort: [SortBit] = []
    @Published public private(set) var loading = false
    @Published public private(set) var progress: TaskProgress?
    @Published public var errorMessage: String?
    @Published public private(set) var busy = false

    private var client: JSONRPCClient?
    private var swatches: [String: Data] = [:]
    private var pending: [String: Task<Data?, Never>] = [:]

    public init() {}

    /// `SIMSTUDIO_ROOT=<folder>` stands in for the game's user folder, so a
    /// headless check can point the catalog and Install at a scratch root
    /// instead of the game's own (privacy-protected) container.
    public static var gameRootOverride: String? = ProcessInfo.processInfo.environment["SIMSTUDIO_ROOT"]
    private var gameRootOverride: String? { Self.gameRootOverride }

    private func ensureClient() throws -> JSONRPCClient {
        if let c = client { return c }
        let c = try JSONRPCClient(python: PythonLocator.interpreter(),
                                  script: PythonLocator.script("s2studio.py", defaultsKey: "studioPath"))
        c.onEvent = { [weak self] value in
            guard let p = TaskProgress(value) else { return }
            Task { @MainActor in self?.progress = p.done >= p.total ? nil : p }
        }
        client = c
        return c
    }

    /// Read (or refresh) the catalog. The first run scans the game's objects
    /// and every package in Downloads, a few seconds; later runs are cached.
    public func load(refresh: Bool = false) async {
        loading = true
        defer { loading = false }
        do {
            let c = try ensureClient()
            _ = try await c.call("meta", as: JSONValue.self)
            var params: [String: JSONValue] = ["refresh": .bool(refresh)]
            if let root = gameRootOverride { params["root"] = .string(root) }
            let r = try await c.call("catalog", params, as: CatalogResult.self, timeout: 600)
            entries = r.entries
            functionSort = r.functionSort
            roomSort = r.roomSort
        } catch {
            // A cancelled load is the window going away, not news.
            if !(error is CancellationError) { errorMessage = describe(error) }
        }
    }

    /// The object's picture, fetched once and kept for the window's life.
    public func swatch(for entry: CatalogEntry) async -> Data? {
        if let d = swatches[entry.id] { return d }
        if let t = pending[entry.id] { return await t.value }
        let task = Task<Data?, Never> { [weak self] in
            guard let self, let c = self.client else { return nil }
            guard let r = try? await c.call("catalog_swatch", entry.json.objectValue ?? [:], as: SwatchResult.self) else { return nil }
            return Data(base64Encoded: r.pngB64)
        }
        pending[entry.id] = task
        let data = await task.value
        pending[entry.id] = nil
        if let data { swatches[entry.id] = data }
        return data
    }

    /// Create the project bundle at `url` from a base and an identity. On
    /// success the bundle is on disk and this service's daemon is done with
    /// it; open the URL in a window to edit.
    public func createProject(at url: URL, base: CatalogEntry, identity: ProjectIdentity) async -> Bool {
        busy = true
        defer { busy = false }
        do {
            let c = try ensureClient()
            let baseJSON: JSONValue = .object(["source": .string(base.source), "guid": .int(Int(base.guid)),
                                               "group": .int(Int(base.group)), "name": .string(base.name)])
            _ = try await c.call("project_new", ["path": .string(url.path), "base": baseJSON,
                                                 "identity": identity.json], as: PackageSummary.self, timeout: 120)
            return true
        } catch {
            if !(error is CancellationError) { errorMessage = describe(error) }
            return false
        }
    }

    public func close() {
        client?.shutdown()
        client = nil
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
