import SwiftUI

// MARK: - The HabitKit-class multi-habit widget (Wave B) — RENDER + RELAY only
//
// Two tabbable modes (the user's spec):
//
//   • Compact (default) — one row per habit: icon + name, the last 7 days as
//     squares, the current streak, and a weekly-goal ring. iStat-Menus
//     density. The everyday glanceable view. This is the ONLY interactive
//     view: a square click relays to the sanctioned record entry point.
//
//   • Grid (dense) — a READ-ONLY HabitKit-style contribution chart: 7 FIXED
//     weekday rows × week-columns over the FULL history, small dense squares,
//     a thin month-label header, always horizontally scrollable with the
//     NEWEST columns at the trailing edge. There is NO period selector and
//     NO interactivity here — Grid never mutates anything (marking lives in
//     Compact only). HEIGHT-BOUNDED: it never grows taller, only wider.
//
// Every value shown — color, streak, longest, goal progress, each cell's
// level — was computed by the FROZEN engine and decoded read-only. This view
// computes NO habit logic. In Compact, clicking a square does not mutate
// anything here: it calls `store.relayToggle`, which relays to the single
// sanctioned record entry point; the engine mutates + re-emits the JSON and the
// poller refreshes. Grid issues no such call. Plain-language copy only
// (wife-test bar). Inverse habits render inverted semantics (a filled square
// = success = abstained).

struct HabitsWidgetView: View {
    // @Observable store: a plain `let` is correct — SwiftUI tracks the
    // property reads performed in `body` automatically (no @ObservedObject).
    let store: HabitsWidgetStore

    // Compact-mode cell metrics. `nonisolated` so the pure-geometry helpers
    // below (and ContractLitmus, which reads them off-actor) can reference
    // them without an actor hop — they are immutable value-type constants,
    // mirroring how `PanelController.columnWidth` is declared. The dense
    // (Grid) mode does NOT use these — it has its own fixed cell/gap below.
    nonisolated static let cell: CGFloat = 11
    nonisolated static let cellGap: CGFloat = 2
    private var cell: CGFloat { Self.cell }
    private var cellGap: CGFloat { Self.cellGap }

    // MARK: dense-grid metrics — GitHub / HabitKit fixed-cell model
    //
    // HISTORY: earlier implementations DERIVED the cell from the available
    // column width and, when the natural run fit, "filled" the column by
    // ballooning the inter-column gap to ~65pt — producing huge, scattered
    // squares. That whole derive-cell / slack-distribution / per-column-gap-
    // recompute mechanism was the bug and is DELETED and stays deleted.
    //
    // The model is exactly GitHub's / HabitKit's contribution chart: a FIXED
    // small cell (7) and a FIXED small gap (2), 7 weekday rows, N week-columns
    // over the FULL decoded history. The grid is ALWAYS rendered at its
    // NATURAL size inside a horizontal scroll view, anchored to the trailing
    // edge so the newest columns show first. It is NEVER stretched or width-
    // derived. There is no period gating — Grid shows everything.

    /// FIXED cell side, in points. Never derived, never clamped. (GitHub/
    /// HabitKit contribution-grid model — identical for every width.)
    nonisolated static let denseCell: CGFloat = 7
    /// FIXED inter-cell gap, in points (both axes). Never recomputed.
    nonisolated static let denseGap: CGFloat = 2
    /// 7 weekday rows — fixed, never grows with history length.
    nonisolated static let denseRows = 7

    /// Resolved geometry for one habit's dense grid. Everything is derived
    /// ONLY from the fixed `denseCell`/`denseGap` and the column count — the
    /// available width is informational only (Grid ALWAYS scrolls), never
    /// used to size cells or gaps.
    struct DenseGridMetrics: Equatable {
        let cell: CGFloat          // == denseCell, ALWAYS (fixed, 7)
        let gap: CGFloat           // == denseGap, ALWAYS (fixed, 2)
        let columns: Int           // N week-columns
        /// The inter-cell spacing actually used at RENDER time. It MUST
        /// always equal `gap` (== `denseGap`). It exists only so the layout
        /// test can prove there is no separate "render gap" — the stretch/
        /// scatter regression shipped 3× by setting this to ~65.
        let renderGap: CGFloat
        let blockHeight: CGFloat   // 7*7 + 6*2 == 61 (fixed)
        let naturalWidth: CGFloat  // columns*7 + (columns−1)*2
        let renderedWidth: CGFloat // == naturalWidth, ALWAYS (never stretched)
        let scrollsHorizontally: Bool // informational: naturalWidth > avail
    }

    /// Pure metric calc — NO running UI needed (the layout test calls this
    /// directly). FIXED cell + FIXED gap; `availableWidth` is purely
    /// informational now (Grid is ALWAYS in a horizontal ScrollView).
    ///
    ///   width  = columns*denseCell + (columns−1)*denseGap   (natural)
    ///   height = 7*denseCell + 6*denseGap = 61               (fixed)
    ///
    /// No derivation, no clamping, no slack distribution, no per-column gap
    /// recompute. Zero habit logic — pure geometry of already-decoded cells.
    nonisolated static func denseGridMetrics(
        availableWidth: CGFloat, columns: Int
    ) -> DenseGridMetrics {
        let n = max(1, columns)
        let nF = CGFloat(n)
        let cell = denseCell
        let gap = denseGap
        let naturalWidth = nF * cell + (nF - 1) * gap
        let blockHeight = cell * CGFloat(denseRows)
            + gap * CGFloat(denseRows - 1)
        return DenseGridMetrics(
            cell: cell,
            gap: gap,
            columns: n,
            renderGap: gap,                       // always == gap
            blockHeight: blockHeight,             // always 61
            naturalWidth: naturalWidth,
            renderedWidth: naturalWidth,          // never stretched
            scrollsHorizontally: naturalWidth > availableWidth)
    }

    /// Legacy compile/contract anchor: the COMPACT-mode height cap proof.
    /// Dense mode has its own fixed `blockHeight` (== 61) via
    /// `denseGridMetrics`. Kept so the existing ContractLitmus period-
    /// invariance assertion (cell*7 + cellGap*6 < 120) stays valid.
    nonisolated static func denseGridHeight() -> CGFloat {
        cell * 7 + cellGap * 6
    }

    /// The dense-grid content width inside the board column: the fixed
    /// column width minus the board's symmetric content padding (14pt each
    /// side) and the habit list's trailing inset (2pt). Informational only —
    /// Grid always scrolls horizontally regardless.
    nonisolated static func denseContentWidth() -> CGFloat {
        PanelController.columnWidth - 28 - 2
    }

    // MARK: month-label mapping — HabitKit / GitHub thin header
    //
    // Pure mapping from the laid-out week-columns to the column indices that
    // should display a 3-letter month abbrev. A column is labelled when it is
    // the FIRST column whose first DATED cell falls in a month different from
    // the previous column's first dated cell (the first column with any date
    // is always labelled). Unit-testable without UI.
    nonisolated static func monthLabelColumns(
        _ cols: [[GridCell]]
    ) -> [Int: String] {
        var out: [Int: String] = [:]
        var prevMonthKey: Int? = nil   // year*12 + month of prev labelled col
        for (idx, week) in cols.enumerated() {
            // The first cell in this column that carries a real date
            // (skip the leading -1 weekday-alignment padding cells).
            guard let dated = week.first(where: {
                $0.level >= 0 && !$0.date.isEmpty
            }), let d = prettyParser.date(from: dated.date) else { continue }
            var cal = Calendar(identifier: .iso8601)
            cal.timeZone = TimeZone(identifier: "UTC") ?? .current
            let comps = cal.dateComponents([.year, .month], from: d)
            guard let y = comps.year, let m = comps.month else { continue }
            let key = y * 12 + m
            if prevMonthKey != key {
                out[idx] = monthAbbrevFormatter.shortMonthSymbols[m - 1]
                prevMonthKey = key
            }
        }
        return out
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            headerRow
            if let reason = store.waitingReason, store.data.habits.isEmpty {
                Label(reason, systemImage: "hourglass")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .help(HelpText.habitsWaiting)
            } else {
                switch store.viewMode {
                case .compact: compactList
                case .dense:   denseList
                }
            }
        }
    }

    // MARK: header — title + mode tabs (Compact ↔ Grid only)

    private var headerRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                sectionHeader("Habits")
                Text("\(store.data.habits.count)")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .monospacedDigit()
                    .foregroundStyle(
                        store.data.habits.isEmpty
                            ? Color.secondary : Color.blue)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(
                        (store.data.habits.isEmpty
                            ? Color.secondary : Color.blue).opacity(0.12)))
                Spacer()
                modeTabs
            }
            .help(HelpText.habitsSection)
        }
    }

    private var modeTabs: some View {
        Picker("", selection: Binding(
            get: { store.viewMode },
            set: { store.viewMode = $0 })) {
            ForEach(HabitsViewMode.allCases, id: \.self) { m in
                Text(m.label).tag(m)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(width: 132)
        .help(HelpText.habitsModeTabs)
    }

    // MARK: compact mode — one row/habit, last 7 days, INTERACTIVE

    // NO inner ScrollView — the user's defect #2. The board owns the SINGLE
    // vertical scroll context; this list renders inline so there are never
    // two competing vertical scrollbars. The habit list scrolls as ONE
    // surface with everything else on the board. This is the ONLY mutating
    // surface: a square click relays via `store.relayToggle`.
    private var compactList: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let err = store.lastRelayError {
                Label(err, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .lineLimit(2)
                    .help(err)
            }
            ForEach(store.data.habits) { h in
                compactRow(h)
            }
        }
        .padding(.trailing, 2)
    }

    private func compactRow(_ h: HabitEntry) -> some View {
        let color = Self.color(h.colorHex)
        let last7 = Array(h.cells.suffix(7))
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                habitGlyph(h, color: color)
                Text(h.name)
                    .font(.caption)
                    .fontWeight(.medium)
                    .lineLimit(1)
                if h.isInverse {
                    Text("avoid")
                        .font(.system(size: 8, weight: .semibold))
                        .foregroundStyle(color)
                        .padding(.horizontal, 4)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(color.opacity(0.15)))
                        .help(HelpText.habitsInverse)
                }
                Spacer()
                streakChip(h, color: color)
                goalRing(h, color: color)
            }
            HStack(spacing: cellGap + 1) {
                ForEach(Array(last7.enumerated()), id: \.offset) { _, c in
                    square(habit: h, cell: c, color: color, size: cell + 3)
                }
                Text(weekdayHint(last7))
                    .font(.system(size: 8))
                    .foregroundStyle(.tertiary)
                    .padding(.leading, 2)
            }
            // Per-habit coach line — Compact ONLY. Deterministic text the
            // engine built (no LLM, no app logic); shown only when present
            // (old payloads decode it as nil → no line). One small,
            // truncated secondary line; Grid never renders this.
            if let coach = h.coach,
               !coach.trimmingCharacters(
                   in: .whitespacesAndNewlines).isEmpty {
                Text(coach)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(coach)
                    .accessibilityLabel(coach)
            }
        }
    }

    // MARK: one INTERACTIVE square — Compact only; click relays to the entry point

    // The ONLY mutating cell renderer. Used EXCLUSIVELY by `compactRow`.
    // Grid uses `denseSquare` (read-only) — it never reaches this code.
    private func square(
        habit h: HabitEntry, cell c: GridCell, color: Color, size: CGFloat
    ) -> some View {
        // A cell `level > 0` means the engine already decided this day is a
        // SUCCESS for this habit (inverse-aware: for an "avoid" habit a clean
        // day is success). We render that; we do not recompute it.
        let done = c.level > 0
        let pending = store.isPending(h.id, c.date)
        let fill = Self.fill(level: c.level, base: color, levels: h.levels)
        return RoundedRectangle(cornerRadius: 2)
            .fill(fill)
            .overlay(
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(
                        Color.primary.opacity(pending ? 0.45 : 0),
                        lineWidth: 1))
            .frame(width: size, height: size)
            .opacity(pending ? 0.5 : 1)
            .contentShape(Rectangle())
            .onTapGesture {
                store.relayToggle(
                    habitId: h.id, date: c.date, currentlyDone: done)
            }
            .help(tooltip(h, c, done: done, pending: pending))
            .accessibilityLabel(tooltip(h, c, done: done, pending: pending))
    }

    /// Plain-language tooltip — date, amount-ish state, inverse-aware, no
    /// jargon. The wife-test bar.
    private func tooltip(
        _ h: HabitEntry, _ c: GridCell, done: Bool, pending: Bool
    ) -> String {
        if pending { return "\(c.date) — saving…" }
        let pretty = Self.prettyDate(c.date) ?? c.date
        if h.isInverse {
            return done
                ? "\(pretty): stayed clean — nice. Click to undo."
                : "\(pretty): slipped (or not logged). Click to mark clean."
        }
        return done
            ? "\(pretty): done. Click to undo."
            : "\(pretty): not done yet. Click to mark it done."
    }

    // MARK: dense mode — READ-ONLY HabitKit chart, full history, scrolls

    // NO inner vertical ScrollView (defect #2). Each habit's dense grid
    // scrolls HORIZONTALLY only (a different axis — it does not compete with
    // the board's single vertical scroll). The list itself renders inline.
    // This whole subtree is READ-ONLY: no Button, no onTapGesture, no relay.
    private var denseList: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(store.data.habits) { h in
                denseRow(h)
            }
        }
        .padding(.trailing, 2)
    }

    private func denseRow(_ h: HabitEntry) -> some View {
        let color = Self.color(h.colorHex)
        // FULL decoded history — no period gating, no .suffix(...). The
        // engine emitted its window; Grid shows all of it. Pure presentation
        // slice — no level computed here.
        let cols = Self.weekColumns(h.cells)
        let labels = Self.monthLabelColumns(cols)
        let m = Self.denseGridMetrics(
            availableWidth: Self.denseContentWidth(),
            columns: cols.count)
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                habitGlyph(h, color: color)
                Text(h.name)
                    .font(.caption)
                    .fontWeight(.medium)
                    .lineLimit(1)
                Spacer()
                streakChip(h, color: color)
                goalRing(h, color: color)
            }
            // GitHub / HabitKit chart: ALWAYS a horizontal ScrollView at
            // NATURAL size from the pure, unit-tested `denseGridMetrics` —
            // FIXED small cell + FIXED small gap, month-label header on top,
            // anchored to the trailing edge so the NEWEST columns show first.
            // READ-ONLY: nothing in this subtree mutates state.
            denseChart(h, cols: cols, color: color,
                       metrics: m, labels: labels)
            legend(color: color, levels: h.levels, inverse: h.isInverse)
        }
    }

    /// The dense chart: a thin month-label header row aligned to columns,
    /// directly above the contribution grid, the whole thing inside a
    /// horizontal ScrollView anchored to the trailing (newest) edge.
    /// Cells are exactly `denseCell`×`denseCell`, gaps exactly `denseGap` on
    /// both axes — uniform, dense, even. NEVER stretched. READ-ONLY: this
    /// renders `denseSquare` (no gesture), never the interactive `square`.
    private func denseChart(
        _ h: HabitEntry, cols: [[GridCell]],
        color: Color, metrics m: DenseGridMetrics,
        labels: [Int: String]
    ) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            VStack(alignment: .leading, spacing: m.gap) {
                denseMonthHeader(cols: cols, metrics: m, labels: labels)
                denseGridBody(h, cols: cols, color: color, metrics: m)
            }
        }
        .frame(height: m.blockHeight + Self.monthHeaderHeight + m.gap)
        .defaultScrollAnchor(.trailing)
    }

    /// The thin month-label header. One slot per week-column of the grid's
    /// fixed width; a labelled column shows its 3-letter month abbrev, others
    /// an empty spacer so labels stay column-aligned. Pure presentation.
    private func denseMonthHeader(
        cols: [[GridCell]], metrics m: DenseGridMetrics,
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
        .frame(height: Self.monthHeaderHeight, alignment: .bottomLeading)
    }

    /// The contribution grid body — 7 fixed weekday rows × N week-columns at
    /// natural size. READ-ONLY: every cell is a non-interactive `denseSquare`
    /// (no Button, no onTapGesture, no relay). Marking lives in Compact only.
    private func denseGridBody(
        _ h: HabitEntry, cols: [[GridCell]],
        color: Color, metrics m: DenseGridMetrics
    ) -> some View {
        HStack(alignment: .top, spacing: m.gap) {
            ForEach(Array(cols.enumerated()), id: \.offset) { _, week in
                VStack(spacing: m.gap) {
                    ForEach(0..<7, id: \.self) { row in
                        if row < week.count {
                            denseSquare(habit: h, cell: week[row],
                                        color: color, size: m.cell)
                        } else {
                            Color.clear
                                .frame(width: m.cell, height: m.cell)
                        }
                    }
                }
            }
        }
        .frame(height: m.blockHeight, alignment: .topLeading)
    }

    // MARK: one READ-ONLY dense square — Grid only, NO interactivity
    //
    // Deliberately has NO Button, NO onTapGesture, NO relayToggle/runRecord.
    // Grid is a pure visual analytics chart; marking happens in Compact via
    // the interactive `square` above. This separation is asserted by the
    // ContractLitmus / DenseGridLayout read-only greps.
    private func denseSquare(
        habit h: HabitEntry, cell c: GridCell, color: Color, size: CGFloat
    ) -> some View {
        let fill = Self.fill(level: c.level, base: color, levels: h.levels)
        let done = c.level > 0
        return RoundedRectangle(cornerRadius: 1.5)
            .fill(fill)
            .frame(width: size, height: size)
            .help(denseTooltip(h, c, done: done))
            .accessibilityLabel(denseTooltip(h, c, done: done))
    }

    /// Read-only tooltip for a Grid cell — purely descriptive, no "click to"
    /// affordance (Grid never mutates).
    private func denseTooltip(
        _ h: HabitEntry, _ c: GridCell, done: Bool
    ) -> String {
        if c.level < 0 || c.date.isEmpty { return "" }
        let pretty = Self.prettyDate(c.date) ?? c.date
        if h.isInverse {
            return done
                ? "\(pretty): stayed clean."
                : "\(pretty): slipped (or not logged)."
        }
        return done ? "\(pretty): done." : "\(pretty): not done."
    }

    // MARK: small parts (all pure presentation of decoded values)

    private func habitGlyph(_ h: HabitEntry, color: Color) -> some View {
        Group {
            if let e = h.emoji, !e.isEmpty {
                Text(e).font(.caption)
            } else {
                Image(systemName: Self.sfSymbol(for: h.icon))
                    .font(.caption2)
                    .foregroundStyle(color)
            }
        }
        .frame(width: 14)
    }

    private func streakChip(_ h: HabitEntry, color: Color) -> some View {
        HStack(spacing: 3) {
            Image(systemName: h.currentStreak > 0
                ? "flame.fill" : "flame")
                .font(.system(size: 9))
            Text("\(h.currentStreak)")
                .font(.caption2)
                .fontWeight(.semibold)
                .monospacedDigit()
        }
        .foregroundStyle(h.currentStreak > 0 ? color : Color.secondary)
        .help(h.currentStreak > 0
            ? "\(h.currentStreak)-day streak (best ever: "
                + "\(h.longestStreak)). The engine counts this, not the app."
            : "No active streak. Best ever: \(h.longestStreak) days.")
    }

    // MARK: HabitKit-style segmented goal ring (pure, unit-tested geometry)
    //
    // One circular ring split into `target` equal arcs with a small gap
    // between segments, filled CLOCKWISE FROM TOP: `displayCount` segments
    // in the habit color, the remainder dim. `target == 1` (a Daily goal)
    // is a single FULL ring (no segmentation). When the engine says the
    // goal is `done` the whole ring is the habit color + a check. The app
    // computes NOTHING about the goal — `displayCount`/`target`/`done` all
    // come verbatim from the engine; this is pure geometry of those values.

    /// One ring segment's drawing spec: its start/end angle (degrees,
    /// 0° = 12 o'clock, growing clockwise) and whether it is filled.
    struct RingSegment: Equatable {
        let startDegrees: Double
        let endDegrees: Double
        let filled: Bool
    }

    /// Pure segment geometry. `target` arcs of equal sweep, separated by a
    /// `gapDegrees` visual gap, the first `filledCount` (clamped to
    /// 0...target) marked filled. `done` forces every segment filled (a
    /// completed goal is a solid ring regardless of raw count). `target<=1`
    /// → exactly one full segment (Daily = single unbroken ring). NO UI, NO
    /// habit logic — just maps already-decided counts to angles.
    nonisolated static func ringSegments(
        target: Int, filledCount: Int, done: Bool,
        gapDegrees: Double = 8
    ) -> [RingSegment] {
        let n = max(1, target)
        let filled = done ? n : min(max(0, filledCount), n)
        if n == 1 {
            // Single unbroken ring — no gap, no segmentation.
            return [RingSegment(
                startDegrees: 0, endDegrees: 360,
                filled: done || filled >= 1)]
        }
        // Effective gap can't consume the whole circle.
        let gap = min(max(0, gapDegrees), 360.0 / Double(n) * 0.6)
        let slot = 360.0 / Double(n)
        var out: [RingSegment] = []
        out.reserveCapacity(n)
        for i in 0..<n {
            let start = Double(i) * slot + gap / 2
            let end = Double(i + 1) * slot - gap / 2
            out.append(RingSegment(
                startDegrees: start, endDegrees: end,
                filled: i < filled))
        }
        return out
    }

    /// Weekly/period goal ring — only when there is an actual goal. A
    /// HabitKit-style segmented progress ring driven entirely by the
    /// engine-decided `displayCount`/`target`/`done`. Pure presentation.
    @ViewBuilder
    private func goalRing(_ h: HabitEntry, color: Color) -> some View {
        if h.goal.hasGoal, let target = h.goal.target, target > 0 {
            let segs = Self.ringSegments(
                target: target,
                filledCount: h.goal.displayCount,
                done: h.goal.done)
            ZStack {
                ForEach(Array(segs.enumerated()), id: \.offset) { _, s in
                    Circle()
                        .trim(
                            from: s.startDegrees / 360.0,
                            to: s.endDegrees / 360.0)
                        .stroke(
                            s.filled ? color : color.opacity(0.18),
                            style: StrokeStyle(
                                lineWidth: 3,
                                lineCap: segs.count == 1
                                    ? .round : .butt))
                        // Rotate so 0° is 12 o'clock, growing clockwise.
                        .rotationEffect(.degrees(-90))
                }
                Image(systemName: h.goal.done ? "checkmark" : "")
                    .font(.system(size: 7, weight: .bold))
                    .foregroundStyle(color)
            }
            .frame(width: 16, height: 16)
            .help(goalText(h))
        }
    }

    private func goalText(_ h: HabitEntry) -> String {
        let p: String = {
            switch h.goal.period {
            case "day":   return "today"
            case "week":  return "this week"
            case "month": return "this month"
            default:      return "this period"
            }
        }()
        let t = h.goal.target ?? 0
        if h.goal.done {
            return "Goal met \(p): \(h.goal.count)/\(t). "
                + "(Computed by the engine.)"
        }
        return "\(h.goal.count) of \(t) \(p) — "
            + "\(max(0, t - h.goal.count)) to go."
    }

    private func legend(
        color: Color, levels: Int, inverse: Bool
    ) -> some View {
        HStack(spacing: 4) {
            Text(inverse ? "slip" : "less")
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
            ForEach(0...levels, id: \.self) { l in
                RoundedRectangle(cornerRadius: 2)
                    .fill(Self.fill(level: l, base: color, levels: levels))
                    .frame(width: 9, height: 9)
            }
            Text(inverse ? "clean" : "more")
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
        }
        .help(inverse
            ? HelpText.habitsLegendInverse : HelpText.habitsLegend)
    }

    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption)
            .fontWeight(.semibold)
            .tracking(0.6)
            .foregroundStyle(.secondary)
    }

    // MARK: pure presentation helpers (NO habit logic)

    /// Lay decoded cells into GitHub-style week columns of 7 (weekday rows).
    /// Pure grouping — no level is computed; aligns the first column so each
    /// row is a fixed weekday (Mon…Sun by ISO weekday of the first date).
    static func weekColumns(_ cells: [GridCell]) -> [[GridCell]] {
        guard !cells.isEmpty else { return [] }
        var cols: [[GridCell]] = []
        var col: [GridCell] = []
        // Pad the first column so row index == weekday (Mon=0 … Sun=6).
        if let wd = isoWeekdayIndex(cells[0].date), wd > 0 {
            col = Array(
                repeating: GridCell(date: "", level: -1), count: wd)
        }
        for c in cells {
            col.append(c)
            if col.count == 7 { cols.append(col); col = [] }
        }
        if !col.isEmpty { cols.append(col) }
        return cols
    }

    /// Mon=0 … Sun=6 for an ISO date, or nil if unparizable. Pure date math
    /// for ROW ALIGNMENT only — not a habit decision.
    static func isoWeekdayIndex(_ iso: String) -> Int? {
        guard let d = prettyParser.date(from: iso) else { return nil }
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC") ?? .current
        // Calendar weekday: 1=Sun…7=Sat → map to Mon=0…Sun=6.
        let wd = cal.component(.weekday, from: d)
        return (wd + 5) % 7
    }

    nonisolated private static let prettyParser: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .iso8601)
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// Cached output formatter (fix #4) — was allocated per `prettyDate`
    /// call, which is on the tooltip/render hot path for every grid cell.
    private static let prettyPrinter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEE d MMM"
        return f
    }()

    /// Cached month-abbrev formatter for the dense chart's month header.
    /// `shortMonthSymbols` is locale-aware ("Jan" / "Lut" / …). Allocated
    /// once — the header maps over it for every labelled column.
    nonisolated private static let monthAbbrevFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM"
        return f
    }()

    /// Fixed height for the dense chart's thin month-label header row.
    nonisolated static let monthHeaderHeight: CGFloat = 11

    static func prettyDate(_ iso: String) -> String? {
        guard let d = prettyParser.date(from: iso) else { return nil }
        return prettyPrinter.string(from: d)
    }

    private func weekdayHint(_ cells: [GridCell]) -> String {
        guard let last = cells.last,
              let p = Self.prettyDate(last.date) else { return "" }
        return "…\(p)"
    }

    /// Concrete color from the engine-provided hex. Invents nothing — if the
    /// hex is malformed, fall back to the substrate-neutral indigo.
    static func color(_ hex: String) -> Color {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        if s.count == 3 {
            s = s.map { "\($0)\($0)" }.joined()
        }
        guard s.count == 6, let v = UInt64(s, radix: 16) else {
            return Color(red: 0.357, green: 0.357, blue: 0.839) // #5B5BD6
        }
        return Color(
            red: Double((v >> 16) & 0xFF) / 255.0,
            green: Double((v >> 8) & 0xFF) / 255.0,
            blue: Double(v & 0xFF) / 255.0)
    }

    /// Fill for an already-decided level. level<0 = padding (clear); level 0 =
    /// the empty tile; >0 = a ramp of the habit's own color. Frozen
    /// presentation mapping — the engine decided the level, never this code.
    static func fill(level: Int, base: Color, levels: Int) -> Color {
        if level < 0 { return Color.clear }
        if level == 0 { return Color.secondary.opacity(0.12) }
        let frac = Double(level) / Double(max(1, levels))
        return base.opacity(0.30 + 0.65 * frac)
    }

    /// Test-target-safe composition (no `Color` literal needed by callers):
    /// resolve the engine hex then map the engine-decided level to its fill.
    /// Pure presentation — still zero habit logic.
    static func levelColor(
        level: Int, hex: String, levels: Int
    ) -> Color {
        fill(level: level, base: color(hex), levels: levels)
    }

    /// Map a substrate icon name to an SF Symbol. Best-effort; an unknown
    /// name falls back to a neutral dot. Pure cosmetic mapping.
    static func sfSymbol(for icon: String?) -> String {
        switch (icon ?? "").lowercased() {
        case "book", "reading":      return "book.fill"
        case "leaf", "plant":        return "leaf.fill"
        case "dumbbell", "gym":      return "dumbbell.fill"
        case "drop", "water":        return "drop.fill"
        case "heart", "health":      return "heart.fill"
        case "clock", "time":        return "clock.fill"
        case "run", "running":       return "figure.run"
        case "moon", "sleep":        return "moon.fill"
        case "fork", "food":         return "fork.knife"
        case "pencil", "write":      return "pencil"
        case "star":                 return "star.fill"
        default:                     return "circle.fill"
        }
    }
}
