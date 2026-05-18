import Foundation
import Observation
import AppKit

// MARK: - Scheduler host (the launchd thin-daemon replacement)
//
// Role: replace the planned launchd agent. It fires the engine scan on a
// cadence AND shortly after the Mac wakes/powers on — solving the
// "Mac is off at 07:00" problem the launchd plan had.
//
// It triggers the engine ONLY (via StateStore.scanNow → ContractGuard). It
// makes no scheduling/admission decision about jobs themselves — the engine
// owns whether a job actually fires once scanned. This box just answers
// "when do we ask the engine to look?".
//
// Mechanisms:
//   * NSBackgroundActivityScheduler — energy-friendly periodic trigger
//     (interval ≈ the morning/evening cadence; the OS coalesces it).
//   * NSWorkspace.didWakeNotification — fire ~30s after wake so an
//     overnight-off Mac still gets its morning scan when it powers on.
//
// A menu toggle enables/disables scheduling; state persists in UserDefaults.

@MainActor
@Observable
final class Scheduler {

    private(set) var enabled: Bool
    private(set) var lastFire: Date?
    private(set) var nextHint: String = "—"

    // Non-UI internals — excluded from observation (they never drive a view;
    // only `enabled`/`lastFire`/`nextHint` above are observed state).
    @ObservationIgnored
    private let activity = NSBackgroundActivityScheduler(
        identifier: "com.iga.menubar.scan")
    @ObservationIgnored
    private weak var store: StateStore?
    @ObservationIgnored
    private var wakeObserver: NSObjectProtocol?
    @ObservationIgnored
    private let defaultsKey = "IgaSchedulerEnabled"

    /// Cadence target: roughly twice daily (07:00 / 19:00-ish). The OS
    /// coalesces background activity so we use a ~6h interval with tolerance;
    /// the wake-trigger fills the gap when the Mac was asleep at those hours.
    @ObservationIgnored
    private let interval: TimeInterval = 6 * 3600
    @ObservationIgnored
    private let tolerance: TimeInterval = 2 * 3600

    init(store: StateStore) {
        self.store = store
        let d = UserDefaults.standard
        // Default ON; honor a persisted opt-out.
        self.enabled = d.object(forKey: defaultsKey) as? Bool ?? true
        if enabled { startScheduling() }
        installWakeObserver()
    }

    deinit {
        if let o = wakeObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(o)
        }
    }

    func setEnabled(_ on: Bool) {
        enabled = on
        UserDefaults.standard.set(on, forKey: defaultsKey)
        if on { startScheduling() } else { stopScheduling() }
    }

    func toggle() { setEnabled(!enabled) }

    private func startScheduling() {
        activity.repeats = true
        activity.interval = interval
        activity.tolerance = tolerance
        activity.qualityOfService = .utility
        activity.schedule { [weak self] completion in
            Task { @MainActor in
                self?.fire(reason: "scheduled")
                completion(.finished)
            }
        }
        nextHint = "≈ every \(Int(interval / 3600))h (OS-coalesced) + on wake"
    }

    private func stopScheduling() {
        activity.invalidate()
        nextHint = "disabled"
    }

    private func installWakeObserver() {
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification,
            object: nil, queue: .main) { [weak self] _ in
            // Delay so network/MCP/uv have a moment after wake.
            DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
                Task { @MainActor in
                    guard let self, self.enabled else { return }
                    self.fire(reason: "wake")
                }
            }
        }
    }

    /// Single fire path: ask the engine to scan, then UI/notifications
    /// refresh via the store's post-scan poll. No job logic here.
    private func fire(reason: String) {
        guard enabled else { return }
        lastFire = Date()
        store?.scanNow()
        NSLog("Iga scheduler fired (%@)", reason)
    }

    /// Used by tests to assert the fire path only triggers the engine.
    func testFire() { fire(reason: "test") }
}
