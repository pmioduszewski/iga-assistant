import SwiftUI

// MARK: - The compact multi-habit widget (Wave B) — RENDER + RELAY only
//
// Two tabbable modes (the user's spec):
//
//   • Compact (default) — one row per habit: icon + name, the last 7 days as
//     squares, the current streak, and a weekly-goal ring. iStat-Menus
//     density. The everyday glanceable view. This is the ONLY interactive
//     view: a square click relays to the sanctioned record seam.
//
//   • Grid (dense) — a READ-ONLY contribution chart: 7 FIXED
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
// sanctioned record seam; the engine mutates + re-emits the JSON and the
// poller refreshes. Grid issues no such call. Plain-language copy only
// (wife-test bar). Inverse habits render inverted semantics (a filled square
// = success = abstained).

struct HabitsWidgetView: View {
    // @Observable store: a plain `let` is correct — SwiftUI tracks the
    // property reads performed in `body` automatically (no @ObservedObject).
    let store: HabitsWidgetStore

    /// The habit whose ⋯ management sheet is open (nil = none). `HabitEntry`
    /// is Identifiable so `.sheet(item:)` drives presentation directly.
    @State private var manageTarget: HabitEntry?

    /// The (habit, day) whose quick-log drawer is open (nil = none).
    /// Set when a per-day-goal square is tapped.
    @State private var logTarget: HabitLogContext?

    /// The row currently hovered (by habit id) — drives the otherwise-dim
    /// settings cog brightening on hover (SwiftUI `.onHover`, the rough
    /// equivalent of CSS `:hover`).
    @State private var hoveredHabit: String?

    /// The habit whose coach-tip popover is open (hover the coach bubble).
    @State private var tipShownFor: String?

    /// Bottom "Archived (N)" collapsible expanded? (default collapsed —
    /// it's a recovery affordance, not everyday clutter).
    @State private var showArchived = false

    // Compact-mode cell metrics. `nonisolated` so the pure-geometry helpers
    // below (and ContractLitmus, which reads them off-actor) can reference
    // them without an actor hop — they are immutable value-type constants,
    // mirroring how `PanelController.columnWidth` is declared. The dense
    // (Grid) mode does NOT use these — it has its own fixed cell/gap below.
    nonisolated static let cell: CGFloat = 11
    nonisolated static let cellGap: CGFloat = 2
    private var cell: CGFloat { Self.cell }
    private var cellGap: CGFloat { Self.cellGap }

    // MARK: dense-grid metrics — GitHub-style fixed-cell model
    //
    // HISTORY: earlier implementations DERIVED the cell from the available
    // column width and, when the natural run fit, "filled" the column by
    // ballooning the inter-column gap to ~65pt — producing huge, scattered
    // squares. That whole derive-cell / slack-distribution / per-column-gap-
    // recompute mechanism was the bug and is DELETED and stays deleted.
    //
    // The model is exactly GitHub-style contribution chart: a FIXED
    // small cell (7) and a FIXED small gap (2), 7 weekday rows, N week-columns
    // over the FULL decoded history. The grid is ALWAYS rendered at its
    // NATURAL size inside a horizontal scroll view, anchored to the trailing
    // edge so the newest columns show first. It is NEVER stretched or width-
    // derived. There is no period gating — Grid shows everything.

    /// FIXED cell side, in points. Never derived, never clamped. (GitHub/
    /// GitHub-style contribution-grid model — identical for every width.)
    nonisolated static let denseCell: CGFloat = 7
    /// FIXED inter-cell gap, in points (both axes). Never recomputed.
    nonisolated static let denseGap: CGFloat = 2
    /// 7 weekday rows — fixed, never grows with history length.
    nonisolated static let denseRows = 7

    /// Minimum columns between two month labels. A 3-letter month at
    /// caption2 (~22pt) is far wider than one column (denseCell+denseGap =
    /// 9pt); without a gap, sparse left-edge months collide ("AugSep").
    /// 3 cols ≈ 27pt clearance — enough for "Sep" without overlap.
    nonisolated static let monthLabelMinCols = 3

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

    /// Fill-to-width done RIGHT: when the real history is too short to span
    /// the available width (a recently-started habit), BACKFILL earlier
    /// real-dated empty day cells (level 0) BEFORE the first real day —
    /// whole weeks at a time so weekday alignment is preserved exactly and
    /// the series stays ONE continuous dated calendar. Result: the grid
    /// fills the width, month abbreviations still label correctly (the
    /// backfilled cells carry real earlier dates, so older months appear),
    /// and there is NO seam/blank column between the empty and the real
    /// data (it's all one contiguous run). Newest day stays at the
    /// trailing edge. Cells/gaps stay FIXED — only days are prepended,
    /// never stretched (the "balloon the gap" regression stays dead).
    /// History already wide enough → returned UNCHANGED (scrolls as
    /// before). Pure date geometry; unit-tested.
    nonisolated static func fillCells(
        _ cells: [GridCell], availableWidth: CGFloat
    ) -> [GridCell] {
        guard let first = cells.first,
              let d0 = prettyParser.date(from: first.date)
        else { return cells }
        let cell = denseCell, gap = denseGap
        let fit = Int(((availableWidth + gap) / (cell + gap))
            .rounded(.down))
        let have = weekColumns(cells).count
        let need = max(1, fit) - have
        guard need > 0 else { return cells }
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC") ?? .current
        // Exactly `need` whole weeks earlier → +need columns, same weekday
        // alignment (a multiple of 7 days back lands on the same weekday).
        let extraDays = need * 7
        var pre: [GridCell] = []
        pre.reserveCapacity(extraDays)
        for k in stride(from: extraDays, through: 1, by: -1) {
            guard let d = cal.date(
                byAdding: .day, value: -k, to: d0) else { continue }
            pre.append(GridCell(
                date: prettyParser.string(from: d), level: 0))
        }
        return pre + cells
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

    // MARK: month-label mapping — the tracker / GitHub thin header
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
        var prevMonthKey: Int? = nil   // year*12 + month of prev SEEN month
        var lastLabelIdx = -monthLabelMinCols   // allow a label at idx 0
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
                // New month. Emit its label ONLY if there's horizontal
                // room since the last emitted one — a 3-letter month is
                // far wider than one column, so back-to-back months at the
                // sparse (left/backfilled) edge would collide ("AugSep").
                // Skipped months simply go unlabelled (GitHub does this);
                // month tracking still advances so the next roomy month
                // gets its label.
                if idx - lastLabelIdx >= monthLabelMinCols {
                    out[idx] =
                        monthAbbrevFormatter.shortMonthSymbols[m - 1]
                    lastLabelIdx = idx
                }
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
    /// The engine-decided "too many habits" advisory, rendered ONCE below
    /// the last habit and ONLY when the engine says `show`. Tinted card,
    /// coherent with the coach bubble (rounded + tinted), a calm amber
    /// "graduate/archive" affordance — never a hard red alarm. The app
    /// renders the engine's message verbatim; it computes nothing.
    @ViewBuilder
    private var focusAdvisory: some View {
        if let f = store.data.focus, f.show,
           !f.message.trimmingCharacters(
               in: .whitespacesAndNewlines).isEmpty {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: "tray.and.arrow.down.fill")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.orange)
                    .padding(.top, 1)
                Text(f.message)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.orange.opacity(0.10)))
            .help(f.message)
            .accessibilityLabel(f.message)
            .padding(.top, 2)
        }
    }

    /// A subtle bottom collapsible so archiving isn't a one-way trap:
    /// "Archived (N)" line → expands to each archived habit with a Restore
    /// (unarchive) action. Hidden entirely when nothing is archived.
    @ViewBuilder
    private var archivedDisclosure: some View {
        let arc = store.data.archived
        if !arc.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Divider().padding(.vertical, 2)
                Button {
                    withAnimation(.easeInOut(duration: 0.12)) {
                        showArchived.toggle()
                    }
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: showArchived
                              ? "chevron.down" : "chevron.right")
                            .font(.system(size: 8, weight: .semibold))
                        Text("Archived (\(arc.count))")
                            .font(.caption2)
                            .fontWeight(.semibold)
                            .tracking(0.5)
                        Spacer()
                    }
                    .foregroundStyle(.tertiary)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Graduated/archived habits — history kept. "
                      + "Restore to bring one back to the active list.")

                if showArchived {
                    ForEach(arc) { a in
                        HStack(spacing: 8) {
                            Circle()
                                .fill(Self.color(a.colorHex))
                                .frame(width: 8, height: 8)
                            Text(a.name)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                            Spacer(minLength: 6)
                            Button("Restore") {
                                store.relayManage(
                                    habitId: a.id,
                                    op: .setArchived(false))
                            }
                            .font(.caption2)
                            .buttonStyle(.plain)
                            .foregroundStyle(.blue)
                            .disabled(store.managePending)
                            .help("Unarchive — back to the active list")
                            .accessibilityLabel("Restore \(a.name)")
                        }
                        .padding(.leading, 4)
                    }
                }
            }
        }
    }

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
            focusAdvisory
            archivedDisclosure
        }
        .padding(.trailing, 2)
        .sheet(item: $manageTarget) { h in
            HabitManageSheet(
                habit: h, store: store,
                onClose: { manageTarget = nil })
        }
        .sheet(item: $logTarget) { ctx in
            HabitLogDrawer(
                context: ctx, store: store,
                onClose: { logTarget = nil })
        }
    }

    /// The per-row settings cog (dim at rest, brightens on row hover).
    /// SHARED by Compact and Grid. It opens the manage SHEET — it does NOT
    /// mark/relay a completion, so Grid stays read-only for marking while
    /// still exposing settings (the contract is "no marking in Grid", not
    /// "no affordances"). Deliberately NOT named `dense*` so the read-only
    /// grep stays meaningful (it bans marking in dense members; this is
    /// not marking).
    private func settingsCog(_ h: HabitEntry) -> some View {
        Button {
            manageTarget = h
        } label: {
            Image(systemName: "gearshape.fill")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .opacity(hoveredHabit == h.id ? 1.0 : 0.28)
        }
        .buttonStyle(.plain)
        .help("Manage “\(h.name)” — rename, goal, colour, "
              + "archive, delete, backup")
        .accessibilityLabel("Manage \(h.name)")
    }

    private func compactRow(_ h: HabitEntry) -> some View {
        let color = Self.color(h.colorHex)
        let last7 = Self.compactWindow(
            cells: h.cells, todayISO: HabitsWidgetStore.systemTodayISO())
        return VStack(alignment: .leading, spacing: 4) {
            // TOP row: LEFT = just the title (+ inverse badge) + the dim
            // settings cog. RIGHT = streak · goal ring, then the weekday
            // labels + 7-day grid flush RIGHT (grid lines up across rows
            // for fast scanning). Streak now sits immediately BEFORE the
            // grid, not on the title. No colour glyph — the grid's own
            // colour signals the habit.
            HStack(alignment: .center, spacing: 8) {
                HStack(spacing: 6) {
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
                            .background(Capsule()
                                .fill(color.opacity(0.15)))
                            .help(HelpText.habitsInverse)
                    }
                    settingsCog(h)
                }
                Spacer(minLength: 8)
                // Streak + goal ring align to the GRID row (items-end),
                // NOT centered over the weekday-label band — so they sit
                // strictly level with the 7-day squares.
                HStack(alignment: .bottom, spacing: 8) {
                    streakChip(h, color: color)
                    goalRing(h, color: color)
                    VStack(alignment: .trailing, spacing: 2) {
                        weekdayLabels(last7)
                        HStack(spacing: cellGap + 1) {
                            ForEach(
                                Array(last7.enumerated()), id: \.offset
                            ) { _, c in
                                square(habit: h, cell: c,
                                       color: color, size: cell + 3)
                            }
                        }
                    }
                }
            }
            // SEPARATE full-width row UNDER everything (incl. under the
            // grid). Compact ONLY, SALIENT-ONLY (the engine emits a line
            // only at a decision point; a cruising habit → nil/empty → NO
            // row). Content-width tinted bubble, leading — free to extend
            // under the grid since it's no longer boxed in the left column.
            if let coach = h.coach,
               !coach.trimmingCharacters(
                   in: .whitespacesAndNewlines).isEmpty {
                HStack(spacing: 0) {
                    Spacer(minLength: 0)        // push the bubble RIGHT,
                    HStack(alignment: .center,  // closer to the grid
                           spacing: 5) {
                        Image(systemName:
                                Self.coachSymbol(h.coachKind))
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(color)
                        Text(coach)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .fixedSize(
                                horizontal: false, vertical: true)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 6)
                    .background(
                        RoundedRectangle(cornerRadius: 6)
                            .fill(color.opacity(0.08)))
                    .help(coach)
                    .accessibilityLabel(coach)
                    .onHoverDelayed { stable in
                        // DWELL → the Atomic-Habits "why" popover (only
                        // when the engine sent a tip); leaving hides it
                        // immediately. The delay kills the cross-the-
                        // bubble flicker the user reported.
                        if h.coachTip != nil {
                            tipShownFor = stable ? h.id
                                : (tipShownFor == h.id
                                   ? nil : tipShownFor)
                        }
                    }
                    .popover(isPresented: Binding(
                        get: { tipShownFor == h.id
                               && h.coachTip != nil },
                        set: { if !$0 { tipShownFor = nil } }),
                        arrowEdge: .bottom
                    ) {
                        coachTipPopover(
                            line: coach, tip: h.coachTip ?? "",
                            kind: h.coachKind, color: color)
                    }
                }
            }
        }
        .contentShape(Rectangle())
        .onHover { inside in
            withAnimation(.easeInOut(duration: 0.12)) {
                if inside {
                    hoveredHabit = h.id
                } else if hoveredHabit == h.id {
                    hoveredHabit = nil
                }
            }
        }
    }

    /// The coach hover popover — the longer Atomic-Habits "why", styled
    /// COHERENTLY with the inline bubble (same tinted bg + sparkles mark):
    /// the short line bold on top, the principle below. Engine-authored
    /// text only (render-only).
    private func coachTipPopover(
        line: String, tip: String, kind: String?, color: Color
    ) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: Self.coachSymbol(kind))
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(color)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 5) {
                Text(line)
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(.primary)
                Text(tip)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("— James Clear, Atomic Habits")
                    .font(.system(size: 9))
                    .foregroundStyle(.tertiary)
            }
        }
        .padding(12)
        .frame(width: 280)
        .background(color.opacity(0.10))
    }

    /// The 3 weekday labels above the grid, aligned to squares 0 / 3 / 6
    /// (today-6 · today-3 · today). Each label sits in a slot the SAME
    /// width + spacing as a grid square so it centres over its column;
    /// empty slots elsewhere keep the alignment. Pure date presentation.
    /// Fixed height for the weekday header band. WITHOUT a fixed height the
    /// empty `Color.clear` slots are flexible and the row stretches to the
    /// (tall, when a coach bubble shows) row height — floating the labels
    /// far above the grid. Pinning the band keeps labels tight to the grid.
    private static let weekdayLabelHeight: CGFloat = 11

    private func weekdayLabels(_ cells: [GridCell]) -> some View {
        HStack(spacing: cellGap + 1) {
            ForEach(0..<7, id: \.self) { i in
                Group {
                    if (i == 0 || i == 3 || i == 6),
                       i < cells.count {
                        Text(Self.weekdayAbbrev(cells[i].date))
                    } else {
                        Color.clear
                    }
                }
                .font(.system(size: 8))
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .minimumScaleFactor(0.5)
                .frame(width: cell + 3, height: Self.weekdayLabelHeight)
            }
        }
        .frame(height: Self.weekdayLabelHeight)
    }

    // MARK: one INTERACTIVE square — Compact only; click relays to the seam

    // The ONLY mutating cell renderer. Used EXCLUSIVELY by `compactRow`.
    // Grid uses `denseSquare` (read-only) — it never reaches this code.
    /// How ONE day-square should be drawn. Pure mapping of already-decided
    /// engine values (level / raw amount / per-day target) to a render
    /// choice — NO habit logic (it computes no streak/goal/level; it only
    /// chooses a shape for values the engine already decided), exactly like
    /// `ringSegments`/`fill`.
    enum SquareStyle: Equatable {
        case flat                                  // binary/period — flat
        case solid                                 // day met its target
        case ringSegmented(target: Int, filled: Int)   // small goal
        case ringContinuous(progress: Double)          // large goal
    }

    /// Above this per-day target a discrete segment ring stops reading as a
    /// ring (too many thin arcs = a hairball); switch to a continuous
    /// percentage arc instead. At/below it, the segmented ring is the nicer,
    /// countable look.
    nonisolated static let segmentRingMax = 10

    /// The advanced +/- log drawer is for BIG per-day targets only (the
    /// continuous-ring habits, target > `segmentRingMax`). Small targets
    /// (segmented ring) and binary habits iterate by tap instead — no
    /// dialog. Pure threshold; unit-tested. Kept aligned with
    /// `squareStyle` so the interaction matches the visual.
    nonisolated static func usesLogDrawer(_ perDayTarget: Int?) -> Bool {
        (perDayTarget ?? 0) > segmentRingMax
    }

    /// Per-day-goal square rendering. A habit with a per-DAY target > 1:
    ///   • target 2…`segmentRingMax`  → a SEGMENTED ring (one arc per unit,
    ///     `filled` of them in colour) — the clean countable look;
    ///   • target > `segmentRingMax`  → a CONTINUOUS arc (amount/target),
    ///     because >10 thin segments is unreadable;
    ///   • amount ≥ target (or the engine bucketed the day to a success
    ///     level) → SOLID;
    /// a binary / period-only / pre-ring habit keeps the flat fill.
    /// Deterministic & pure; unit-tested directly.
    nonisolated static func squareStyle(
        level: Int, levels: Int, amount: Int?, perDayTarget: Int?
    ) -> SquareStyle {
        guard let raw = perDayTarget, raw > 1 else { return .flat }
        let a = max(0, amount ?? 0)
        if a >= raw || level >= levels { return .solid }
        if raw <= segmentRingMax {
            // 0 → an empty (all-dim) ring outline; partial → that many arcs.
            return .ringSegmented(target: raw, filled: min(a, raw))
        }
        // Large target: a proportional arc (strictly < 1 here, a < raw).
        let progress = min(0.999, max(0.0, Double(a) / Double(raw)))
        return .ringContinuous(progress: progress)
    }

    private func square(
        habit h: HabitEntry, cell c: GridCell, color: Color, size: CGFloat
    ) -> some View {
        // A cell `level > 0` means the engine already decided this day is a
        // SUCCESS for this habit (inverse-aware: for an "avoid" habit a clean
        // day is success). We render that; we do not recompute it.
        let done = c.level > 0
        let pending = store.isPending(h.id, c.date)
        let fill = Self.fill(level: c.level, base: color, levels: h.levels)
        let style = Self.squareStyle(
            level: c.level, levels: h.levels,
            amount: c.amount, perDayTarget: h.goal.perDayTarget)
        return Group {
            switch style {
            case .flat:
                RoundedRectangle(cornerRadius: 2).fill(fill)
            case .solid:
                // Full-intensity solid: the day met its per-day target.
                RoundedRectangle(cornerRadius: 2)
                    .fill(Self.fill(
                        level: max(c.level, h.levels),
                        base: color, levels: h.levels))
            case let .ringSegmented(target, filledCount):
                ZStack {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color.secondary.opacity(0.10))
                    perDayRingSegmented(
                        target: target, filled: filledCount,
                        color: color, size: size)
                }
            case let .ringContinuous(progress):
                ZStack {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Color.secondary.opacity(0.10))
                    perDayRingContinuous(
                        progress: progress, color: color, size: size)
                }
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 2)
                .strokeBorder(
                    Color.primary.opacity(pending ? 0.45 : 0),
                    lineWidth: 1))
        .frame(width: size, height: size)
        .opacity(pending ? 0.5 : 1)
        .contentShape(Rectangle())
        .onTapGesture {
            // ONLY a big per-day target (> segmentRingMax, the continuous-
            // ring habits) opens the advanced +/- drawer. A small target
            // (2…segmentRingMax, the segmented-ring habits) and a binary
            // habit just relay a tap: the engine iterates the amount +1
            // each click (1,2,3 …) and a tap on a done day clears it — no
            // dialog needed for those.
            if Self.usesLogDrawer(h.goal.perDayTarget) {
                logTarget = HabitLogContext(habit: h, date: c.date)
            } else {
                store.relayToggle(
                    habitId: h.id, date: c.date, currentlyDone: done)
            }
        }
        .help(tooltip(h, c, done: done, pending: pending))
        .accessibilityLabel(tooltip(h, c, done: done, pending: pending))
    }

    /// SEGMENTED per-day ring (small targets, 2…`segmentRingMax`): one arc
    /// per unit, `filled` of them in the habit colour and the rest dim —
    /// the clean, countable look. Reuses the same unit-tested
    /// `ringSegments` geometry as the weekly goal ring. Pure presentation.
    private func perDayRingSegmented(
        target: Int, filled: Int, color: Color, size: CGFloat
    ) -> some View {
        let segs = Self.ringSegments(
            target: target, filledCount: filled, done: false)
        let lw: CGFloat = max(1.4, size * 0.16)
        return ZStack {
            ForEach(Array(segs.enumerated()), id: \.offset) { _, s in
                Circle()
                    .trim(
                        from: s.startDegrees / 360.0,
                        to: s.endDegrees / 360.0)
                    .stroke(
                        s.filled ? color : color.opacity(0.20),
                        style: StrokeStyle(
                            lineWidth: lw,
                            lineCap: segs.count == 1 ? .round : .butt))
                    .rotationEffect(.degrees(-90))
            }
        }
        .padding(size * 0.16)
    }

    /// CONTINUOUS per-day ring (large targets, > `segmentRingMax`): one arc
    /// filled to `progress` (= amount/target) on a faint full-circle track,
    /// because >10 thin segments is an unreadable hairball. `progress == 0`
    /// → just the dim track (an empty goal day, still tappable). Pure
    /// presentation.
    private func perDayRingContinuous(
        progress: Double, color: Color, size: CGFloat
    ) -> some View {
        let lw: CGFloat = max(1.4, size * 0.16)
        let p = min(max(progress, 0), 1)
        return ZStack {
            Circle()
                .stroke(color.opacity(0.18),
                        style: StrokeStyle(lineWidth: lw))
            Circle()
                .trim(from: 0, to: p)
                .stroke(color,
                        style: StrokeStyle(
                            lineWidth: lw, lineCap: .round))
                .rotationEffect(.degrees(-90))   // start at 12 o'clock
        }
        .padding(size * 0.16)
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

    // MARK: dense mode — READ-ONLY contribution chart, full history, scrolls

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
        let cols = Self.weekColumns(
            Self.fillCells(
                h.cells, availableWidth: Self.denseContentWidth()))
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
                settingsCog(h)
                Spacer()
                streakChip(h, color: color)
                goalRing(h, color: color)
            }
            // GitHub-style contribution chart: ALWAYS a horizontal ScrollView at
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

    // MARK: segmented goal ring (pure, unit-tested geometry)
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
    /// segmented progress ring driven entirely by the
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
                                lineWidth: 2,
                                lineCap: segs.count == 1
                                    ? .round : .butt))
                        // Rotate so 0° is 12 o'clock, growing clockwise.
                        .rotationEffect(.degrees(-90))
                }
                Image(systemName: h.goal.done ? "checkmark" : "")
                    .font(.system(size: 6, weight: .bold))
                    .foregroundStyle(color)
            }
            .frame(width: 11, height: 11)
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

    /// The Compact strip's visible window: the last `days` calendar dates
    /// ending at `todayISO` (the system "today"), each carrying the
    /// engine-decided level for that date. A date the engine did not emit a
    /// cell for → level 0, which IS the engine's own meaning of "not done"
    /// (and inverse-aware "not yet clean") — this invents no semantics.
    ///
    /// WHY this is not just `cells.suffix(days)`: the engine bakes its own
    /// `today` into the projection. On a cold launch (Mac restart, no
    /// scan/record since yesterday) that window ends YESTERDAY, so the
    /// rightmost square was yesterday, not today; clicking it recorded
    /// yesterday and the side-effect re-projection then shifted the whole
    /// strip — the "jump" that consumed the click. Anchoring to the system
    /// date makes today ALWAYS the rightmost and ALWAYS clickable (mapped to
    /// the true calendar date) regardless of how stale the engine file is —
    /// the reproject just catches the engine-computed numbers up.
    ///
    /// Pure date-axis presentation + a date→level lookup. NO habit logic:
    /// the level is the engine's; absence is the engine's own "not done".
    /// Deterministic with an injected `todayISO` (unit-tested directly).
    static func compactWindow(
        cells: [GridCell], todayISO: String, days: Int = 7
    ) -> [GridCell] {
        let n = max(1, days)
        // Defensive: an unparseable today must never crash or blank the
        // strip — fall back to the engine's own trailing window.
        guard let today = prettyParser.date(from: todayISO) else {
            return Array(cells.suffix(n))
        }
        // Map the WHOLE engine cell by date — level AND amount. Earlier
        // this kept only `level`, which silently zeroed `amount` for every
        // Compact square; the per-day ring is driven by `amount`, so it
        // could never fill even after the engine recorded the click (the
        // streak — a different field — updated, the square did not). A date
        // the engine never emitted → a genuine 0/0 cell (its own "not
        // done"), still clickable as that day.
        var cellByDate: [String: GridCell] = [:]
        cellByDate.reserveCapacity(cells.count)
        for c in cells where !c.date.isEmpty {
            cellByDate[c.date] = c
        }
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC") ?? .current
        var out: [GridCell] = []
        out.reserveCapacity(n)
        // Oldest → newest, ending at today (inclusive).
        for back in stride(from: n - 1, through: 0, by: -1) {
            guard let d = cal.date(
                byAdding: .day, value: -back, to: today) else { continue }
            let iso = prettyParser.string(from: d)
            out.append(
                cellByDate[iso] ?? GridCell(date: iso, level: 0, amount: 0))
        }
        return out
    }

    /// Lay decoded cells into GitHub-style week columns of 7 (weekday rows).
    /// Pure grouping — no level is computed; aligns the first column so each
    /// row is a fixed weekday (Mon…Sun by ISO weekday of the first date).
    nonisolated static func weekColumns(
        _ cells: [GridCell]
    ) -> [[GridCell]] {
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
    nonisolated static func isoWeekdayIndex(_ iso: String) -> Int? {
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

    /// Cached short-weekday formatter (UTC, to match the engine's UTC civil
    /// cell dates) — "Mon"/"Tue"/… Allocated once; the Compact weekday
    /// header maps a few dates per row.
    nonisolated private static let weekdayAbbrevFormatter: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .iso8601)
        f.timeZone = TimeZone(identifier: "UTC")
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "EEE"
        return f
    }()

    /// Short weekday abbrev ("Mon") for an ISO `yyyy-MM-dd` (UTC civil —
    /// same convention the grid cells use). "" if unparseable. Pure date
    /// presentation; deterministic; unit-tested.
    nonisolated static func weekdayAbbrev(_ iso: String) -> String {
        guard let d = prettyParser.date(from: iso) else { return "" }
        return weekdayAbbrevFormatter.string(from: d)
    }

    static func prettyDate(_ iso: String) -> String? {
        guard let d = prettyParser.date(from: iso) else { return nil }
        return prettyPrinter.string(from: d)
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

    /// The coach mark: the AI "sparkles" glyph (the conventional
    /// assistant/AI affordance). One mark for every nudge — the KIND is
    /// still carried in the data (`coach_kind`) for future colour/theming,
    /// but the icon itself is the AI sparkle, not a per-kind pictogram.
    /// Pure cosmetic mapping (kept kind-parameterised so theming can branch
    /// later without touching call sites).
    nonisolated static func coachSymbol(_: String?) -> String {
        "sparkles"
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
