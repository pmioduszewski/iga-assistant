import Foundation
import Observation

// MARK: - Widget host store (read-only poller)
//
// Discovers registered widgets once (SkillDiscovery, read-only SKILL.md scan)
// and polls each widget's data_source file on a timer. It NEVER writes a data
// file, NEVER generates widget data or coach text, NEVER execs anything. It is
// the widget analogue of StateStore: pure render-feed.
//
// Robustness: an absent / empty / half-written / garbage data file becomes a
// `.waiting` or `.error` slot — the UI shows "waiting for <skill>" and never
// crashes. Atomic-ish read: one `Data(contentsOf:)`; the producers write via
// tmp+os.replace so a reader sees either the old or the new whole file.

/// Render state for one registered widget slot.
enum WidgetSlotState: Equatable {
    case waiting(String)        // no/partial data yet — reason
    case ready(WidgetData)
    case error(String)
}

struct WidgetSlot: Identifiable, Equatable {
    let spec: RegisteredWidget
    var state: WidgetSlotState
    var lastPolled: Date?

    var id: String { spec.uniqueKey }
}

@MainActor
@Observable
final class WidgetHostStore {

    private(set) var slots: [WidgetSlot] = []

    /// Poll cadence. Uses the smallest declared widget `refresh`, clamped to
    /// a sane floor; overridable via `IGA_WIDGET_POLL_SECONDS` for tests.
    @ObservationIgnored
    private(set) var pollInterval: TimeInterval = 60
    @ObservationIgnored
    private var timer: Timer?

    init() {}

    func start() {
        rediscover()
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

    /// The legacy v1 single-habit widget (`habit-tracker/habit-grid`,
    /// data file `habit-tracker-habit-grid.json`). Still EMITTED by the
    /// engine for back-compat, but it is superseded in the UI by the
    /// Wave-B multi-habit Habits widget (its own dedicated store/view).
    /// Surfacing it on the generic board would show a stale, redundant
    /// "1 habit + coach" card, so it is excluded from board discovery
    /// here — engine emission is untouched (back-compat/tests rely on it).
    static let legacyHabitGridKey = "habit-tracker/habit-grid"

    /// Re-scan SKILL.md frontmatter for registered widgets (read-only).
    func rediscover() {
        let found = SkillDiscovery.scan().widgets
            .filter { $0.uniqueKey != Self.legacyHabitGridKey }
        // Preserve any already-loaded state for widgets that still exist.
        var prev: [String: WidgetSlot] = [:]
        for s in slots { prev[s.spec.uniqueKey] = s }
        slots = found.map { spec in
            if let old = prev[spec.uniqueKey] {
                return WidgetSlot(spec: spec, state: old.state,
                                  lastPolled: old.lastPolled)
            }
            return WidgetSlot(
                spec: spec,
                state: .waiting("waiting for \(spec.skill)"),
                lastPolled: nil)
        }
        if let env = ProcessInfo.processInfo
            .environment["IGA_WIDGET_POLL_SECONDS"],
           let v = TimeInterval(env), v >= 2 {
            pollInterval = v
        } else if let minRefresh = found.map(\.refresh).min() {
            pollInterval = max(5, TimeInterval(minRefresh))
        }
    }

    func poll() {
        for idx in slots.indices {
            slots[idx].state = Self.readSlot(slots[idx].spec)
            slots[idx].lastPolled = Date()
        }
    }

    /// Read + decode one widget's data file. Pure (static) so tests can
    /// exercise it without a timer. Never throws — degrades to a state.
    static func readSlot(_ spec: RegisteredWidget) -> WidgetSlotState {
        let path = spec.dataSource
        guard FileManager.default.fileExists(atPath: path) else {
            return .waiting("waiting for \(spec.skill)")
        }
        guard let data = try? Data(
            contentsOf: URL(fileURLWithPath: path)) else {
            return .waiting("waiting for \(spec.skill) (unreadable)")
        }
        if data.isEmpty {
            return .waiting("waiting for \(spec.skill) (writing…)")
        }
        do {
            let w = try WidgetData.decode(from: data)
            return .ready(w)
        } catch {
            return .error("\(spec.skill): unreadable data file")
        }
    }
}
