import SwiftUI

// MARK: - Mood Board section — dense, mood-meter-coloured calendar
//
// The mood analogue of HabitsWidgetView's Grid (dense) mode, and ONLY
// that mode: a fixed-small-cell (denseCell=7) × denseGap GitHub-style
// contribution grid, 7 weekday rows, the full decoded history, inside a
// horizontal ScrollView anchored to the trailing edge so the newest days
// show first. Each day's tile is painted with that day's DOMINANT
// emotion's mood-meter QUADRANT colour (the engine's `qcells[].color`,
// i.e. the same palette the source mood app uses) — NOT a
// green 0→4 valence ramp.
//
// It deliberately REUSES the layout primitives that already proved out on
// the habit grid (`HabitsWidgetView.weekColumns / fillCells /
// monthLabelColumns / denseGridMetrics / color / denseContentWidth`), so
// weekday alignment, month labels and the no-stretch invariant are
// identical and tested once.
//
// STRICTLY READ-ONLY. There is NO Button, NO onTapGesture, NO relay, NO
// subprocess anywhere in this file. Logging a mood happens through the
// sanctioned `engine/record.py` chat entry point, never the UI — the same
// render+relay contract the ContractLitmus greps assert for the habit
// Grid.

struct MoodWidgetView: View {
    let store: MoodWidgetStore
    /// Read-only view of the always-on ingest watcher (last check time +
    /// the engine's one-line result). The view RENDERS it; it never
    /// triggers or decides anything.
    let sync: MoodIngestWatcher

    @State private var showSync = false

    // Cached formatter (allocated once). `nonisolated(unsafe)` matches the
    // codebase pattern for shared Foundation formatters — only ever read
    // on the main actor here; DateFormatter/RelativeDateTimeFormatter
    // formatting is thread-safe regardless.
    nonisolated(unsafe) private static let rel:
        RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()

    private static func ago(_ d: Date?) -> String {
        guard let d else { return "never" }
        return rel.localizedString(for: d, relativeTo: Date())
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            header
            if let reason = store.waitingReason,
               !store.data.cells.contains(where: { $0.count > 0 }) {
                Label(reason, systemImage: "hourglass")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                moodNowRow
                grid
                legend
                if !store.data.coachText.isEmpty {
                    coachLine(store.data.coachText)
                }
            }
        }
    }

    // MARK: header — title + logged-day count badge

    private var loggedDays: Int {
        store.data.cells.reduce(0) { $0 + ($1.count > 0 ? 1 : 0) }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Text("MOOD")
                .font(.caption).fontWeight(.semibold)
                .tracking(0.6).foregroundStyle(.secondary)
            Text("\(loggedDays)")
                .font(.caption).fontWeight(.semibold).monospacedDigit()
                .foregroundStyle(loggedDays == 0
                    ? Color.secondary : Color.blue)
                .padding(.horizontal, 6).padding(.vertical, 1)
                .background(Capsule().fill(
                    (loggedDays == 0 ? Color.secondary : Color.blue)
                        .opacity(0.12)))
            if !store.data.label.isEmpty {
                Text(store.data.label)
                    .font(.caption2).foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            syncChip
        }
    }

    // MARK: mood sync-state indicator (render-only — never triggers ingest)

    private var newestEntryISO: String? {
        store.data.cells.last(where: { $0.count > 0 })?.date
    }

    private enum SyncState { case ok, behind, stale, error, none }

    private var daysSinceNewest: Int? {
        guard let iso = newestEntryISO, let d = isoDate(iso)
        else { return nil }
        let cal = Calendar.current
        let a = cal.startOfDay(for: d)
        let b = cal.startOfDay(for: Date())
        return cal.dateComponents([.day], from: a, to: b).day
    }

    private func isoDate(_ iso: String) -> Date? {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .iso8601)
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: iso)
    }

    private var syncState: SyncState {
        if let s = sync.lastStatus, s.lowercased().contains("fail") {
            return .error
        }
        guard sync.lastRun != nil else { return .none }
        guard let d = daysSinceNewest else { return .behind }
        if d <= 1 { return .ok }
        if d <= 7 { return .behind }
        return .stale
    }

    private var syncGlyph: (name: String, tint: Color, word: String) {
        switch syncState {
        case .ok:
            return ("checkmark.icloud.fill", .green, "Up to date")
        case .behind:
            return ("icloud.fill", .orange, "A few days behind")
        case .stale:
            return ("exclamationmark.icloud.fill", .red, "Stale")
        case .error:
            return ("exclamationmark.icloud.fill", .red, "Sync error")
        case .none:
            return ("icloud", .secondary, "Not synced yet")
        }
    }

    private var syncChip: some View {
        let g = syncGlyph
        return Image(systemName: g.name)
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(g.tint)
            .onHoverDelayed { showSync = $0 }
            .popover(isPresented: $showSync, arrowEdge: .bottom) {
                syncPopover
            }
            .accessibilityLabel("Mood sync: \(g.word)")
    }

    private func syncRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(label)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary)
                .frame(width: 92, alignment: .leading)
            Text(value)
                .font(.system(size: 10))
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }

    private var syncPopover: some View {
        let g = syncGlyph
        return VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Image(systemName: g.name)
                    .foregroundStyle(g.tint)
                Text(g.word).font(.caption).fontWeight(.semibold)
            }
            Divider()
            syncRow("Last check", Self.ago(sync.lastRun))
            syncRow("Result",
                    sync.lastStatus.map {
                        $0.replacingOccurrences(
                            of: "mood ingest: ", with: "")
                    } ?? "—")
            syncRow("Latest mood",
                    newestEntryISO.map {
                        let pretty =
                            HabitsWidgetView.prettyDate($0) ?? $0
                        if let d = daysSinceNewest {
                            return d <= 0 ? "\(pretty) (today)"
                                : "\(pretty) (\(d)d ago)"
                        }
                        return pretty
                    } ?? "none yet")
            syncRow("Watching", sync.watchDirDisplay)
            Text("Auto-checks hourly. Drop a mood-app export in "
                 + "that folder (or log in chat) and it appears here.")
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(width: 270)
    }

    // MARK: dense grid (reuses the proven habit-grid layout primitives)

    /// Layout series for the shared column/label maths: a `GridCell` per
    /// decoded day, `level` = "logged?" only (1/0). Colour comes from the
    /// per-day quadrant map below — NOT from `level` (no valence ramp).
    private var layoutCells: [GridCell] {
        store.data.cells.map {
            GridCell(date: $0.date, level: $0.count > 0 ? 1 : 0)
        }
    }

    private var byDate: [String: MoodDayCell] {
        Dictionary(store.data.cells.map { ($0.date, $0) },
                   uniquingKeysWith: { _, b in b })
    }

    // Empty-day tile. The engine's palette["none"] is an absolute RGB
    // (looks ~black on a light system appearance); the empty cell is pure
    // presentation, so we mirror the habit grid's appearance-adaptive
    // neutral instead of an engine hex — readable in both light and dark.
    private var noLogColor: Color {
        Color.secondary.opacity(0.12)
    }

    private var grid: some View {
        let avail = HabitsWidgetView.denseContentWidth()
        let filled = HabitsWidgetView.fillCells(
            layoutCells, availableWidth: avail)
        let cols = HabitsWidgetView.weekColumns(filled)
        let labels = HabitsWidgetView.monthLabelColumns(cols)
        let m = HabitsWidgetView.denseGridMetrics(
            availableWidth: avail, columns: cols.count)
        let map = byDate
        return ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: m.gap) {
                monthHeader(cols: cols, metrics: m, labels: labels)
                gridBody(cols: cols, metrics: m, map: map)
            }
        }
        .frame(height: m.blockHeight
            + HabitsWidgetView.monthHeaderHeight + m.gap)
        .defaultScrollAnchor(.trailing)
    }

    /// Mirrors `HabitsWidgetView.denseMonthHeader` exactly (column-aligned
    /// 3-letter month abbrevs; spacing already de-collided upstream by
    /// `monthLabelColumns`). Pure presentation.
    private func monthHeader(
        cols: [[GridCell]],
        metrics m: HabitsWidgetView.DenseGridMetrics,
        labels: [Int: String]
    ) -> some View {
        HStack(alignment: .bottom, spacing: m.gap) {
            ForEach(Array(cols.enumerated()), id: \.offset) { idx, _ in
                Text(labels[idx] ?? "")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize()
                    .frame(width: m.cell, alignment: .leading)
            }
        }
        .frame(height: HabitsWidgetView.monthHeaderHeight,
               alignment: .bottomLeading)
    }

    /// 7 fixed weekday rows × N week-columns at natural size. Every tile is
    /// a non-interactive rectangle — NO Button / onTapGesture / relay.
    private func gridBody(
        cols: [[GridCell]],
        metrics m: HabitsWidgetView.DenseGridMetrics,
        map: [String: MoodDayCell]
    ) -> some View {
        HStack(alignment: .top, spacing: m.gap) {
            ForEach(Array(cols.enumerated()), id: \.offset) { _, week in
                VStack(spacing: m.gap) {
                    ForEach(0..<7, id: \.self) { row in
                        if row < week.count {
                            tile(week[row], size: m.cell, map: map)
                        } else {
                            Color.clear.frame(width: m.cell,
                                               height: m.cell)
                        }
                    }
                }
            }
        }
        .frame(height: m.blockHeight, alignment: .topLeading)
    }

    @ViewBuilder
    private func tile(_ c: GridCell, size: CGFloat,
                      map: [String: MoodDayCell]) -> some View {
        if c.level < 0 || c.date.isEmpty {
            // weekday-alignment padding — not a real day.
            Color.clear.frame(width: size, height: size)
        } else {
            let mc = map[c.date]
            let logged = (mc?.count ?? 0) > 0
            let fill = (logged && !(mc?.colorHex.isEmpty ?? true))
                ? HabitsWidgetView.color(mc!.colorHex)
                : noLogColor
            RoundedRectangle(cornerRadius: 1.5)
                .fill(fill)
                .frame(width: size, height: size)
                .help(tooltip(c.date, mc))
                .accessibilityLabel(tooltip(c.date, mc))
        }
    }

    private func tooltip(_ iso: String, _ mc: MoodDayCell?) -> String {
        let pretty = HabitsWidgetView.prettyDate(iso) ?? iso
        guard let mc, mc.count > 0 else { return "\(pretty): no log." }
        let n = mc.count == 1 ? "1 log" : "\(mc.count) logs"
        return "\(pretty): mostly \(quadrantWord(mc.quadrant)) · \(n)."
    }

    // MARK: "mood now ← previous" row (render-only — logging is chat)
    //
    // A tinted strip above the grid, split: LEFT = previous mood
    // (de-emphasized + relative time), an arrow, RIGHT = the latest mood
    // (quadrant-colour token + name + freshness). When the latest log is
    // old it dims and shows a gentle "tell Iga in chat to refresh" hint —
    // there is NO button (the Mood surface is strictly read-only; logging
    // happens via the engine's record entry point from chat, by contract).

    private static let tsParser: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()

    private func tsDate(_ ts: String) -> Date? {
        Self.tsParser.date(from: ts)
            ?? Self.tsParser.date(from: ts + ":00")
    }

    /// Human freshness for a log timestamp. Civil, approximate — a hint,
    /// not a clock.
    private func freshness(_ ts: String) -> String {
        guard let d = tsDate(ts) else { return "" }
        let mins = Int(Date().timeIntervalSince(d) / 60)
        if mins < 0 { return "just now" }
        if mins < 60 { return mins <= 1 ? "just now" : "\(mins)m ago" }
        let cal = Calendar.current
        if cal.isDateInToday(d) { return "\(mins / 60)h ago" }
        if cal.isDateInYesterday(d) { return "yesterday" }
        let days = cal.dateComponents(
            [.day], from: cal.startOfDay(for: d),
            to: cal.startOfDay(for: Date())).day ?? 0
        return "\(days)d ago"
    }

    /// The latest log is "stale" once it's older than ~12 h (a mood is
    /// momentary; an old one may no longer reflect how you feel).
    private func isStale(_ ts: String) -> Bool {
        guard let d = tsDate(ts) else { return false }
        return Date().timeIntervalSince(d) > 12 * 3600
    }

    /// One equal-half feeling card, tinted with its own mood-meter
    /// quadrant colour. Centered name + freshness; previous is
    /// de-emphasized vs the latest. Pure presentation — no interactivity.
    private func feelingCard(_ r: MoodRecent, emphasized: Bool)
        -> some View {
        VStack(spacing: 2) {
            // One coloured dot PER feeling (primary + any secondary),
            // each in its own quadrant colour.
            HStack(spacing: 5) {
                ForEach(Array(r.parts.enumerated()), id: \.offset) {
                    idx, part in
                    if idx > 0 {
                        Text("·")
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                    Circle()
                        .fill(HabitsWidgetView.color(part.colorHex))
                        .frame(width: 7, height: 7)
                    Text(part.name)
                        .font(.system(
                            size: 12,
                            weight: emphasized ? .semibold : .regular))
                        .foregroundStyle(emphasized
                            ? Color.primary : Color.secondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                }
            }
            Text(freshness(r.ts))
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .padding(.horizontal, 6)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(cardGradient(r, emphasized: emphasized)))
        .opacity(emphasized ? 1 : 0.7)
    }

    /// Card background gradient. For a multi-feeling log it blends each
    /// feeling's quadrant colour left→right (same order as the dots), so
    /// the tint visibly reflects the double mood; a single feeling gets a
    /// subtle one-colour sheen. Same opacity scale as before.
    private func cardGradient(_ r: MoodRecent, emphasized: Bool)
        -> LinearGradient {
        let a = emphasized ? 0.18 : 0.11
        let cols = r.parts.map {
            HabitsWidgetView.color($0.colorHex).opacity(a)
        }
        let stops: [Color]
        if cols.count >= 2 {
            stops = cols
        } else {
            let base = cols.first
                ?? HabitsWidgetView.color(r.colorHex).opacity(a)
            stops = [base, base.opacity(0.45)]
        }
        return LinearGradient(colors: stops,
                              startPoint: .leading,
                              endPoint: .trailing)
    }

    @ViewBuilder
    private var moodNowRow: some View {
        if let latest = store.data.recent.first {
            let prev = store.data.recent.count > 1
                ? store.data.recent[1] : nil
            let stale = isStale(latest.ts)
            VStack(alignment: .leading, spacing: 4) {
                // Two EQUAL-width tinted feeling cards, the arrow dead
                // centre between them. No trailing label.
                HStack(spacing: 8) {
                    if let prev {
                        feelingCard(prev, emphasized: false)
                        Image(systemName: "arrow.right")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.secondary)
                    }
                    feelingCard(latest, emphasized: true)
                        .opacity(stale ? 0.7 : 1)
                }
                if stale {
                    Text("Tell Iga how you feel in chat to refresh this.")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    // MARK: legend + coach (verbatim engine text — app invents nothing)

    private func quadrantWord(_ q: String) -> String {
        switch q {
        case "yellow": return "high-energy pleasant"
        case "green":  return "calm pleasant"
        case "red":    return "high-energy unpleasant"
        case "blue":   return "low-energy unpleasant"
        default:       return "mixed"
        }
    }

    private var legend: some View {
        HStack(spacing: 10) {
            ForEach([("yellow", "Pleasant ↑"),
                     ("green", "Calm"),
                     ("red", "Stressed"),
                     ("blue", "Down")], id: \.0) { q, label in
                HStack(spacing: 4) {
                    RoundedRectangle(cornerRadius: 1.5)
                        .fill(HabitsWidgetView.color(
                            store.data.palette[q] ?? "#888"))
                        .frame(width: 8, height: 8)
                    Text(label)
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
    }

    private func coachLine(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "sparkles")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.purple)
                .padding(.top, 1)
            Text(text)
                .font(.caption2)
                .foregroundStyle(.primary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 8)
            .fill(Color.purple.opacity(0.08)))
    }
}
