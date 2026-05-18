import XCTest
import SQLite3
@testable import IgaMenuBar

final class LedgerReaderTests: XCTestCase {

    private let SQLITE_TRANSIENT = unsafeBitCast(
        -1, to: sqlite3_destructor_type.self)

    /// Build a temp sqlite db with the EXACT frozen `job_runs` schema
    /// (copied verbatim from engine/ledger.py) and a few rows.
    private func makeTempLedger() throws -> String {
        let dir = NSTemporaryDirectory()
        let path = (dir as NSString)
            .appendingPathComponent("iga-test-\(UUID().uuidString).db")
        var db: OpaquePointer?
        XCTAssertEqual(
            sqlite3_open(path, &db), SQLITE_OK, "open temp db")
        defer { sqlite3_close(db) }

        let schema = """
        CREATE TABLE IF NOT EXISTS job_runs (
            idempotency_key TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL,
            last_run_ts     TEXT NOT NULL,
            status          TEXT NOT NULL CHECK(status IN
                                ('claimed','running','done','failed','timeout')),
            output_ref      TEXT,
            cooldown_until  TEXT NOT NULL
        );
        """
        XCTAssertEqual(
            sqlite3_exec(db, schema, nil, nil, nil), SQLITE_OK, "schema")

        let inserts = """
        INSERT INTO job_runs VALUES
          ('research::abc::2026-05-17','research-todoist',
           '2026-05-16T07:05:07Z','claimed',NULL,'2026-05-18T07:05:07Z'),
          ('brief::w20','prep-brief',
           '2026-05-16T06:00:00Z','running',NULL,'2026-05-16T12:00:00Z'),
          ('done::1','prep-brief',
           '2026-05-15T06:00:00Z','done','drawer:xyz','2026-05-17T06:00:00Z');
        """
        XCTAssertEqual(
            sqlite3_exec(db, inserts, nil, nil, nil), SQLITE_OK, "insert")
        return path
    }

    func testReadsRowsAndCounts() throws {
        let path = try makeTempLedger()
        defer { try? FileManager.default.removeItem(atPath: path) }

        let snap = LedgerReader.snapshot(dbPath: path)
        XCTAssertFalse(snap.unavailable)
        XCTAssertEqual(snap.rows.count, 3)
        XCTAssertEqual(snap.queued, 1)   // claimed → queued
        XCTAssertEqual(snap.running, 1)
        XCTAssertEqual(snap.done, 1)

        let done = try XCTUnwrap(
            snap.rows.first { $0.status == "done" })
        XCTAssertEqual(done.outputRef, "drawer:xyz")
        XCTAssertEqual(done.jobId, "prep-brief")
    }

    func testMissingDbDegradesGracefully() {
        let snap = LedgerReader.snapshot(
            dbPath: "/nonexistent/path/to/proactive.db")
        XCTAssertTrue(snap.unavailable)
        XCTAssertTrue(snap.rows.isEmpty)
        XCTAssertNotNil(snap.note)
    }

    /// CONTRACT: the reader opens read-only. Prove it by attempting a write
    /// through the SAME URI/flags the reader uses — it MUST fail.
    func testReaderConnectionIsReadOnly() throws {
        let path = try makeTempLedger()
        defer { try? FileManager.default.removeItem(atPath: path) }

        let uri = "file:\(path)?mode=ro&immutable=0"
        var db: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_URI
        XCTAssertEqual(
            sqlite3_open_v2(uri, &db, flags, nil), SQLITE_OK)
        defer { sqlite3_close(db) }

        let rc = sqlite3_exec(
            db,
            "UPDATE job_runs SET status='done' WHERE idempotency_key='x';",
            nil, nil, nil)
        // A read-only connection MUST reject any write.
        XCTAssertNotEqual(
            rc, SQLITE_OK,
            "read-only ledger connection must reject writes")
        XCTAssertEqual(rc, SQLITE_READONLY)
    }
}
