import Foundation
import SQLite3

// MARK: - Read-ONLY ledger reader
//
// The frozen engine owns `~/Iga/state/proactive.db` (env IGA_PROACTIVE_DB),
// table `job_runs(idempotency_key, job_id, last_run_ts, status, output_ref,
// cooldown_until)`. This app NEVER writes it. We open with the SQLite URI
// `?mode=ro` flag AND SQLITE_OPEN_READONLY so any accidental write attempt
// fails at the driver level — a structural guard for the hard contract.
//
// Robustness: a missing, locked, or partial db never throws to the caller;
// it returns an empty/`unavailable` result so the UI degrades gracefully.

private let SQLITE_TRANSIENT = unsafeBitCast(
    -1, to: sqlite3_destructor_type.self)

struct LedgerRow: Identifiable, Equatable {
    let idempotencyKey: String
    let jobId: String
    let lastRunTs: String
    let status: String
    let outputRef: String?
    let cooldownUntil: String

    var id: String { idempotencyKey }
}

struct LedgerSnapshot: Equatable {
    var rows: [LedgerRow] = []
    /// Live tally by status (claimed→queued, running, done) — same mapping
    /// surfacer._counts uses, so app-derived counts match engine-derived ones.
    var queued: Int = 0
    var running: Int = 0
    var done: Int = 0
    /// True when the db file is absent or could not be opened read-only.
    var unavailable: Bool = false
    var note: String?

    static let empty = LedgerSnapshot()
}

enum LedgerReader {

    /// `$IGA_PROACTIVE_DB` or `~/Iga/state/proactive.db`.
    static func defaultDBPath() -> String {
        if let env = ProcessInfo.processInfo.environment["IGA_PROACTIVE_DB"],
           !env.isEmpty {
            return (env as NSString).expandingTildeInPath
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/state/proactive.db"
    }

    /// Open the ledger strictly read-only and snapshot it. Never throws.
    static func snapshot(dbPath: String? = nil) -> LedgerSnapshot {
        let path = dbPath ?? defaultDBPath()
        var snap = LedgerSnapshot()

        guard FileManager.default.fileExists(atPath: path) else {
            snap.unavailable = true
            snap.note = "engine not run yet (no ledger db)"
            return snap
        }

        // URI mode=ro + SQLITE_OPEN_READONLY: double guarantee no writes.
        let uri = "file:\(path)?mode=ro&immutable=0"
        var db: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_URI
        guard sqlite3_open_v2(uri, &db, flags, nil) == SQLITE_OK,
              let handle = db else {
            if let h = db { sqlite3_close(h) }
            snap.unavailable = true
            snap.note = "ledger locked or unreadable"
            return snap
        }
        defer { sqlite3_close(handle) }

        // Busy timeout so a concurrent engine write doesn't error us out.
        sqlite3_busy_timeout(handle, 1500)

        snap.rows = readRows(handle)
        let counts = readCounts(handle)
        snap.queued = counts.queued
        snap.running = counts.running
        snap.done = counts.done
        return snap
    }

    private static func readRows(_ handle: OpaquePointer) -> [LedgerRow] {
        let sql = """
        SELECT idempotency_key, job_id, last_run_ts, status,
               output_ref, cooldown_until
        FROM job_runs
        ORDER BY last_run_ts DESC
        LIMIT 100;
        """
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &stmt, nil) == SQLITE_OK
        else { return [] }
        defer { sqlite3_finalize(stmt) }

        var out: [LedgerRow] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            func col(_ i: Int32) -> String? {
                guard let c = sqlite3_column_text(stmt, i) else { return nil }
                return String(cString: c)
            }
            out.append(LedgerRow(
                idempotencyKey: col(0) ?? "?",
                jobId: col(1) ?? "?",
                lastRunTs: col(2) ?? "",
                status: col(3) ?? "?",
                outputRef: col(4),
                cooldownUntil: col(5) ?? ""
            ))
        }
        return out
    }

    private static func readCounts(_ handle: OpaquePointer)
        -> (queued: Int, running: Int, done: Int) {
        let sql =
            "SELECT status, COUNT(*) FROM job_runs GROUP BY status;"
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &stmt, nil) == SQLITE_OK
        else { return (0, 0, 0) }
        defer { sqlite3_finalize(stmt) }

        var queued = 0, running = 0, done = 0
        while sqlite3_step(stmt) == SQLITE_ROW {
            guard let s = sqlite3_column_text(stmt, 0) else { continue }
            let status = String(cString: s)
            let n = Int(sqlite3_column_int(stmt, 1))
            switch status {
            case "claimed": queued += n
            case "running": running += n
            case "done": done += n
            default: break
            }
        }
        return (queued, running, done)
    }
}
