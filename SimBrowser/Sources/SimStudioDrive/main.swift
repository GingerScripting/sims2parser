import Foundation
import SimStudioCore

// Walks the editing flow the way the views do — through `PackageSession`,
// which launches the real `s2studio.py` daemon — on a scratch copy of a
// donor from `sample-packages/`. This is the layer every editor button
// calls, including the Swift-side JSON encoding of a decoded edit, which the
// Python smoke test cannot exercise. It is not a click-through of the views.
//
//     cd SimBrowser && swift run SimStudioDrive            # scratch copy of the Diploma donor
//     cd SimBrowser && swift run SimStudioDrive file.package   # a package of yours; it WILL be saved
//
// Exits 0 on success, 1 on the first failure, 2 when there is nothing to
// drive (no sample-packages/). A separate executable rather than a test
// target: XCTest and Swift Testing both need Xcode, and the README asks only
// for the Command Line Tools.

/// The repo root, found from this file so the drive works in any checkout.
let repoRoot = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent()      // SimStudioDrive
    .deletingLastPathComponent()      // Sources
    .deletingLastPathComponent()      // SimBrowser
    .deletingLastPathComponent()      // repo
setenv("S2_TOOLKIT_DIR", repoRoot.path, 1)

func log(_ s: String) { print("drive: \(s)") }
func fail(_ s: String) -> Never { log("FAIL \(s)"); exit(1) }

/// Poll on the main actor until `ok`, up to `ticks` × 100 ms, else fail.
@MainActor
func settle(_ what: String, ticks: Int = 50, until ok: () -> Bool) async {
    for _ in 0..<ticks where !ok() {
        try? await Task.sleep(nanoseconds: 100_000_000)
    }
    guard ok() else { fail("timed out waiting for \(what)") }
}

@MainActor
func open(_ url: URL) async -> PackageSession {
    let session = PackageSession(url: url)
    await session.start()
    guard case .ready = session.phase else { fail("could not open \(url.lastPathComponent): \(session.phase)") }
    return session
}

@MainActor
func firstString(_ s: PackageSession) -> String? {
    s.detail?.decoded?["entries"]?[0]?["value"]?.stringValue
}

@MainActor
func drive(_ scratch: URL) async {
    let session = await open(scratch)
    log("opened \(session.title): \(session.rows.count) rows, readonly=\(session.isReadonly)")
    guard !session.isReadonly else { fail("the file is read-only; drive a scratch copy") }
    // Names arrive after the index; every BHAV has one.
    let named = session.rows.filter { $0.name != nil }.count
    guard session.rows.contains(where: { $0.bhav && $0.name != nil }) else { fail("no BHAV row carries a name") }
    log("\(named) of \(session.rows.count) rows named, e.g. '\(session.rows.first { $0.bhav }?.name ?? "")'")
    await session.loadOverview()
    guard let ov = session.overview, ov.kind == "object", let obj = ov.objects.first else {
        fail("overview: \(session.overview.map { $0.kind } ?? session.errorMessage ?? "nil")")
    }
    log("overview: \(ov.headline) (\(obj.interactions.count) pie-menu entries)")

    // 1. A STR# row that decodes with at least one entry.
    var picked: (ResourceRow, JSONValue)?
    for candidate in session.rows.filter({ $0.decodable && $0.typeName == "STR#" }).prefix(8) {
        session.selectedTGIs = [candidate.tgi]
        await settle("detail \(candidate.tgi)") { session.detail?.tgi == candidate.tgi }
        if let d = session.detail, d.tgi == candidate.tgi, let dec = d.decoded,
           (dec["entries"]?.arrayValue?.count ?? 0) > 0 {
            picked = (candidate, dec)
            break
        }
        log("skipping \(candidate.tgi): \(session.detail?.decodeError ?? session.errorMessage ?? "no decoded form")")
    }
    guard let (row, decoded) = picked else { fail("no STR# with entries decoded") }
    var draft = decoded
    log("selected STR# \(row.tgi) '\(draft["name"]?.stringValue ?? "")' with \(draft["entries"]?.arrayValue?.count ?? 0) entries")

    // 2. Edit the first string the way StrEditor's binding does, and apply.
    //    A second run on the same file finds the last run's edit already
    //    there; a no-op put is (correctly) not an edit, so vary it.
    let original = draft["entries"]?[0]?["value"]?.stringValue ?? ""
    let edited = original == "Sim Studio was here" ? "Sim Studio was here again" : "Sim Studio was here"
    draft["entries"]?[0]?["value"] = .string(edited)
    guard await session.putDecoded(row.tgi, draft) else { fail("putDecoded: \(session.errorMessage ?? "?")") }
    guard session.isDirty, session.canUndo else { fail("session is not dirty after an edit") }
    log("applied edit: '\(original)' -> '\(edited)'; undo=\(session.undoLabel)")

    // 3. Undo and redo — the toolbar buttons — and wait for the pane.
    await session.undo()
    await settle("undo to show '\(original)'") { firstString(session) == original }
    await session.redo()
    await settle("redo to show '\(edited)'") { firstString(session) == edited }
    log("undo/redo OK")

    // 4. Add, rename, compress, delete, at instance ids the package does not use.
    let used = Set(session.rows.filter { $0.type == row.type && $0.group == 0xFFFFFFFF }.map(\.instance))
    let free = (0x7F00...0x7FFF).filter { !used.contains(UInt32($0)) }.prefix(2).map(UInt32.init)
    guard free.count == 2 else { fail("no free instance ids in the private group") }
    let fresh = TGI(type: row.type, group: 0xFFFFFFFF, instance: free[0], instanceHi: 0)
    let moved = TGI(type: row.type, group: 0xFFFFFFFF, instance: free[1], instanceHi: 0)
    guard await session.addResource(fresh, bytes: [UInt8](repeating: 0, count: 68)) else { fail("addResource") }
    guard await session.rename(fresh, to: moved) else { fail("rename") }
    guard await session.setCompressed([moved], true) else { fail("setCompressed") }
    guard await session.delete([moved]) else { fail("delete") }
    guard !session.rows.contains(where: { $0.tgi == moved }) else { fail("deleted row still listed") }
    log("add/rename/compress/delete OK; rows=\(session.rows.count) undo=\(session.undoLabel)")

    // 5. Save, then a second session re-reads the file.
    guard await session.save() else { fail("save: \(session.errorMessage ?? "?")") }
    log("saved; dirty=\(session.isDirty)")
    let check = await open(scratch)
    check.selectedTGIs = [row.tgi]
    await settle("re-read detail") { check.detail?.tgi == row.tgi }
    guard firstString(check) == edited else { fail("re-read string is \(firstString(check) ?? "nil"), expected '\(edited)'") }
    check.close()
    log("re-opened \(check.title): edit survived on disk")

    // 6. A BHAV, if there is one: fetch, transform, apply, undo.
    if let b = session.rows.first(where: { $0.bhav }) {
        session.selectedTGIs = [b.tgi]
        await settle("BHAV detail") { session.detail?.tgi == b.tgi }
        await session.loadBhavMeta()
        guard let bd = session.detail?.decoded, session.bhavMeta != nil else { fail("BHAV did not decode") }
        let n = bd["instructions"]?.arrayValue?.count ?? 0
        guard let t = await session.bhavTransform(bd, op: "insert", index: 0) else { fail("bhav_transform") }
        guard t.decoded["instructions"]?.arrayValue?.count == n + 1 else { fail("insert did not add an instruction") }
        guard await session.putDecoded(b.tgi, t.decoded) else { fail("BHAV apply") }
        await session.undo()
        await settle("undo to restore \(n) instructions") {
            session.detail?.decoded?["instructions"]?.arrayValue?.count == n
        }
        log("BHAV '\(bd["name"]?.stringValue ?? "")': \(n) instructions, insert/apply/undo OK")
    }

    // 7. Deselecting clears the pane and the spinner.
    session.selectedTGIs = []
    guard session.detail == nil, !session.detailLoading else { fail("deselect left the pane populated") }

    session.close()
    await driveProject(scratch.deletingLastPathComponent())
    log("OK")
    exit(0)
}

/// The object maker: catalog a scratch root, make a project from the
/// Diploma in its Downloads, edit the identity through undo, export,
/// install into the scratch root, and re-open both the project and the
/// export.
@MainActor
func driveProject(_ dir: URL) async {
    let root = dir.appendingPathComponent("root")
    let downloads = root.appendingPathComponent("Downloads")
    try! FileManager.default.createDirectory(at: downloads, withIntermediateDirectories: true)
    try! FileManager.default.createDirectory(at: root.appendingPathComponent("Neighborhoods"), withIntermediateDirectories: true)
    let donor = repoRoot.appendingPathComponent("sample-packages/Christianlov_CounterfeitCollegeDiploma.package")
    try! FileManager.default.copyItem(at: donor, to: downloads.appendingPathComponent(donor.lastPathComponent))
    CatalogService.gameRootOverride = root.path

    let catalog = CatalogService()
    await catalog.load(refresh: true)
    guard let base = catalog.entries.first(where: { $0.name.contains("Diploma") && !$0.isGame }) else {
        fail("the catalog did not list the Diploma from the scratch Downloads (\(catalog.entries.count) entries)")
    }
    guard let png = await catalog.swatch(for: base), png.count > 100 else { fail("no swatch for the Diploma") }
    let project = dir.appendingPathComponent("Drive Object.simobject")
    let identity = ProjectIdentity(name: "Drive Object", description: "made by the drive", price: 42, roomFlags: 2, functionFlags: 0x20)
    guard await catalog.createProject(at: project, base: base, identity: identity) else {
        fail("project_new: \(catalog.errorMessage ?? "?")")
    }
    catalog.close()
    log("project created from \(base.name)")

    let s = await open(project)
    guard s.isProject, let info = s.project, info.identity.price == 42, !s.isReadonly else { fail("project did not open as a project") }
    guard s.rows.count > 10, s.title == "Drive Object" else { fail("project window state: \(s.rows.count) rows, title \(s.title)") }
    var want = info.identity
    want.price = 77
    guard await s.projectSet(want), s.project?.identity.price == 77, s.undoLabel == "Undo Reprice" else { fail("project_set: \(s.undoLabel)") }
    await s.undo()
    guard s.project?.identity.price == 42 else { fail("undo did not restore the price") }
    await s.redo()
    guard s.project?.identity.price == 77 else { fail("redo did not reapply the price") }
    guard await s.projectSave(), !s.isDirty else { fail("project_save") }
    let exported = dir.appendingPathComponent("Drive Object.package")
    guard let file = await s.projectExport(to: exported), FileManager.default.fileExists(atPath: file.path) else { fail("project_export") }
    guard case .success(let installed)? = await s.projectInstall(), installed.path.hasPrefix(downloads.path) else { fail("project_install") }
    guard case .failure(let f)? = await s.projectInstall(), case .remote(let e) = f, e.code == "exists" else { fail("second install should say exists") }
    guard case .success? = await s.projectInstall(replace: true) else { fail("install with replace") }
    s.close()
    log("project: set/undo/redo, saved, exported \(file.lastPathComponent), installed into the scratch root")

    let again = await open(project)
    guard again.project?.identity.price == 77 else { fail("re-opened project lost the price") }
    again.close()
    let pkg = await open(exported)
    guard !pkg.isProject, pkg.rows.allSatisfy({ $0.group == 0xFFFFFFFF }) else { fail("export is not a clean private-group package") }
    let objs = await pkg.objects()
    guard objs.count == 1, objs[0].guid == info.identity.guid, objs[0].price == 77 else { fail("export objects: \(objs)") }
    pkg.close()
    log("re-opened the project and the export: identity intact")
}

// A package of the user's, or a scratch copy of the Diploma donor.
let target: URL
if CommandLine.arguments.count > 1 {
    target = URL(fileURLWithPath: CommandLine.arguments[1])
} else {
    let donor = repoRoot.appendingPathComponent("sample-packages/Christianlov_CounterfeitCollegeDiploma.package")
    guard FileManager.default.fileExists(atPath: donor.path) else {
        log("skip: sample-packages/ is not present in this checkout")
        exit(2)
    }
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("simstudio-drive-\(UUID().uuidString)")
    try! FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    target = dir.appendingPathComponent("Diploma.package")
    try! FileManager.default.copyItem(at: donor, to: target)
    log("scratch copy at \(target.path)")
}

Task { @MainActor in await drive(target) }
RunLoop.main.run()
