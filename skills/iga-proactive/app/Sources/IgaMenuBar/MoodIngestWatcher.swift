import Foundation
import Observation

// MARK: - Mood ingest watcher — the always-on host that triggers ingest
//
// This is the reliable mechanism we chose over a LaunchAgent: the
// menu-bar app is already running (login item, accessory), so it is the
// dependable host on the always-on Mac mini. This object periodically
// (default hourly — the user wants ~1 h) TRIGGERS the sanctioned
// `ContractGuard.runMoodIngest()` seam, which imports the newest export
// from the configurable iCloud-Drive `Iga/` folder iff it changed and
// re-emits the Mood widget. On success it nudges the MoodWidgetStore to
// re-poll so the Board updates with no user action.
//
// It holds ZERO mood logic and constructs NO subprocess itself — it only
// relays a timer tick to the engine seam (exactly like HabitsWidgetStore
// relays a click). A timer (not a DispatchSource vnode watch) is used
// deliberately: iCloud-Drive files appear as lazily-materialized
// placeholders, so file-system events are unreliable; a periodic poll
// against the idempotent ingest (stable ids + content-sha1 marker → a
// re-run with no/overlapping export is a cheap no-op, never a duplicate)
// is the dependable design.

@MainActor
@Observable
final class MoodIngestWatcher {

    /// Called on the main actor after a SUCCESSFUL ingest so the widget
    /// store can re-poll the refreshed file. Pure UI refresh — no logic.
    @ObservationIgnored
    private let onIngested: () -> Void

    private(set) var lastRun: Date?
    /// The engine's own one-line status (e.g. "mood ingest: imported N
    /// entries…" / "no new export"), surfaced for diagnostics only. The
    /// app never parses or acts on it — the engine decides everything.
    private(set) var lastStatus: String?

    @ObservationIgnored
    private var interval: TimeInterval = 3600   // hourly by default
    @ObservationIgnored
    private var timer: Timer?
    /// One ingest in flight at a time (the timer must not stack runs if a
    /// slow iCloud sync makes one take a while).
    @ObservationIgnored
    private var inFlight = false

    /// Friendly one-liner of the folder being watched, for the widget's
    /// sync popover. Reads the resolved path from the seam (the watcher
    /// already legitimately couples to ContractGuard) and shortens the
    /// long iCloud container prefix to "iCloud Drive ▸ …".
    var watchDirDisplay: String {
        let p = ContractGuard.moodWatchDir()
        if let r = p.range(of: "com~apple~CloudDocs/") {
            return "iCloud Drive ▸ "
                + p[r.upperBound...].replacingOccurrences(
                    of: "/", with: " ▸ ")
        }
        return (p as NSString).abbreviatingWithTildeInPath
    }

    init(onIngested: @escaping () -> Void) {
        self.onIngested = onIngested
        if let env = ProcessInfo.processInfo
            .environment["IGA_MOOD_INGEST_SECONDS"],
           let v = TimeInterval(env), v >= 60 {
            interval = v
        }
    }

    func start() {
        trigger()                       // once at launch
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
        guard !inFlight else { return }
        inFlight = true
        Task.detached(priority: .utility) {
            let outcome = ContractGuard.runMoodIngest()
            await MainActor.run { [weak self] in
                guard let self else { return }
                self.inFlight = false
                self.lastRun = Date()
                if outcome.ok {
                    self.lastStatus = outcome.stdout
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    self.onIngested()
                } else {
                    self.lastStatus = "ingest failed: "
                        + outcome.stderr
                            .trimmingCharacters(
                                in: .whitespacesAndNewlines)
                            .prefix(160)
                }
            }
        }
    }
}
