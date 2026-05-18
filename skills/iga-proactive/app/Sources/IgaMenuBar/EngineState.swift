import Foundation

// MARK: - Engine state contract (v1)
//
// This file is a pure DECODER for the frozen engine→entrypoint JSON contract
// emitted by `engine/dispatcher.py::build_state` and refreshed by
// `engine/surfacer.py::refresh_state`. The shapes here mirror those Python
// functions exactly. We never construct or write this document — read only.
//
// Schema v1 (dispatcher.build_state):
// {
//   schema_version, generated_at,
//   tick: { discovered_jobs, fired_candidates, condition_skipped,
//           claim_skipped, governor_denied, queue_alert, errors[] },
//   queue: [WORKER_REQUEST...],
//   counts: { queued, running, done },
//   governor: { invocations_5h, max_invocations_5h, invocations_24h,
//               max_invocations_24h, est_tokens_5h, max_est_tokens_5h },
//   surface?: { lines[], shown, total, overflow }   // surfacer.refresh_state
// }
//
// surfacer.refresh_state writes a SUBSET (no `tick`/`queue`). The decoder must
// tolerate either producer. Every field below is optional/defaulted so a
// partial or stale file never throws.

/// A single queued worker request the engine is about to dispatch.
/// Mirrors `dispatcher.to_worker_request`.
struct WorkerRequest: Codable, Identifiable, Equatable {
    let jobId: String
    let idempotencyKey: String
    let triggerKind: String?
    let action: String?
    let actionName: String?
    let promptPath: String?
    let model: String?
    let estTokens: Int?
    let deliver: String?

    // Identity is the idempotency key — this is also the de-dupe key for
    // notifications, matching the ledger's PRIMARY KEY.
    var id: String { idempotencyKey }

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case idempotencyKey = "idempotency_key"
        case triggerKind = "trigger_kind"
        case action
        case actionName = "action_name"
        case promptPath = "prompt_path"
        case model
        case estTokens = "est_tokens"
        case deliver
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobId = (try? c.decode(String.self, forKey: .jobId)) ?? "?"
        idempotencyKey =
            (try? c.decode(String.self, forKey: .idempotencyKey)) ?? "?"
        triggerKind = try? c.decode(String.self, forKey: .triggerKind)
        action = try? c.decode(String.self, forKey: .action)
        actionName = try? c.decode(String.self, forKey: .actionName)
        promptPath = try? c.decode(String.self, forKey: .promptPath)
        model = try? c.decode(String.self, forKey: .model)
        estTokens = try? c.decode(Int.self, forKey: .estTokens)
        deliver = try? c.decode(String.self, forKey: .deliver)
    }

    // Test/fixture convenience initializer.
    init(jobId: String, idempotencyKey: String, triggerKind: String? = nil,
         action: String? = nil, actionName: String? = nil,
         promptPath: String? = nil, model: String? = nil,
         estTokens: Int? = nil, deliver: String? = nil) {
        self.jobId = jobId
        self.idempotencyKey = idempotencyKey
        self.triggerKind = triggerKind
        self.action = action
        self.actionName = actionName
        self.promptPath = promptPath
        self.model = model
        self.estTokens = estTokens
        self.deliver = deliver
    }

    /// Short, stable idempotency-key label for dense UI rows.
    var shortKey: String {
        if idempotencyKey.count <= 22 { return idempotencyKey }
        let head = idempotencyKey.prefix(12)
        let tail = idempotencyKey.suffix(8)
        return "\(head)…\(tail)"
    }
}

/// Tick stats. Mirrors `runtime.TickResult` fields surfaced in `state["tick"]`.
struct TickStats: Codable, Equatable {
    var discoveredJobs: Int = 0
    var firedCandidates: Int = 0
    var conditionSkipped: Int = 0
    var claimSkipped: Int = 0
    var governorDenied: Int = 0
    var queueAlert: Bool = false
    var errors: [String] = []

    enum CodingKeys: String, CodingKey {
        case discoveredJobs = "discovered_jobs"
        case firedCandidates = "fired_candidates"
        case conditionSkipped = "condition_skipped"
        case claimSkipped = "claim_skipped"
        case governorDenied = "governor_denied"
        case queueAlert = "queue_alert"
        case errors
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        discoveredJobs = (try? c.decode(Int.self, forKey: .discoveredJobs)) ?? 0
        firedCandidates =
            (try? c.decode(Int.self, forKey: .firedCandidates)) ?? 0
        conditionSkipped =
            (try? c.decode(Int.self, forKey: .conditionSkipped)) ?? 0
        claimSkipped = (try? c.decode(Int.self, forKey: .claimSkipped)) ?? 0
        governorDenied =
            (try? c.decode(Int.self, forKey: .governorDenied)) ?? 0
        queueAlert = (try? c.decode(Bool.self, forKey: .queueAlert)) ?? false
        errors = (try? c.decode([String].self, forKey: .errors)) ?? []
    }
}

/// counts block.
struct Counts: Codable, Equatable {
    var queued: Int = 0
    var running: Int = 0
    var done: Int = 0

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        queued = (try? c.decode(Int.self, forKey: .queued)) ?? 0
        running = (try? c.decode(Int.self, forKey: .running)) ?? 0
        done = (try? c.decode(Int.self, forKey: .done)) ?? 0
    }

    enum CodingKeys: String, CodingKey { case queued, running, done }
}

/// Governor budget snapshot. Mirrors `governor.Governor.stats()`.
struct GovernorSnapshot: Codable, Equatable {
    var invocations5h: Int = 0
    var maxInvocations5h: Int = 0
    var invocations24h: Int = 0
    var maxInvocations24h: Int = 0
    var estTokens5h: Int = 0
    var maxEstTokens5h: Int = 0
    /// Set when `stats()` itself failed inside the engine (rare).
    var errorText: String?

    enum CodingKeys: String, CodingKey {
        case invocations5h = "invocations_5h"
        case maxInvocations5h = "max_invocations_5h"
        case invocations24h = "invocations_24h"
        case maxInvocations24h = "max_invocations_24h"
        case estTokens5h = "est_tokens_5h"
        case maxEstTokens5h = "max_est_tokens_5h"
        case errorText = "error"
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        invocations5h = (try? c.decode(Int.self, forKey: .invocations5h)) ?? 0
        maxInvocations5h =
            (try? c.decode(Int.self, forKey: .maxInvocations5h)) ?? 0
        invocations24h =
            (try? c.decode(Int.self, forKey: .invocations24h)) ?? 0
        maxInvocations24h =
            (try? c.decode(Int.self, forKey: .maxInvocations24h)) ?? 0
        estTokens5h = (try? c.decode(Int.self, forKey: .estTokens5h)) ?? 0
        maxEstTokens5h =
            (try? c.decode(Int.self, forKey: .maxEstTokens5h)) ?? 0
        errorText = try? c.decode(String.self, forKey: .errorText)
    }

    var hasBudget: Bool { maxInvocations5h > 0 || maxEstTokens5h > 0 }

    /// The windowed circuit breaker is "tripped" iff any ceiling is saturated.
    /// This mirrors `governor.allow`'s breaker condition exactly (>=, not >).
    var breakerTripped: Bool {
        guard hasBudget else { return false }
        if maxInvocations5h > 0 && invocations5h >= maxInvocations5h {
            return true
        }
        if maxInvocations24h > 0 && invocations24h >= maxInvocations24h {
            return true
        }
        if maxEstTokens5h > 0 && estTokens5h >= maxEstTokens5h { return true }
        return false
    }

    var breakerLabel: String { breakerTripped ? "TRIPPED" : "OK" }
}

/// Surface payload. Mirrors `surfacer.build_surface`.
struct SurfacePayload: Codable, Equatable {
    var lines: [String] = []
    var shown: Int = 0
    var total: Int = 0
    var overflow: String?

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        lines = (try? c.decode([String].self, forKey: .lines)) ?? []
        shown = (try? c.decode(Int.self, forKey: .shown)) ?? 0
        total = (try? c.decode(Int.self, forKey: .total)) ?? 0
        overflow = try? c.decode(String.self, forKey: .overflow)
    }

    enum CodingKeys: String, CodingKey {
        case lines, shown, total, overflow
    }
}

/// Top-level decoded engine state document (v1). Every section optional so a
/// surfacer-only refresh, a partial write, or a stale file decodes cleanly.
struct EngineState: Codable, Equatable {
    var schemaVersion: Int = 0
    var generatedAtRaw: String?
    var tick: TickStats?
    var queue: [WorkerRequest] = []
    var counts: Counts = Counts()
    var governor: GovernorSnapshot = GovernorSnapshot()
    var surface: SurfacePayload?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case generatedAtRaw = "generated_at"
        case tick, queue, counts, governor, surface
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion =
            (try? c.decode(Int.self, forKey: .schemaVersion)) ?? 0
        generatedAtRaw = try? c.decode(String.self, forKey: .generatedAtRaw)
        tick = try? c.decode(TickStats.self, forKey: .tick)
        queue = (try? c.decode([WorkerRequest].self, forKey: .queue)) ?? []
        counts = (try? c.decode(Counts.self, forKey: .counts)) ?? Counts()
        governor =
            (try? c.decode(GovernorSnapshot.self, forKey: .governor))
            ?? GovernorSnapshot()
        surface = try? c.decode(SurfacePayload.self, forKey: .surface)
    }

    /// Cached ISO-8601 parsers (fix #4). `generatedAt` is read on EVERY
    /// poll-decode (the 15s hot path) and previously allocated two
    /// `ISO8601DateFormatter` instances per call. They are immutable after
    /// configuration and ISO8601DateFormatter is thread-safe for parsing.
    private static let isoWithFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    /// Parse the ISO-8601 `generated_at` (Python `datetime.isoformat()`,
    /// e.g. `2026-05-16T07:32:55.550981+00:00`).
    var generatedAt: Date? {
        guard let raw = generatedAtRaw else { return nil }
        if let d = Self.isoWithFractional.date(from: raw) { return d }
        return Self.isoPlain.date(from: raw)
    }

    static func decode(from data: Data) throws -> EngineState {
        try JSONDecoder().decode(EngineState.self, from: data)
    }
}
