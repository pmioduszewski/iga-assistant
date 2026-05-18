import XCTest
@testable import IgaMenuBar

final class EngineStateTests: XCTestCase {

    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(
            Bundle.module.url(
                forResource: name, withExtension: "json",
                subdirectory: "Fixtures"),
            "missing fixture \(name).json")
        return try Data(contentsOf: url)
    }

    func testDecodeEmptyLiveState() throws {
        let s = try EngineState.decode(from: fixture("state_empty"))
        XCTAssertEqual(s.schemaVersion, 1)
        XCTAssertNotNil(s.generatedAt)
        XCTAssertEqual(s.tick?.discoveredJobs, 2)
        XCTAssertEqual(s.tick?.firedCandidates, 1)
        XCTAssertEqual(s.tick?.claimSkipped, 1)
        XCTAssertEqual(s.tick?.errors.count, 2)
        XCTAssertTrue(s.queue.isEmpty)
        XCTAssertEqual(s.counts.queued, 0)
        XCTAssertEqual(s.governor.maxInvocations5h, 8)
        XCTAssertEqual(s.governor.maxEstTokens5h, 2_000_000)
        XCTAssertFalse(s.governor.breakerTripped)
    }

    func testDecodeQueuedStateAndWorkerRequest() throws {
        let s = try EngineState.decode(from: fixture("state_queued"))
        XCTAssertEqual(s.queue.count, 2)

        let r0 = s.queue[0]
        XCTAssertEqual(r0.jobId, "research-todoist")
        XCTAssertEqual(
            r0.idempotencyKey,
            "research::6gfGhpHQq888QWgm::2026-05-17T10:00:00")
        XCTAssertEqual(r0.triggerKind, "todoist")
        XCTAssertEqual(r0.actionName, "spawn_worker")
        XCTAssertEqual(r0.model, "opus")
        XCTAssertEqual(r0.estTokens, 120_000)
        XCTAssertEqual(r0.deliver, "surface_next_brief")
        XCTAssertEqual(r0.id, r0.idempotencyKey) // identity == idem key
        XCTAssertTrue(r0.shortKey.contains("…"))

        let r1 = s.queue[1]
        XCTAssertNil(r1.promptPath)
        XCTAssertEqual(r1.model, "sonnet")

        XCTAssertEqual(s.counts.done, 5)
        XCTAssertTrue(s.tick?.queueAlert ?? false)

        // Breaker tripped: invocations_5h == max (>= condition).
        XCTAssertTrue(s.governor.breakerTripped)
        XCTAssertEqual(s.governor.breakerLabel, "TRIPPED")
    }

    func testDecodeSurfaceOnlyRefresh() throws {
        // surfacer.refresh_state writes NO tick/queue — must still decode.
        let s = try EngineState.decode(from: fixture("state_surface"))
        XCTAssertNil(s.tick)
        XCTAssertTrue(s.queue.isEmpty)
        XCTAssertEqual(s.surface?.lines.count, 2)
        XCTAssertEqual(s.surface?.overflow, "+1 more")
        XCTAssertEqual(s.counts.done, 7)
        XCTAssertFalse(s.governor.hasBudget)
        XCTAssertFalse(s.governor.breakerTripped)
    }

    func testPartialAndGarbageNeverCrash() throws {
        // Empty object → all defaults, no throw beyond decode.
        let empty = try EngineState.decode(from: Data("{}".utf8))
        XCTAssertEqual(empty.schemaVersion, 0)
        XCTAssertTrue(empty.queue.isEmpty)

        // Half-written / wrong-typed fields tolerated by lenient decoders.
        let weird = #"{"schema_version":"x","queue":"nope","counts":5}"#
        let w = try EngineState.decode(from: Data(weird.utf8))
        XCTAssertEqual(w.schemaVersion, 0)
        XCTAssertTrue(w.queue.isEmpty)
        XCTAssertEqual(w.counts.queued, 0)
    }

    func testBreakerMirrorsGovernorAllowSemantics() {
        var g = GovernorSnapshot()
        g.maxInvocations5h = 8
        g.maxInvocations24h = 20
        g.maxEstTokens5h = 2_000_000

        g.invocations5h = 7
        XCTAssertFalse(g.breakerTripped)        // 7 < 8
        g.invocations5h = 8
        XCTAssertTrue(g.breakerTripped)         // 8 >= 8 (engine uses >=)

        g.invocations5h = 0
        g.estTokens5h = 2_000_000
        XCTAssertTrue(g.breakerTripped)         // token ceiling saturated
    }
}
