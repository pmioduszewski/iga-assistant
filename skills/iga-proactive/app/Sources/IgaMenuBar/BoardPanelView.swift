import SwiftUI

// MARK: - The right board (Wave C) — RENDER + RELAY only
//
// Hosted as the RIGHT column of `PanelController`'s single borderless
// NSPanel (the left column is `FundamentalsView`; this is to its RIGHT,
// edge-to-edge, tops aligned — see PanelController). This is the
// DOUBLED-space widget board: it carries everything domain-ish that used to
// be crammed into the left popover —
//
//   • the multi-habit widget (HabitsWidgetView, compact + dense modes)
//   • the generic widget host (WidgetHostView)
//   • the proactive-research surfacing, MIGRATED here as a board widget card
//     (it no longer gets any special left-popover treatment)
//   • the email triage status section (last-run + status + Run now button)
//
// SINGLE SCROLL CONTEXT (the user's defect #2): the board owns exactly ONE
// vertical ScrollView. The habit/widget child views were changed to NOT nest
// their own vertical ScrollView, so there are never competing scrollbars. The
// dense habit grid still scrolls HORIZONTALLY only — that is a different axis,
// not a nested vertical scroll, so it does not compete.
//
// CONTRACT: pure presentation. No Process, no write, no JSON encode, no
// sqlite. The only mutation path is a habit-square click, which the habit
// view relays through the single `ContractGuard.runRecord` entry point exactly as
// before — unchanged. The "Run now" button relays to EmailTriageWatcher which
// owns the entry point call. Deleting the app removes this board; `/gm` keeps
// working. ContractLitmus greps this file too (blanket + explicit assertion).

struct BoardPanelView: View {
    // @Observable stores → plain `let` (SwiftUI auto-tracks body reads).
    // Injected via init params ONLY (the redundant .environmentObject path
    // is removed — see PanelController, fix #5).
    let host: WidgetHostStore
    let habits: HabitsWidgetStore
    let mood: MoodWidgetStore
    let moodSync: MoodIngestWatcher
    let emailTriage: EmailTriageWatcher
    let store: StateStore
    let onClose: () -> Void

    private let sectionGap: CGFloat = 12

    var body: some View {
        VStack(spacing: 0) {
            boardHeader
            Divider()
            // THE single scroll context for the whole board. Children render
            // their content inline (no inner vertical ScrollView), so this is
            // the only vertical scrollbar that can ever appear.
            ScrollView(.vertical) {
                VStack(alignment: .leading, spacing: sectionGap) {
                    MoodWidgetView(store: mood, sync: moodSync)
                    Divider()
                    HabitsWidgetView(store: habits)
                    Divider()
                    emailTriageSection
                    Divider()
                    WidgetHostView(host: host)
                    if let s = store.state.surface, !s.lines.isEmpty {
                        Divider()
                        researchSurfacingWidget(s)
                    }
                }
                .padding(14)
            }
        }
        .frame(width: PanelController.columnWidth)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(.regularMaterial)
    }

    // MARK: header — title + close

    private var boardHeader: some View {
        HStack(spacing: 8) {
            Image(systemName: "square.grid.2x2.fill")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("Board")
                .font(.headline)
            Text("habits · widgets · research")
                .font(.caption2)
                .foregroundStyle(.secondary)
            Spacer()
            Button {
                onClose()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.secondary)
                    .padding(5)
                    .background(Circle().fill(Color.secondary.opacity(0.12)))
            }
            .buttonStyle(.plain)
            .help("Close the board (Esc, or click outside)")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: email triage status section

    private var emailTriageSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                sectionHeader("Mail")
                Spacer()
                if emailTriage.isRunning {
                    ProgressView().controlSize(.small)
                    Text("running…")
                        .font(.caption2).foregroundStyle(.secondary)
                } else {
                    Button {
                        emailTriage.runNow()
                    } label: {
                        Label("Run now", systemImage: "arrow.clockwise")
                            .font(.caption2)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .help("Run email triage now (ignores the "
                          + "once-per-day marker). Takes a few seconds.")
                }
            }

            HStack(spacing: 6) {
                Text("Last run:")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                if let r = emailTriage.lastRun {
                    Text(r, style: .relative)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text("·")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Text(r, style: .time)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                } else {
                    Text("never")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            if let status = emailTriage.lastStatus {
                let ok = status.hasPrefix("email triage ok")
                HStack(spacing: 4) {
                    Image(systemName: ok
                          ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .font(.caption2)
                        .foregroundStyle(ok ? Color.green : Color.orange)
                    Text(status)
                        .font(.caption2)
                        .foregroundStyle(ok ? Color.green : Color.orange)
                        .fixedSize(horizontal: false, vertical: true)
                    if let d = emailTriage.lastDurationSec {
                        Text("· \(Self.durationText(d))")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if let summary = emailTriage.lastSummary {
                Text(summary)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.secondary.opacity(0.06)))
    }

    // MARK: proactive research — now a board WIDGET card (migrated off left)
    //
    // Pure render of the engine-produced surface payload. Identical data the
    // left popover used to show under "For your next briefing"; it now lives
    // here as a board card so everything domain-ish is in one place.

    private func researchSurfacingWidget(_ s: SurfacePayload) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                sectionHeader("Research surfacing")
                Text("\(s.lines.count)")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .monospacedDigit()
                    .foregroundStyle(.purple)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(Color.purple.opacity(0.12)))
                Spacer()
                Text("for your next briefing")
                    .font(.system(size: 9))
                    .foregroundStyle(.secondary)
            }
            .help(HelpText.researchSurfacing)

            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(s.lines.enumerated()), id: \.offset) {
                    _, line in
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "sparkle")
                            .font(.system(size: 8))
                            .foregroundStyle(.purple)
                            .padding(.top, 2)
                        Text(line)
                            .font(.caption2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if let o = s.overflow {
                    Text(o)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.secondary.opacity(0.06)))
    }

    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption)
            .fontWeight(.semibold)
            .tracking(0.6)
            .foregroundStyle(.secondary)
    }

    /// Compact human duration: "0.8s" / "12s" / "1m 05s".
    static func durationText(_ s: Double) -> String {
        if s < 1 { return String(format: "%.1fs", s) }
        if s < 60 { return "\(Int(s.rounded()))s" }
        let m = Int(s) / 60, sec = Int(s) % 60
        return String(format: "%dm %02ds", m, sec)
    }
}
