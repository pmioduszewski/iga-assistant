import Foundation
import Observation

// MARK: - Multi-habit widget store (read-poll + single-entry point relay)
//
// Wave B. Polls the skill-produced `habit-tracker-habits.json` (schema_version
// 2) and exposes the decoded `HabitsWidgetData` for the view. It holds ZERO
// habit logic: it computes no streak, no goal, no grid level. The ONLY side
// effect it can cause is relaying a click to the sanctioned record entry point
// (`ContractGuard.runRecord`) — the engine performs the mutation and re-emits
// the JSON; this store then re-reads the refreshed file. Deleting the app
// removes this poller only; the record CLI + engine keep working standalone.
//
// Robustness mirrors WidgetHostStore: an absent/partial/garbage file becomes
// an empty habit list with a benign reason, never a crash. The producers
// write via tmp+os.replace so a reader sees either the whole old or whole new
// file.

@MainActor
@Observable
final class HabitsWidgetStore {

    /// Decoded data for the view. Empty + a reason until the file appears.
    private(set) var data = HabitsWidgetData()
    private(set) var waitingReason: String? =
        "waiting for habit-tracker"
    private(set) var lastPolled: Date?

    /// A relay in flight (one click at a time per habit-day). The view
    /// disables the tapped square until the engine round-trips.
    private(set) var pending: Set<String> = []

    /// The last record-entry point failure, surfaced to the Compact UI so a failed
    /// click is NEVER a silent no-op again (the original bug: a Finder-
    /// launched .app had no PATH, `uv` wasn't found, the non-zero exit was
    /// swallowed). Set on a failed relay, cleared on the next successful one
    /// or the next start of a relay. The Compact row renders this inline.
    private(set) var lastRelayError: String?

    // Persistence key for the single user-picked, cross-launch-sticky knob
    // (the Compact ↔ Grid view-mode toggle). The dense period selector was
    // removed — Grid now always shows the full history, so there is no
    // `densePeriodDays` knob anymore.
    @ObservationIgnored
    private let viewModeKey = "iga.habits.viewMode"

    /// Raw persisted view-mode (compact = default). Same seed-and-persist
    /// pattern; `viewMode` is the typed façade over it.
    private var viewModeRaw: Int {
        didSet {
            UserDefaults.standard.set(viewModeRaw, forKey: viewModeKey)
        }
    }

    var viewMode: HabitsViewMode {
        get { HabitsViewMode(rawValue: viewModeRaw) ?? .compact }
        set { viewModeRaw = newValue.rawValue }
    }

    @ObservationIgnored
    private(set) var pollInterval: TimeInterval = 60
    @ObservationIgnored
    private var timer: Timer?

    /// Resolved path to the Wave-B data file. Read-only; mirrors the v1
    /// widget path the host already polls, just the multi-habit sibling.
    static func dataPath() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_HABITS_DATA_FILE"], !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let stateDir = ProcessInfo.processInfo
            .environment["IGA_STATE_DIR"].flatMap {
                $0.isEmpty ? nil : $0
            } ?? "\(home)/Iga/state"
        return "\(stateDir)/widgets/habit-tracker-habits.json"
    }

    init() {
        // Seed the sticky view-mode knob from UserDefaults, preserving the
        // exact fallback the old @AppStorageBacked wrapper used (compact
        // mode). `object(forKey:) as? Int` keeps the "unset → fallback"
        // semantics (a missing key is nil, not 0).
        let d = UserDefaults.standard
        self.viewModeRaw =
            d.object(forKey: viewModeKey) as? Int
                ?? HabitsViewMode.compact.rawValue
        if let env = ProcessInfo.processInfo
            .environment["IGA_WIDGET_POLL_SECONDS"],
           let v = TimeInterval(env), v >= 2 {
            pollInterval = v
        }
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

    /// Read + decode the data file. Never throws to the caller — degrades to
    /// an empty list + a benign reason. Pure read.
    func poll() {
        lastPolled = Date()
        let path = Self.dataPath()
        guard FileManager.default.fileExists(atPath: path) else {
            data = HabitsWidgetData()
            waitingReason = "waiting for habit-tracker"
            return
        }
        guard let bytes = try? Data(
            contentsOf: URL(fileURLWithPath: path)) else {
            waitingReason = "waiting for habit-tracker (unreadable)"
            return
        }
        if bytes.isEmpty {
            waitingReason = "waiting for habit-tracker (writing…)"
            return
        }
        if let decoded = try? HabitsWidgetData.decode(from: bytes) {
            data = decoded
            waitingReason = decoded.habits.isEmpty
                ? "No habits yet — add one in HabitKit, then import."
                : nil
        } else {
            waitingReason = "habit-tracker: unreadable data file"
        }
    }

    /// Pending key for one habit-day square (so only that tile shows a
    /// spinner / is disabled while the engine round-trips).
    static func pendingKey(_ habitId: String, _ date: String) -> String {
        "\(habitId)@\(date)"
    }

    func isPending(_ habitId: String, _ date: String) -> Bool {
        pending.contains(Self.pendingKey(habitId, date))
    }

    /// Relay a square click to the SANCTIONED record entry point, then refresh from
    /// the engine-re-emitted JSON. The app decides NOTHING about the result —
    /// it names the gesture; the engine computes the new amount/streak/grid.
    ///
    /// Toggle semantics for the gesture (which gesture, not the math):
    ///   * a not-done day  → `.add`
    ///   * an already-done day → `.remove`
    /// (`.setAmount` is exposed for a future amount stepper; not wired to a
    /// plain click so the wife-test "tap to mark done/undone" stays obvious.)
    func relayToggle(habitId: String, date: String, currentlyDone: Bool) {
        let key = Self.pendingKey(habitId, date)
        guard !pending.contains(key) else { return }
        pending.insert(key)
        lastRelayError = nil
        let op: ContractGuard.RecordOp =
            currentlyDone ? .remove : .add
        let window = max(1, data.windowDays > 0 ? data.windowDays : 120)
        Task.detached { [weak self] in
            let outcome = ContractGuard.runRecord(
                habitId: habitId, date: date, op: op, windowDays: window)
            await self?.finishRelay(
                key: key, ok: outcome.ok,
                exitCode: outcome.exitCode, stderr: outcome.stderr)
        }
    }

    /// MainActor continuation of a relay — re-read the engine-refreshed file
    /// (the engine performed the mutation + re-projection; the app only
    /// reads). On FAILURE the non-zero exit / stderr is now SURFACED via
    /// `lastRelayError` (the Compact row renders it) instead of being
    /// swallowed into a silent no-op — that swallow was the original bug.
    private func finishRelay(
        key: String, ok: Bool, exitCode: Int32, stderr: String
    ) {
        pending.remove(key)
        if ok {
            lastRelayError = nil
            // The engine re-emitted the JSON; re-read promptly so the
            // square visibly flips without waiting a full poll interval.
            poll()
        } else {
            let detail = Self.briefError(exitCode: exitCode, stderr: stderr)
            lastRelayError = "couldn't save that — \(detail)"
            // Still re-poll: harmless, and recovers if the file did change.
            poll()
        }
    }

    /// Test entry point: drive `finishRelay` directly with a synthesized outcome
    /// so the failure-propagation contract is unit-tested without spawning
    /// the real subprocess. Not used by production code paths.
    func testInjectRelayResult(
        key: String, ok: Bool, exitCode: Int32, stderr: String
    ) {
        finishRelay(key: key, ok: ok, exitCode: exitCode, stderr: stderr)
    }

    /// One short human line from a record-entry point failure — picks the most
    /// actionable signal (the classic cause is `uv` not found on a
    /// Finder-launched app). Pure string shaping; no habit logic.
    nonisolated static func briefError(
        exitCode: Int32, stderr: String
    ) -> String {
        let s = stderr.lowercased()
        if s.contains("command not found") || s.contains("no such file")
            || s.contains("not found") {
            return "engine runner (uv) not found — see README setup"
        }
        if exitCode == -1 { return "couldn't launch the engine" }
        if exitCode == -2 { return "the engine timed out" }
        let last = stderr
            .split(whereSeparator: \.isNewline)
            .last.map(String.init)?
            .trimmingCharacters(in: .whitespaces) ?? ""
        if !last.isEmpty { return String(last.prefix(120)) }
        return "exit \(exitCode)"
    }
}

/// The two render modes (HabitKit-class). Compact is the default.
enum HabitsViewMode: Int, CaseIterable, Equatable {
    case compact = 0     // one row/habit: name + last 7d + streak + ring
    case dense = 1       // GitHub-model 7×weeks grid, height-bounded

    var label: String {
        switch self {
        case .compact: return "Compact"
        case .dense:   return "Grid"
        }
    }
}

// The old `@AppStorageBacked` property wrapper was removed in the
// @Observable migration: a custom property wrapper cannot compose with the
// @Observable macro's storage transform, so the sticky view-mode knob
// (viewModeRaw) is now a plain observed stored property seeded from
// UserDefaults at init and persisted on didSet. Same behavior, now
// Observation-tracked (no manual objectWillChange.send()). The dense period
// knob (densePeriodDays) was removed entirely with the period selector —
// Grid now always renders the full decoded history.
