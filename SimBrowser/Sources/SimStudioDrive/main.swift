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
    log("OK")
    exit(0)
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
