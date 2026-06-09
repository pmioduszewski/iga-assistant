import Foundation
import Observation

// MARK: - State store / poller
//
// Single source of truth for the UI. Polls (a) the engine JSON state file and
// (b) the read-only ledger on a timer (default 15s, configurable). Robust to
// missing / partial / locked files: it degrades to a `health` enum, never
// crashes, never throws to the UI.
//
// It also DIFFS successive snapshots to drive notifications (new
// WORKER_REQUEST, breaker trip, counts.done increment), de-duped by
// idempotency key so the same item never re-notifies.

enum EngineHealth: Equatable {
    case healthy            // fresh state file decoded
    case stale(String)      // file exists but old / partial — message
    case notRunYet          // no state file at all
    case error(String)      // decode/io error

    var label: String {
        switch self {
        case .healthy: return "Healthy"
        case .stale(let m): return "Stale — \(m)"
        case .notRunYet: return "Engine not run yet"
        case .error(let m): return "Error — \(m)"
        }
    }
}

@MainActor
@Observable
final class StateStore {

    private(set) var state = EngineState()
    private(set) var ledger = LedgerSnapshot.empty
    private(set) var health: EngineHealth = .notRunYet
    private(set) var lastPolled: Date?
    private(set) var lastScanResult: EngineRunResult?
    private(set) var scanInProgress = false

    /// Poll cadence; configurable via `IGA_POLL_SECONDS` env (default 15s).
    @ObservationIgnored
    let pollInterval: TimeInterval

    @ObservationIgnored
    private var timer: Timer?
    @ObservationIgnored
    private let notifier: Notifier
    @ObservationIgnored
    private var seenWorkerKeys: Set<String> = []
    @ObservationIgnored
    private var lastBreakerTripped = false
    @ObservationIgnored
    private var lastDoneCount: Int?
    @ObservationIgnored
    private var didPrime = false

    init(notifier: Notifier = Notifier.shared) {
        self.notifier = notifier
        if let s = ProcessInfo.processInfo.environment["IGA_POLL_SECONDS"],
           let v = TimeInterval(s), v >= 2 {
            self.pollInterval = v
        } else {
            self.pollInterval = 15
        }
    }

    /// `$IGA_PROACTIVE_STATE` or `~/Iga/scratch/proactive-state.json`.
    static func defaultStatePath() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_PROACTIVE_STATE"], !env.isEmpty {
            return (env as NSString).expandingTildeInPath
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/scratch/proactive-state.json"
    }

    /// `$IGA_PROACTIVE_CANCEL` or `~/Iga/scratch/proactive-cancel.json`
    /// (sibling of the state file). The engine drains this at the start of
    /// every scan tick and marks each key sticky-cancelled in the ledger.
    static func defaultCancelPath() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_PROACTIVE_CANCEL"], !env.isEmpty {
            return (env as NSString).expandingTildeInPath
        }
        // Derive from the state file's directory so the "sibling of the
        // state file" guarantee holds even when $IGA_PROACTIVE_STATE points
        // elsewhere (matches engine dispatcher.default_cancel_path()).
        let stateDir = (Self.defaultStatePath() as NSString)
            .deletingLastPathComponent
        return (stateDir as NSString)
            .appendingPathComponent("proactive-cancel.json")
    }

    /// User clicked Cancel on a lined-up job. Append its idempotency key to
    /// the cancel-request file (the engine turns it into a sticky-terminal
    /// ledger row on the next scan) and optimistically drop it from the
    /// visible queue so the UI reacts immediately, not at next reconcile.
    /// Returns true iff the cancel request was durably written. The
    /// optimistic UI removal happens ONLY on a confirmed write — otherwise
    /// the row stays visible, because a silent removal would falsely tell
    /// the user the job is cancelled while it would still run.
    @discardableResult
    func cancelQueued(_ request: WorkerRequest) -> Bool {
        let key = request.idempotencyKey
        guard !key.isEmpty, key != "?" else { return false }

        let path = Self.defaultCancelPath()
        let url = URL(fileURLWithPath: path)
        var keys: [String] = []
        if let data = try? Data(contentsOf: url),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let existing = obj["cancel"] as? [String] {
            keys = existing
        }
        if !keys.contains(key) { keys.append(key) }

        // Persist FIRST; only mutate the UI if the write provably succeeded.
        do {
            let out = try JSONSerialization.data(
                withJSONObject: ["cancel": keys], options: [.prettyPrinted])
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true)
            try out.write(to: url, options: .atomic)
        } catch {
            // Write failed → do NOT pretend it's cancelled. Leave the row;
            // the user can retry. (No silent success.)
            return false
        }

        // Confirmed persisted. Optimistic UI: remove now; the engine
        // reconciles the ledger on the next /gm·/back scan. Re-adding is
        // impossible (sticky cancel).
        state.queue.removeAll { $0.idempotencyKey == key }
        if state.counts.queued > 0 { state.counts.queued -= 1 }
        seenWorkerKeys.insert(key)  // never re-notify a cancelled key
        return true
    }

    func start() {
        poll()
        let t = Timer(timeInterval: pollInterval, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.poll() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    // MARK: poll

    func poll() {
        let path = Self.defaultStatePath()
        lastPolled = Date()

        // ---- ledger (read-only) — independent of the JSON file ----
        // Change-gated: only publish when the snapshot actually differs.
        // The poll runs every `pollInterval` (default 15s) but the ledger
        // rarely changes between ticks; an unconditional assign re-published
        // an identical value every tick, invalidating every SwiftUI consumer
        // (the 15s full-invalidation storm). `LedgerSnapshot` is Equatable.
        let freshLedger = LedgerReader.snapshot()
        if freshLedger != ledger {
            ledger = freshLedger
        }

        // ---- engine state JSON ----
        guard FileManager.default.fileExists(atPath: path) else {
            health = ledger.unavailable ? .notRunYet
                : .stale("ledger present but no state file")
            return
        }
        guard let data = try? Data(
            contentsOf: URL(fileURLWithPath: path)) else {
            health = .stale("state file unreadable")
            return
        }
        guard !data.isEmpty else {
            health = .stale("state file empty (engine mid-write?)")
            return
        }
        do {
            let decoded = try EngineState.decode(from: data)
            let prev = state
            // Change-gated publish: only reassign `state` when the freshly
            // decoded document actually differs from the last one. Without
            // this, every 15s poll re-published an identical `EngineState`,
            // invalidating every SwiftUI view that observes the store even
            // when nothing changed. Health is cheap and time-relative so it
            // still updates every tick; notification diffing still runs with
            // the decoded value (it is independently de-duped by idempotency
            // key, breaker edge, and done-count, so gating the publish does
            // not drop or duplicate any notification). `EngineState` and all
            // its sub-structs are Equatable.
            if decoded != prev {
                state = decoded
            }
            health = freshness(decoded)
            diffAndNotify(old: prev, new: decoded)
        } catch {
            // Partial / half-written file: keep last good state, mark stale.
            health = .stale("partial state (\(error.localizedDescription))")
        }
    }

    private func freshness(_ s: EngineState) -> EngineHealth {
        guard let gen = s.generatedAt else {
            return s.schemaVersion == 0
                ? .stale("no schema_version / generated_at")
                : .healthy
        }
        let age = Date().timeIntervalSince(gen)
        // > 6h old → flag stale (engine likely hasn't ticked since last wake).
        if age > 6 * 3600 {
            let h = Int(age / 3600)
            return .stale("state \(h)h old")
        }
        return .healthy
    }

    // MARK: notification diffing (de-duped by idempotency key)

    private func diffAndNotify(old: EngineState, new: EngineState) {
        // First successful poll only primes baselines — no backlog spam.
        if !didPrime {
            didPrime = true
            for r in new.queue { seenWorkerKeys.insert(r.idempotencyKey) }
            lastBreakerTripped = new.governor.breakerTripped
            lastDoneCount = new.counts.done
            return
        }

        // (a) new WORKER_REQUEST appears (unseen idempotency key)
        for r in new.queue where !seenWorkerKeys.contains(r.idempotencyKey) {
            seenWorkerKeys.insert(r.idempotencyKey)
            if NotificationPrefs.enabled(.proactive) {
                notifier.notify(
                    id: "worker-\(r.idempotencyKey)",
                    title: "New proactive job queued",
                    body: "\(r.jobId) — \(r.shortKey)")
            }
        }

        // (b) governor circuit-breaker trips (edge: false → true)
        let tripped = new.governor.breakerTripped
        if tripped && !lastBreakerTripped
            && NotificationPrefs.enabled(.proactive) {
            notifier.notify(
                id: "breaker-\(Int(Date().timeIntervalSince1970))",
                title: "Budget circuit-breaker tripped",
                body: "Governor ceiling reached — engine is throttling.")
        }
        lastBreakerTripped = tripped

        // (c) counts.done increments
        if let prevDone = lastDoneCount, new.counts.done > prevDone {
            let delta = new.counts.done - prevDone
            if NotificationPrefs.enabled(.proactive) {
                notifier.notify(
                    id: "done-\(new.counts.done)",
                    title: "Proactive work completed",
                    body: "\(delta) job\(delta == 1 ? "" : "s") finished "
                        + "(\(new.counts.done) done total).")
            }
        }
        lastDoneCount = new.counts.done
    }

    // MARK: manual scan

    /// "Scan now" menu action. Triggers the engine, then re-polls.
    func scanNow() {
        guard !scanInProgress else { return }
        scanInProgress = true
        Task.detached(priority: .userInitiated) {
            let result = EngineRunner.runScan()
            await MainActor.run {
                self.lastScanResult = result
                self.scanInProgress = false
                self.poll()
            }
        }
    }
}
