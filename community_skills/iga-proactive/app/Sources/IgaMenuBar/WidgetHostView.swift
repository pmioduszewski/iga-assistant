import SwiftUI

// MARK: - Widget host (render-only)
//
// Renders the discovered widgets purely from their data files. There is NO
// habit logic, NO grid computation here beyond pure layout of the cells the
// skill already computed: this view maps a `level` to a color and lays
// squares out in a calendar grid. Nothing here decides anything, writes
// anything, or runs anything. Delete this view (and the whole app) and the
// producer + engine keep working unchanged.

struct WidgetHostView: View {
    // @Observable store → plain `let` (SwiftUI auto-tracks body reads).
    let host: WidgetHostStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                sectionHeader("Widgets")
                Text("\(host.slots.count)")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .monospacedDigit()
                    .foregroundStyle(
                        host.slots.isEmpty ? Color.secondary : Color.blue)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(
                        (host.slots.isEmpty ? Color.secondary
                                            : Color.blue).opacity(0.12)))
            }
            .help(HelpText.widgetsSection)

            if host.slots.isEmpty {
                Text("No skill is showing a widget yet.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                // NO inner ScrollView — the board owns the SINGLE vertical
                // scroll context (Wave-C defect #2). Render inline so there
                // are never two competing vertical scrollbars.
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(host.slots) { slot in
                        widgetCard(slot)
                    }
                }
                .padding(.trailing, 2)
            }
        }
    }

    // MARK: one widget card

    @ViewBuilder
    private func widgetCard(_ slot: WidgetSlot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            switch slot.state {
            case .waiting(let reason):
                cardTitle(slot.spec.title, sub: slot.spec.skill)
                Label(reason, systemImage: "hourglass")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .help(HelpText.widgetWaiting)
            case .error(let msg):
                cardTitle(slot.spec.title, sub: slot.spec.skill)
                Label(msg, systemImage: "exclamationmark.triangle")
                    .font(.caption2)
                    .foregroundStyle(.orange)
                    .help(HelpText.widgetError)
            case .ready(let w):
                readyWidget(slot.spec, w)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.secondary.opacity(0.06)))
    }

    @ViewBuilder
    private func readyWidget(
        _ spec: RegisteredWidget, _ w: WidgetData) -> some View {
        cardTitle(w.title.isEmpty ? spec.title : w.title,
                  sub: spec.skill)
        switch w.kind {
        case .contributionGrid:
            if let g = w.grid {
                contributionGrid(g)
            } else {
                Text("(no grid data)")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        case .message:
            if let body = w.messageBody, !body.isEmpty {
                Text(body)
                    .font(.caption)
                    .foregroundStyle(.primary)
            }
        case .unknown(let t):
            Label("This version can't show a \"\(t)\" widget yet.",
                  systemImage: "questionmark.square.dashed")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .help(HelpText.widgetUnknownType)
        }
        if let coach = w.coach, !coach.text.isEmpty {
            coachLine(coach)
        }
    }

    // MARK: contribution grid — PURE LAYOUT of provided cells
    //
    // No computation that decides a level: the skill already put `level` in
    // each cell. We only choose a color for an already-decided level and lay
    // the squares out by week column. That's render, not logic.

    private func contributionGrid(_ g: ContributionGrid) -> some View {
        let weeks = Self.columns(from: g.cells)
        return VStack(alignment: .leading, spacing: 6) {
            if !g.label.isEmpty {
                Text(g.label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            HStack(alignment: .top, spacing: 3) {
                ForEach(Array(weeks.enumerated()), id: \.offset) { _, col in
                    VStack(spacing: 3) {
                        ForEach(0..<7, id: \.self) { row in
                            cellSquare(
                                row < col.count ? col[row] : nil,
                                levels: g.levels)
                        }
                    }
                }
            }
            legend(levels: g.levels)
        }
    }

    /// Lay cells into week columns (7 rows). Pure presentation grouping — no
    /// level is computed here; we only bucket the provided cells by week.
    static func columns(from cells: [GridCell]) -> [[GridCell]] {
        guard !cells.isEmpty else { return [] }
        var cols: [[GridCell]] = []
        var col: [GridCell] = []
        for c in cells {
            col.append(c)
            if col.count == 7 { cols.append(col); col = [] }
        }
        if !col.isEmpty { cols.append(col) }
        return cols
    }

    private func cellSquare(
        _ cell: GridCell?, levels: Int) -> some View {
        let tip: String = {
            guard let c = cell else { return "" }
            return "\(c.date) — "
                + (c.level == 0 ? "not done"
                                : "level \(c.level)/\(levels)")
        }()
        return RoundedRectangle(cornerRadius: 2)
            .fill(Self.color(for: cell?.level ?? -1, levels: levels))
            .frame(width: 10, height: 10)
            .help(tip)
    }

    /// Color ramp by level/levels. A frozen presentation mapping — green
    /// shades like a GitHub contribution graph; level 0 is the empty tile.
    static func color(for level: Int, levels: Int) -> Color {
        if level < 0 { return Color.clear }              // padding cell
        if level == 0 { return Color.secondary.opacity(0.14) }
        let frac = Double(level) / Double(max(1, levels))
        // 0.25 → 1.0 opacity ramp of a green; never fully transparent so a
        // level-1 day is still clearly "done".
        return Color.green.opacity(0.30 + 0.65 * frac)
    }

    private func legend(levels: Int) -> some View {
        HStack(spacing: 4) {
            Text("Less")
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
            ForEach(0...levels, id: \.self) { l in
                RoundedRectangle(cornerRadius: 2)
                    .fill(Self.color(for: l, levels: levels))
                    .frame(width: 9, height: 9)
            }
            Text("More")
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
        }
        .help(HelpText.widgetGridLegend)
    }

    // MARK: coach line — render only (text comes from the skill)

    private func coachLine(_ coach: WidgetCoach) -> some View {
        let accent: Color = {
            switch coach.tone {
            case "encouraging": return .green
            case "nudge":       return .orange
            default:            return .secondary
            }
        }()
        return HStack(alignment: .top, spacing: 6) {
            Image(systemName: coach.tone == "nudge"
                ? "hand.wave" : "sparkles")
                .font(.caption2)
                .foregroundStyle(accent)
            Text(coach.text)
                .font(.caption2)
                .italic()
                .foregroundStyle(.primary.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 2)
        .help(HelpText.widgetCoach)
    }

    // MARK: bits

    private func cardTitle(_ title: String, sub: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
                .font(.caption)
                .fontWeight(.semibold)
            Spacer()
            Text(sub)
                .font(.system(size: 9))
                .foregroundStyle(.secondary)
        }
    }

    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption)
            .fontWeight(.semibold)
            .tracking(0.6)
            .foregroundStyle(.secondary)
    }
}
