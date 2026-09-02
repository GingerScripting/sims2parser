import Foundation

/// `SIMSTUDIO_SELFDRIVE=1` with `SIMSTUDIO_OPEN=<file>`: once the package
/// is open, walk the editing flow the way the views do — select a string
/// table, change a string, apply, save, re-read — logging each step to
/// stderr, then quit with 0 on success or 1 on the first failure.
///
/// This runs the same `PackageSession` calls the editors make, including the
/// Swift-side JSON encoding of a decoded edit, without a screen or a
/// pointer. It is for driving the app from a shell where its window cannot
/// be seen; it is not a substitute for clicking through the views.
enum SelfDrive {
    static let enabled = ProcessInfo.processInfo.environment["SIMSTUDIO_SELFDRIVE"] != nil

    /// Only the window for the file named in SIMSTUDIO_OPEN drives; SwiftUI
    /// may restore other windows from the previous run alongside it.
    @MainActor
    static func applies(to session: PackageSession) -> Bool {
        guard enabled, let target = ProcessInfo.processInfo.environment["SIMSTUDIO_OPEN"] else { return false }
        return URL(fileURLWithPath: target).standardizedFileURL == session.url.standardizedFileURL
    }

    @MainActor private static var launched = false

    /// Start the drive once, detached from the view's task lifetime.
    @MainActor
    static func launch(_ session: PackageSession) {
        guard !launched else { return }
        launched = true
        Task { @MainActor in await run(session) }
    }

    private static func log(_ s: String) {
        FileHandle.standardError.write(Data("selfdrive: \(s)\n".utf8))
    }

    private static func fail(_ s: String) -> Never {
        log("FAIL \(s)")
        exit(1)
    }

    @MainActor
    static func run(_ session: PackageSession) async {
        guard session.phase == .ready else { fail("package did not open: \(session.phase)") }
        log("opened \(session.title): \(session.rows.count) rows, readonly=\(session.isReadonly)")

        // 1. A STR# row that decodes with at least one entry.
        var picked: (ResourceRow, JSONValue)?
        var waited = 0
        for candidate in session.rows.filter({ $0.decodable && $0.typeName == "STR#" }).prefix(8) {
            session.selectedTGIs = [candidate.tgi]
            waited = 0
            while session.detail?.tgi != candidate.tgi && waited < 50 {
                try? await Task.sleep(nanoseconds: 100_000_000)
                waited += 1
            }
            if let d = session.detail, d.tgi == candidate.tgi, let dec = d.decoded,
               (dec["entries"]?.arrayValue?.count ?? 0) > 0 {
                picked = (candidate, dec)
                break
            }
            log("skipping \(candidate.tgi): detail=\(session.detail?.tgi.description ?? "nil") "
                + "decodeError=\(session.detail?.decodeError ?? "-") error=\(session.errorMessage ?? "-")")
        }
        guard let (row, decoded) = picked else { fail("no STR# with entries decoded") }
        var draft = decoded
        let count = draft["entries"]?.arrayValue?.count ?? 0
        log("selected STR# \(row.tgi) '\(draft["name"]?.stringValue ?? "")' with \(count) entries")
        guard count > 0 else { fail("string table is empty") }

        // 2. Edit the first string the way StrEditor's binding does, and apply.
        let original = draft["entries"]?[0]?["value"]?.stringValue ?? ""
        let edited = "Sim Studio was here"
        draft["entries"]?[0]?["value"] = .string(edited)
        guard await session.putDecoded(row.tgi, draft) else { fail("putDecoded failed: \(session.errorMessage ?? "?")") }
        log("applied edit: '\(original)' -> '\(edited)'; dirty=\(session.isDirty) undo=\(session.undoLabel)")
        guard session.isDirty, session.canUndo else { fail("session is not dirty after an edit") }

        // 3. Undo, redo — the toolbar buttons. The detail pane refreshes
        //    asynchronously, so wait for it the way a user's eyes would.
        func firstString() -> String? { session.detail?.decoded?["entries"]?[0]?["value"]?.stringValue }
        func waitFor(_ expected: String, _ what: String) async {
            var n = 0
            while firstString() != expected && n < 30 {
                try? await Task.sleep(nanoseconds: 100_000_000)
                n += 1
            }
            guard firstString() == expected else {
                fail("\(what): detail shows \(firstString() ?? "nil"), expected '\(expected)' (\(session.errorMessage ?? "no error"))")
            }
        }
        await session.undo()
        log("after undo: canUndo=\(session.canUndo) canRedo=\(session.canRedo) redo='\(session.redoLabel)'")
        await waitFor(original, "undo")
        await session.redo()
        log("after redo: canUndo=\(session.canUndo) canRedo=\(session.canRedo) undo='\(session.undoLabel)' dirty=\(session.isDirty)")
        await waitFor(edited, "redo")
        log("undo/redo OK")

        // 4. Rename + compression toggle + new resource, then delete it.
        let fresh = TGI(type: row.type, group: 0xFFFFFFFF, instance: 0x7FF0, instanceHi: 0)
        guard await session.addResource(fresh, bytes: [UInt8](repeating: 0, count: 68)) else { fail("addResource") }
        let moved = TGI(type: row.type, group: 0xFFFFFFFF, instance: 0x7FF1, instanceHi: 0)
        guard await session.rename(fresh, to: moved) else { fail("rename") }
        guard await session.setCompressed([moved], true) else { fail("setCompressed") }
        guard await session.delete([moved]) else { fail("delete") }
        log("add/rename/compress/delete OK; rows=\(session.rows.count) undo=\(session.undoLabel)")

        // 5. Save (the file is a scratch copy, so this is allowed), then a
        //    second session re-reads it and checks the string survived.
        guard await session.save() else { fail("save failed: \(session.errorMessage ?? "?")") }
        log("saved; dirty=\(session.isDirty)")
        let check = PackageSession(url: session.currentURL)
        await check.start()
        guard check.phase == .ready else { fail("re-open failed: \(check.phase)") }
        check.selectedTGIs = [row.tgi]
        waited = 0
        while check.detail?.tgi != row.tgi && waited < 50 {
            try? await Task.sleep(nanoseconds: 100_000_000)
            waited += 1
        }
        let back = check.detail?.decoded?["entries"]?[0]?["value"]?.stringValue
        guard back == edited else {
            fail("re-read string is \(back ?? "nil"), expected '\(edited)'; detail=\(check.detail?.tgi.description ?? "nil") "
                 + "type=\(check.detail?.decoded?.typeName ?? "-") decodeError=\(check.detail?.decodeError ?? "-") "
                 + "error=\(check.errorMessage ?? "-") rows=\(check.rows.count)")
        }
        check.close()
        log("re-opened \(check.title): edit survived on disk")

        // 6. A BHAV, if there is one: fetch, transform, apply, undo.
        if let b = session.rows.first(where: { $0.bhav }) {
            session.selectedTGIs = [b.tgi]
            waited = 0
            while session.detail?.tgi != b.tgi && waited < 50 {
                try? await Task.sleep(nanoseconds: 100_000_000)
                waited += 1
            }
            await session.loadBhavMeta()
            guard let bd = session.detail?.decoded, session.bhavMeta != nil else { fail("BHAV did not decode") }
            let n = bd["instructions"]?.arrayValue?.count ?? 0
            guard let t = await session.bhavTransform(bd, op: "insert", index: 0) else { fail("bhav_transform") }
            guard t.decoded["instructions"]?.arrayValue?.count == n + 1 else { fail("insert did not add an instruction") }
            guard await session.putDecoded(b.tgi, t.decoded) else { fail("BHAV apply") }
            await session.undo()
            log("BHAV '\(bd["name"]?.stringValue ?? "")': \(n) instructions, insert/apply/undo OK")
        }

        log("OK")
        session.close()
        exit(0)
    }
}
