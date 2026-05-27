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

    /// One non-mutating reproject in flight at a time (de-bounce). A cold
    /// launch fires exactly one; the poll that follows clears it.
    @ObservationIgnored
    private var reprojectInFlight = false
    /// The civil day we've already nudged the engine for. Bounds reproject
    /// to ONE attempt per day per launch — a success makes the staleness
    /// check moot anyway; a failure must not become a subprocess storm
    /// (every 60s poll would otherwise re-fire). A real day-rollover while
    /// running changes this string → exactly one fresh attempt.
    @ObservationIgnored
    private var reprojectedForDay: String?

    /// Process-wide UTC `yyyy-MM-dd` formatter. The engine projects with
    /// `datetime.now(timezone.utc).date()`; the app's notion of "today" MUST
    /// use the SAME civil-date convention or the two disagree by a day near
    /// midnight (which is the staleness bug class we are closing). Immutable
    /// after configuration; DateFormatter formatting is thread-safe.
    @ObservationIgnored
    nonisolated private static let utcDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .iso8601)
        f.timeZone = TimeZone(identifier: "UTC")
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// The real "today" as the engine sees it (UTC civil date). The Compact
    /// strip anchors to THIS, never to the engine's last-emitted `today`, so
    /// today is always the rightmost, always clickable, even on a cold
    /// launch before the reproject lands. Pure date — no habit logic.
    nonisolated static func systemTodayISO() -> String {
        utcDayFormatter.string(from: Date())
    }

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
            } ?? "\(home)/Gaia/state"
        return "\(stateDir)/widgets/habit-tracker-habits.json"
    }

    /// Notifier for the daily habit-accountability nudge (reused from the
    /// proactive engine path). Injected for tests.
    @ObservationIgnored
    private let notifier: Notifier
    /// UserDefaults key holding the civil day we last sent the habit
    /// nudge — so it fires AT MOST once per day even across relaunches.
    @ObservationIgnored
    private let nudgeDayKey = "iga.habits.lastNudgeDay"
    /// Set true after the first successful poll so a cold launch doesn't
    /// fire a backlog before the user has even looked (prime, like
    /// StateStore's notification diffing).
    @ObservationIgnored
    private var nudgePrimed = false

    init(notifier: Notifier = Notifier.shared) {
        self.notifier = notifier
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
                ? "No habits yet — add one in your tracker, then import."
                : nil
            // Reproject ONCE per launch (and per day-rollover), NOT only
            // when the date is stale. The old `today < systemToday` gate
            // meant a NEW engine output (added widget keys like `archived`
            // / `focus`, schema bumps) never reached the app until a record
            // op or the next day — the recurring "I shipped it but the app
            // doesn't show it" friction. `maybeReproject()` already bounds
            // itself to once per civil day per launch (in-memory
            // `reprojectedForDay` + in-flight guard) and is non-mutating,
            // so firing it on the first poll of every launch is safe and
            // guarantees the app always renders the CURRENT engine output.
            if !decoded.habits.isEmpty {
                maybeReproject()
            }
            maybeNudge(decoded.habits)
        } else {
            waitingReason = "habit-tracker: unreadable data file"
        }
    }

    /// Fire at most one non-mutating reproject; re-poll when it returns so
    /// the refreshed widget JSON is picked up immediately. A reproject
    /// FAILURE is intentionally NOT surfaced as a relay error (that banner
    /// is for the user's own clicks) — the Compact strip stays usable
    /// regardless because its window is system-date-anchored, so a failed
    /// refresh degrades to "engine numbers a little stale", never "can't
    /// mark today". Relays ONLY via the sanctioned ContractGuard entry point.
    private func maybeReproject() {
        let today = Self.systemTodayISO()
        guard !reprojectInFlight, reprojectedForDay != today else { return }
        reprojectInFlight = true
        reprojectedForDay = today
        let window = max(1, data.windowDays > 0 ? data.windowDays : 120)
        Task.detached { [weak self] in
            _ = ContractGuard.runReproject(windowDays: window)
            await self?.finishReproject()
        }
    }

    private func finishReproject() {
        reprojectInFlight = false
        poll()
    }

    /// The accountability kinds that warrant a nudge (NOT the positive
    /// `milestone` — that's its own reward, no push needed).
    nonisolated static let nudgeKinds: Set<String> =
        ["at-risk", "slipped", "dormant"]

    /// PURE: the once-a-day habit nudge (title, body) from decoded habits,
    /// or nil when nothing needs attention. Engine-decided — it only reads
    /// `coachKind` the engine already set (no inference, no habit logic).
    /// Unit-tested. Names capped so the banner stays glanceable.
    nonisolated static func habitNudge(
        _ habits: [HabitEntry]
    ) -> (title: String, body: String)? {
        let names = habits
            .filter { nudgeKinds.contains($0.coachKind ?? "") }
            .map(\.name)
        guard !names.isEmpty else { return nil }
        let shown = names.prefix(3).joined(separator: ", ")
        let more = names.count > 3 ? " +\(names.count - 3)" : ""
        return (
            "Iga · habits",
            "\(names.count) need you today: \(shown)\(more)")
    }

    /// Fire the nudge AT MOST once per civil day (persisted across
    /// relaunches via UserDefaults), and never on the very first poll of a
    /// launch (prime — no cold-launch backlog). Relays nothing; pure
    /// notification — the engine already decided salience.
    private func maybeNudge(_ habits: [HabitEntry]) {
        guard nudgePrimed else { nudgePrimed = true; return }
        guard NotificationPrefs.enabled(.habit) else { return }
        let today = Self.systemTodayISO()
        let d = UserDefaults.standard
        guard d.string(forKey: nudgeDayKey) != today,
              let n = Self.habitNudge(habits) else { return }
        d.set(today, forKey: nudgeDayKey)
        notifier.notify(
            id: "habit-nudge-\(today)", title: n.title, body: n.body)
    }

    /// True while a ⋯-menu management op (rename/delete/set-goal/import/
    /// export) is round-tripping the engine — the sheet disables its
    /// controls so a double-submit can't race.
    private(set) var managePending = false

    /// Relay a NAMED management intent to the sanctioned manage entry point, then
    /// refresh from the engine-re-emitted JSON. The app decides NOTHING —
    /// the engine performs the mutation + re-projection; this only names it
    /// and re-reads. A failure is SURFACED via `lastRelayError` (same
    /// contract as a click), never a silent no-op. `onDone(ok)` lets the
    /// sheet dismiss only on success.
    func relayManage(
        habitId: String?,
        op: ContractGuard.ManageOp,
        onDone: (@MainActor (Bool) -> Void)? = nil
    ) {
        guard !managePending else { return }
        managePending = true
        lastRelayError = nil
        let window = max(1, data.windowDays > 0 ? data.windowDays : 120)
        Task.detached { [weak self] in
            let outcome = ContractGuard.runManage(
                habitId: habitId, op: op, windowDays: window)
            await self?.finishManage(
                ok: outcome.ok, exitCode: outcome.exitCode,
                stderr: outcome.stderr, onDone: onDone)
        }
    }

    private func finishManage(
        ok: Bool, exitCode: Int32, stderr: String,
        onDone: (@MainActor (Bool) -> Void)?
    ) {
        managePending = false
        if ok {
            lastRelayError = nil
            poll()
        } else {
            lastRelayError = "couldn't do that — "
                + Self.briefError(exitCode: exitCode, stderr: stderr)
            poll()
        }
        onDone?(ok)
    }

    /// Test entry point: drive `finishManage` directly with a synthesized outcome
    /// (no real subprocess). Not used by production paths.
    func testInjectManageResult(
        ok: Bool, exitCode: Int32, stderr: String
    ) {
        finishManage(
            ok: ok, exitCode: exitCode, stderr: stderr, onDone: nil)
    }

    /// Pending key for one habit-day square (so only that tile shows a
    /// spinner / is disabled while the engine round-trips).
    static func pendingKey(_ habitId: String, _ date: String) -> String {
        "\(habitId)@\(date)"
    }

    func isPending(_ habitId: String, _ date: String) -> Bool {
        pending.contains(Self.pendingKey(habitId, date))
    }

    /// The record op a BINARY square click names — PURE, unit-tested. The
    /// app decides only WHICH gesture, never the resulting math.
    ///
    ///   * already-done day → `.remove` (clear it)
    ///   * not-done day      → `.add` (one tap = done; engine → max(1,…))
    ///
    /// Per-DAY-target habits do NOT use this — they open the log drawer and
    /// relay an explicit `.setAmount` (see `relaySetAmount`); a single tap
    /// must NOT silently complete a 40-rep day.
    nonisolated static func recordOp(
        currentlyDone: Bool
    ) -> ContractGuard.RecordOp {
        currentlyDone ? .remove : .add
    }

    /// Relay a BINARY square click to the SANCTIONED record entry point, then
    /// refresh from the engine-re-emitted JSON. The app decides NOTHING
    /// about the result — it names the gesture; the engine computes the new
    /// amount/streak/grid. (Per-day-goal habits use `relaySetAmount` via the
    /// drawer instead.)
    func relayToggle(
        habitId: String, date: String, currentlyDone: Bool
    ) {
        let key = Self.pendingKey(habitId, date)
        guard !pending.contains(key) else { return }
        pending.insert(key)
        lastRelayError = nil
        let op = Self.recordOp(currentlyDone: currentlyDone)
        let window = max(1, data.windowDays > 0 ? data.windowDays : 120)
        Task.detached { [weak self] in
            let outcome = ContractGuard.runRecord(
                habitId: habitId, date: date, op: op, windowDays: window)
            await self?.finishRelay(
                key: key, ok: outcome.ok,
                exitCode: outcome.exitCode, stderr: outcome.stderr)
        }
    }

    /// Relay an EXPLICIT per-day amount (quick-log drawer: +/- , a batch
    /// chip, Reset→0, Fill Day→target). The app only NAMES the desired
    /// amount; the engine clamps/derives streak/goal/level and re-emits. The
    /// drawer reconciles its display from that engine truth on the next
    /// poll — it never trusts a local optimistic value. `amount` is clamped
    /// ≥ 0; 0 clears the day (same as Reset).
    func relaySetAmount(habitId: String, date: String, amount: Int) {
        let key = Self.pendingKey(habitId, date)
        guard !pending.contains(key) else { return }
        pending.insert(key)
        lastRelayError = nil
        let op: ContractGuard.RecordOp = .setAmount(max(0, amount))
        let window = max(1, data.windowDays > 0 ? data.windowDays : 120)
        Task.detached { [weak self] in
            let outcome = ContractGuard.runRecord(
                habitId: habitId, date: date, op: op, windowDays: window)
            await self?.finishRelay(
                key: key, ok: outcome.ok,
                exitCode: outcome.exitCode, stderr: outcome.stderr)
        }
    }

    /// The amount the engine currently has for one (habit, day) — the
    /// drawer's source of truth (read-only projection of decoded state, no
    /// habit logic). nil if the habit/day isn't in the current window.
    func currentAmount(habitId: String, date: String) -> Int? {
        guard let h = data.habits.first(where: { $0.id == habitId })
        else { return nil }
        return h.cells.first(where: { $0.date == date })?.amount
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

/// The two render modes (compact). Compact is the default.
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
