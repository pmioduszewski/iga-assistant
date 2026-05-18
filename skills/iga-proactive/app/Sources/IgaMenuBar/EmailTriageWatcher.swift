import Foundation
import Observation

// MARK: - Email-triage watcher — retires the last LaunchAgent
//
// The reliable replacement for `com.iga.email-triage` (the final
// pre-menu-bar LaunchAgent): the always-on menu-bar app fires the email
// skill's OWN proven triage wrapper once per civil day, no earlier than
// the original 06:00 cron hour. Robust to a sleeping Mac — if the
// machine was off at 06:00 it runs at the first eligible tick after
// wake, exactly like the Scheduler's coalesced model (and unlike a
// rigid launchd calendar trigger that a sleeping Mac silently skips).
//
// It holds ZERO email logic and constructs NO subprocess itself — it
// only relays a day-gated tick to the sanctioned
// `ContractGuard.runEmailTriage()` seam (the wrapper self-handles
// PATH/cwd/logging, byte-for-byte the launchd-era behaviour). The
// once-per-day marker is persisted in UserDefaults so an app relaunch
// can never double-trigger a real Gmail mutation.

@MainActor
@Observable
final class EmailTriageWatcher {

    private(set) var lastRun: Date?
    /// The seam's last result (diagnostics only — the app never parses
    /// or acts on it; the email skill decides everything).
    private(set) var lastStatus: String?

    /// Earliest local hour the daily triage may fire (matches the
    /// retired LaunchAgent's StartCalendarInterval Hour = 6).
    @ObservationIgnored
    private let earliestHour = 6
    /// Poll cadence — hourly is plenty for a once-per-day gate.
    @ObservationIgnored
    private var interval: TimeInterval = 3600
    @ObservationIgnored
    private var timer: Timer?
    /// Observable so the Mail section can show a spinner + disable the
    /// button while a (potentially multi-second) triage is in flight —
    /// otherwise the click "feels dead". Also the reentrancy guard.
    private(set) var isRunning = false

    /// Persisted civil day (local, yyyy-MM-dd) the triage last ran, so a
    /// relaunch never re-triggers a same-day Gmail mutation.
    @ObservationIgnored
    private let lastDayKey = "iga.emailTriage.lastDay"
    /// lastRun/lastStatus are persisted so the panel shows the REAL last
    /// triage (incl. the automatic 06:00 one) across app restarts —
    /// without this, every relaunch wrongly shows "never".
    @ObservationIgnored
    private let lastRunKey = "iga.emailTriage.lastRunTs"
    @ObservationIgnored
    private let lastStatusKey = "iga.emailTriage.lastStatus"

    private static let dayFmt: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    init() {
        if let env = ProcessInfo.processInfo
            .environment["IGA_EMAIL_TRIAGE_SECONDS"],
           let v = TimeInterval(env), v >= 60 {
            interval = v
        }
        // Restore the real last-run so a relaunch doesn't show "never".
        let d = UserDefaults.standard
        let ts = d.double(forKey: lastRunKey)
        if ts > 0 { lastRun = Date(timeIntervalSince1970: ts) }
        lastStatus = d.string(forKey: lastStatusKey)
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
        // once per civil day, not before the original cron hour.
        guard hour >= earliestHour,
              UserDefaults.standard.string(forKey: lastDayKey) != today
        else { return }

        isRunning = true
        // Claim the day BEFORE running so a crash/timeout can't cause a
        // same-day retry storm (matches the launchd "fire once" intent).
        UserDefaults.standard.set(today, forKey: lastDayKey)
        run()
    }

    /// Shared execution path used by both the automatic daily trigger and the
    /// manual "Run now" button. Assumes `inFlight` has already been set to
    /// `true` by the caller (so the guard above or the button handler owns it).
    private func run() {
        Task.detached(priority: .utility) {
            let outcome = ContractGuard.runEmailTriage()
            await MainActor.run { [weak self] in
                guard let self else { return }
                self.isRunning = false
                let now = Date()
                let status = outcome.ok
                    ? "email triage ok"
                    : "email triage failed: "
                        + String(outcome.stderr
                            .trimmingCharacters(
                                in: .whitespacesAndNewlines)
                            .prefix(160))
                self.lastRun = now
                self.lastStatus = status
                let d = UserDefaults.standard
                d.set(now.timeIntervalSince1970, forKey: self.lastRunKey)
                d.set(status, forKey: self.lastStatusKey)
            }
        }
    }

    /// Manual run triggered by the user via the "Run now" button. Ignores the
    /// once-per-day marker — an explicit user action always runs. Relays only
    /// through the sanctioned `ContractGuard.runEmailTriage()` seam.
    func runNow() {
        guard !isRunning else { return }
        isRunning = true
        run()
    }
}
