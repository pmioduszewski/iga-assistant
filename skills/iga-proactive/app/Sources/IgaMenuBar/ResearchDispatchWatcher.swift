import Foundation
import Observation

// MARK: - Research-dispatch watcher — out-of-session proactive research
//
// The always-on menu-bar app fires the proactive-research skill's OWN
// self-contained dispatch wrapper (iga-research-dispatch) once per civil day,
// no earlier than 06:00 local — so research drawers are filed BEFORE the
// morning `/gm` (which is surfacing-only and never spawns workers itself).
//
// Same contract as EmailTriageWatcher: ZERO research logic, constructs NO
// subprocess itself — it only relays a day-gated tick to the sanctioned
// `ContractGuard.runResearchDispatch()` entry point. The wrapper +
// engine governor (atomic ledger claim, per-run cap, rolling-window budget)
// own admission and cost; the once-per-day marker is persisted in
// UserDefaults so an app relaunch can never double-trigger a real dispatch.
//
// Robust to a sleeping Mac: if off at 06:00 it runs at the first eligible
// tick after wake (coalesced), unlike a rigid launchd calendar trigger.

@MainActor
@Observable
final class ResearchDispatchWatcher {

    private(set) var lastRun: Date?
    /// Last result (diagnostics only — the app never parses or acts on it).
    private(set) var lastStatus: String?
    private(set) var lastDurationSec: Double?
    /// Compact human summary read from the wrapper's OWN emitted report
    /// (~/Library/Logs/iga/research-dispatch-<date>.json) — pure presentation.
    private(set) var lastSummary: String?
    private(set) var isRunning = false

    /// Earliest local hour the daily dispatch may fire (before the ~07:00 /gm).
    @ObservationIgnored
    private let earliestHour = 6
    /// Poll cadence — hourly is plenty for a once-per-day gate.
    @ObservationIgnored
    private var interval: TimeInterval = 3600
    @ObservationIgnored
    private var timer: Timer?

    @ObservationIgnored
    private let lastDayKey = "iga.researchDispatch.lastDay"
    @ObservationIgnored
    private let lastRunKey = "iga.researchDispatch.lastRunTs"
    @ObservationIgnored
    private let lastStatusKey = "iga.researchDispatch.lastStatus"
    @ObservationIgnored
    private let lastDurationKey = "iga.researchDispatch.lastDurationSec"
    @ObservationIgnored
    private let lastSummaryKey = "iga.researchDispatch.lastSummary"

    private static let dayFmt: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    init() {
        if let env = ProcessInfo.processInfo
            .environment["IGA_RESEARCH_DISPATCH_SECONDS"],
           let v = TimeInterval(env), v >= 60 {
            interval = v
        }
        let d = UserDefaults.standard
        let ts = d.double(forKey: lastRunKey)
        if ts > 0 { lastRun = Date(timeIntervalSince1970: ts) }
        lastStatus = d.string(forKey: lastStatusKey)
        let dur = d.double(forKey: lastDurationKey)
        if dur > 0 { lastDurationSec = dur }
        lastSummary = d.string(forKey: lastSummaryKey)
    }

    /// Read the wrapper's OWN report and fold it into one compact line.
    /// Best-effort + defensive: a missing/half-written file yields nil.
    private func readSummary(for day: String) -> String? {
        let path = FileManager.default
            .homeDirectoryForCurrentUser
            .appendingPathComponent(
                "Library/Logs/iga/research-dispatch-\(day).json")
        guard let data = try? Data(contentsOf: path),
              let obj = try? JSONSerialization
                .jsonObject(with: data) as? [String: Any]
        else { return nil }
        func i(_ k: String) -> Int { (obj[k] as? Int) ?? 0 }
        return "\(i("dispatched")) dispatched / \(i("queue_total")) queued "
            + "(cap \(i("cap")))"
    }

    func start() {
        trigger()                       // gated; usually a no-op at launch
        let t = Timer(timeInterval: interval, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.trigger() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func trigger() {
        guard !isRunning else { return }
        let now = Date()
        let today = Self.dayFmt.string(from: now)
        let hour = Calendar.current.component(.hour, from: now)
        // once per civil day, not before the earliest hour.
        guard hour >= earliestHour,
              UserDefaults.standard.string(forKey: lastDayKey) != today
        else { return }

        isRunning = true
        // Claim the day BEFORE running so a crash/timeout can't cause a
        // same-day retry storm.
        UserDefaults.standard.set(today, forKey: lastDayKey)
        run()
    }

    private func run() {
        Task.detached(priority: .utility) {
            let outcome = ContractGuard.runResearchDispatch()
            await MainActor.run { [weak self] in
                guard let self else { return }
                self.isRunning = false
                let now = Date()
                let status = outcome.ok
                    ? "research dispatch ok"
                    : "research dispatch failed: "
                        + String(outcome.stderr
                            .trimmingCharacters(
                                in: .whitespacesAndNewlines)
                            .prefix(160))
                let dur = outcome.finishedAt
                    .timeIntervalSince(outcome.startedAt)
                let summary = outcome.ok
                    ? self.readSummary(for: Self.dayFmt.string(from: now))
                    : nil
                self.lastRun = now
                self.lastStatus = status
                self.lastDurationSec = dur
                self.lastSummary = summary
                let d = UserDefaults.standard
                d.set(now.timeIntervalSince1970, forKey: self.lastRunKey)
                d.set(status, forKey: self.lastStatusKey)
                d.set(dur, forKey: self.lastDurationKey)
                if let summary {
                    d.set(summary, forKey: self.lastSummaryKey)
                } else {
                    d.removeObject(forKey: self.lastSummaryKey)
                }
            }
        }
    }

    /// Manual run (e.g. a future "Run now" button). Ignores the once-per-day
    /// marker — an explicit action always runs. Relays only through the
    /// sanctioned `ContractGuard.runResearchDispatch()` entry point.
    func runNow() {
        guard !isRunning else { return }
        isRunning = true
        run()
    }
}
