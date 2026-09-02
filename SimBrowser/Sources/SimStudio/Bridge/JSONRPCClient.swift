import Foundation

/// The daemon's error object, as it comes over the wire.
struct RPCErrorBody: Decodable {
    let code: String
    let message: String
    let data: JSONValue?
}

enum RPCFailure: Error, LocalizedError {
    case remote(RPCErrorBody)
    case transport(String)

    var errorDescription: String? {
        switch self {
        case .remote(let e): return e.message
        case .transport(let s): return s
        }
    }

    var code: String {
        switch self {
        case .remote(let e): return e.code
        case .transport: return "transport"
        }
    }
}

/// One `python3 s2studio.py --serve` process and the newline-delimited
/// JSON-RPC conversation with it. Requests are matched to responses by id;
/// server events (no id) go to `onEvent`. Every response line is handed
/// back as raw `Data` and decoded by the typed caller, so the reader never
/// has to know what a method returns.
final class JSONRPCClient: @unchecked Sendable {   // all mutable state is confined to `queue`
    private let process = Process()
    private let inPipe = Pipe()
    private let outPipe = Pipe()
    private let errPipe = Pipe()
    private let queue = DispatchQueue(label: "org.macadmins.simstudio.rpc")
    private var buffer = Data()
    private var nextID = 0
    private var pending: [Int: (Result<Data, Error>) -> Void] = [:]
    private var closed = false

    /// The last few KB of the daemon's stderr, for error messages.
    private(set) var stderrTail = ""
    var onEvent: ((JSONValue) -> Void)?

    let python: String
    let script: String

    init(python: String, script: String) throws {
        self.python = python
        self.script = script
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [script, "--serve"]
        // The daemon imports its siblings by bare name; Python adds the
        // script's own folder to sys.path, so no cwd games are needed, but
        // set it anyway so relative paths in tracebacks read sensibly.
        process.currentDirectoryURL = URL(fileURLWithPath: script).deletingLastPathComponent()
        process.standardInput = inPipe
        process.standardOutput = outPipe
        process.standardError = errPipe

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self else { return }
            if data.isEmpty {
                handle.readabilityHandler = nil
                self.queue.async { self.failAll("daemon closed its output") }
                return
            }
            self.queue.async { self.receive(data) }
        }
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard let self, !data.isEmpty else { handle.readabilityHandler = nil; return }
            self.queue.async {
                self.stderrTail += String(decoding: data, as: UTF8.self)
                if self.stderrTail.count > 4000 { self.stderrTail = String(self.stderrTail.suffix(4000)) }
                if Self.tracing { FileHandle.standardError.write(data) }
            }
        }
        process.terminationHandler = { [weak self] p in
            self?.queue.async {
                self?.failAll("daemon exited with status \(p.terminationStatus)")
            }
        }
        try process.run()
    }

    deinit {
        if process.isRunning { process.terminate() }
    }

    // MARK: Calls

    private struct Request: Encodable {
        let id: Int
        let method: String
        let params: [String: JSONValue]
    }

    private struct Envelope<T: Decodable>: Decodable {
        let id: Int?
        let result: T?
        let error: RPCErrorBody?
    }

    /// The daemon writes `{"id": N, ...}` or `{"event": "...", ...}` with
    /// that key first, so the id can be read off the first few bytes rather
    /// than by parsing a reply that may be megabytes long.
    private enum Head {
        case response(Int)
        case event
        case other
    }

    private static func head(of line: Data) -> Head {
        let idPrefix = Array("{\"id\": ".utf8)
        let eventPrefix = Array("{\"event\"".utf8)
        if line.starts(with: eventPrefix) { return .event }
        guard line.starts(with: idPrefix) else { return .other }
        var value = 0
        var seen = false
        for byte in line.dropFirst(idPrefix.count).prefix(20) {
            guard byte >= 0x30 && byte <= 0x39 else { break }
            value = value * 10 + Int(byte - 0x30)
            seen = true
        }
        return seen ? .response(value) : .other
    }

    /// `SIMSTUDIO_TRACE=1` in the environment logs every call to stderr, the
    /// way GeometryProbe logs layout in Sim Browser — for driving the app from
    /// a shell where its window cannot be seen.
    static let tracing = ProcessInfo.processInfo.environment["SIMSTUDIO_TRACE"] != nil

    /// Send one request and decode its result. `timeout` is generous because
    /// a first BHAV lookup on objects.package inflates thousands of trees.
    func call<T: Decodable>(_ method: String, _ params: [String: JSONValue] = [:],
                            as type: T.Type = T.self, timeout: TimeInterval = 120) async throws -> T {
        let data = try await send(method, params, timeout: timeout)
        let env: Envelope<T>
        do {
            env = try JSONDecoder().decode(Envelope<T>.self, from: data)
        } catch {
            throw RPCFailure.transport("could not decode \(method) reply: \(error)")
        }
        if let err = env.error {
            if Self.tracing { FileHandle.standardError.write(Data("rpc \(method) error \(err.code): \(err.message)\n".utf8)) }
            throw RPCFailure.remote(err)
        }
        guard let result = env.result else {
            throw RPCFailure.transport("\(method) returned no result")
        }
        return result
    }

    /// Like `call`, but hands back the result as Foundation objects from
    /// JSONSerialization, which parses a multi-megabyte reply in tens of
    /// milliseconds where JSONDecoder takes seconds. For the index.
    func callRaw(_ method: String, _ params: [String: JSONValue] = [:],
                 timeout: TimeInterval = 120) async throws -> Any {
        let data = try await send(method, params, timeout: timeout)
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw RPCFailure.transport("could not parse \(method) reply")
        }
        if let err = obj["error"] as? [String: Any] {
            let body = RPCErrorBody(code: err["code"] as? String ?? "error",
                                    message: err["message"] as? String ?? "unknown error", data: nil)
            throw RPCFailure.remote(body)
        }
        guard let result = obj["result"] else {
            throw RPCFailure.transport("\(method) returned no result")
        }
        return result
    }

    private func send(_ method: String, _ params: [String: JSONValue], timeout: TimeInterval) async throws -> Data {
        let started = Date()
        defer {
            if Self.tracing {
                FileHandle.standardError.write(Data(String(format: "rpc %@ %.0f ms\n", method,
                                                           Date().timeIntervalSince(started) * 1000).utf8))
            }
        }
        return try await withCheckedThrowingContinuation { cont in
            queue.async {
                if self.closed {
                    cont.resume(throwing: RPCFailure.transport("daemon is not running"))
                    return
                }
                self.nextID += 1
                let id = self.nextID
                self.pending[id] = { cont.resume(with: $0) }
                do {
                    var line = try JSONEncoder().encode(Request(id: id, method: method, params: params))
                    line.append(0x0A)
                    try self.inPipe.fileHandleForWriting.write(contentsOf: line)
                } catch {
                    self.pending[id] = nil
                    cont.resume(throwing: RPCFailure.transport("could not write to daemon: \(error.localizedDescription)"))
                    return
                }
                self.queue.asyncAfter(deadline: .now() + timeout) {
                    if let cb = self.pending.removeValue(forKey: id) {
                        cb(.failure(RPCFailure.transport("\(method) timed out after \(Int(timeout))s")))
                    }
                }
            }
        }
    }

    /// Ask the daemon to exit, then make sure it does.
    func shutdown() {
        queue.async {
            guard !self.closed else { return }
            let line = Data("{\"id\": 0, \"method\": \"shutdown\", \"params\": {}}\n".utf8)
            try? self.inPipe.fileHandleForWriting.write(contentsOf: line)
            try? self.inPipe.fileHandleForWriting.close()
            self.closed = true
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [process] in
            if process.isRunning { process.terminate() }
        }
    }

    // MARK: Reading

    private func receive(_ data: Data) {
        buffer.append(data)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            guard !line.isEmpty else { continue }
            switch Self.head(of: line) {
            case .response(let id):
                if let cb = pending.removeValue(forKey: id) { cb(.success(line)) }
            case .event:
                if let value = try? JSONDecoder().decode(JSONValue.self, from: line) { onEvent?(value) }
            case .other:
                continue
            }
        }
    }

    private func failAll(_ reason: String) {
        closed = true
        let tail = stderrTail.trimmingCharacters(in: .whitespacesAndNewlines)
        let message = tail.isEmpty ? reason : "\(reason). \(tail.suffix(600))"
        let callbacks = pending
        pending.removeAll()
        for (_, cb) in callbacks { cb(.failure(RPCFailure.transport(message))) }
    }
}
