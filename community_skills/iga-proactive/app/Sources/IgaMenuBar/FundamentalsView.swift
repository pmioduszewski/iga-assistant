import SwiftUI
import AppKit

// MARK: - The LEFT column: Iga FUNDAMENTALS
//
// Pure render of engine state. No computation that decides anything — every
// number shown comes straight from EngineState / LedgerSnapshot. The only
// "logic" here is presentation: mapping already-decoded values to a color or
// a relative-time string. Nothing here affects the engine, the ledger, the
// state poll, or the contract guard.
//
// Wave C v2 (the user's corrected design): this is the LEFT column of the
// single unified panel (`PanelController`). The widget BOARD is the RIGHT
// column (`BoardPanelView`) and is shown SIMULTANEOUSLY by one status-item
// click — there is NO "Open board" button and NO board toggle here anymore.
// This column owns ZERO board content; it carries only the fundamentals:
// status, usage limits, queue/last-check, skills, and the action controls.

struct FundamentalsView: View {
    // @Observable stores → plain `let` (SwiftUI auto-tracks body reads; the
    // manual Binding(get:set:) closures over scheduler/loginItem still work
    // against a plain reference).
    let store: StateStore
    let scheduler: Scheduler
    let loginItem: LoginItem

    /// Read-only one-shot SKILL.md scan for the Skills section (#3). Computed
    /// in `body` access, not stored mutable state — no logic, just a read.
    private var discoveredSkills: [DiscoveredSkill] {
        SkillDiscovery.scan().skills
    }

    // Section vertical rhythm. One constant so spacing stays consistent.
    private let sectionGap: CGFloat = 10

    var body: some View {
        // This column owns its OWN single vertical scroll context. It is a
        // sibling of the board column (not nested in it), so it does not
        // compete with the board's own single vertical scroll — they are two
        // independent columns in one HStack.
        ScrollView(.vertical) {
            VStack(alignment: .leading, spacing: sectionGap) {
                header
                countsRow
                Divider()
                governorRow
                Divider()
                queueSection
                tickSection
                Divider()
                SkillsSectionView(
                    skills: discoveredSkills,
                    discovered: store.state.tick?.discoveredJobs ?? 0,
                    fired: store.state.tick?.firedCandidates ?? 0)
                Divider()
                actions
                Divider()
                footer
            }
            .padding(14)
        }
        .frame(width: PanelController.columnWidth)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(.regularMaterial)
    }

    // MARK: header

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Iga")
                .font(.headline)
            Text("Your assistant")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Spacer()
            healthBadge
        }
    }

    /// Health pill reflects real status. Color is derived purely from the
    /// already-computed `store.health` enum — no new state, no decisions.
    private var healthBadge: some View {
        let (text, color): (String, Color) = {
            switch store.health {
            case .healthy:      return ("All good", .green)
            case .stale:        return ("Catching up", .orange)
            case .notRunYet:    return ("Not started yet", .secondary)
            case .error:        return ("Needs a look", .red)
            }
        }()
        return HStack(spacing: 4) {
            Circle()
                .fill(color)
                .frame(width: 7, height: 7)
            Text(text)
                .font(.caption)
                .fontWeight(.medium)
                .foregroundStyle(color)
        }
        .lineLimit(1)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(
            Capsule().fill(color.opacity(0.12)))
        .help(HelpText.health)
    }

    // MARK: counts — the focal block

    private var countsRow: some View {
        HStack(spacing: 0) {
            countCell("Lined up", store.state.counts.queued,
                      store.state.counts.queued > 0 ? .blue : .secondary,
                      emphasized: store.state.counts.queued > 0)
                .help(HelpText.queued)
            divider
            countCell("In progress", store.state.counts.running,
                      store.state.counts.running > 0 ? .purple : .secondary,
                      emphasized: store.state.counts.running > 0)
                .help(HelpText.running)
            divider
            countCell("Finished", store.state.counts.done, .green,
                      emphasized: false)
                .help(HelpText.done)
        }
    }

    private var divider: some View {
        Rectangle()
            .fill(Color.secondary.opacity(0.18))
            .frame(width: 1, height: 28)
    }

    private func countCell(_ label: String, _ n: Int, _ c: Color,
                           emphasized: Bool) -> some View {
        VStack(spacing: 3) {
            Text("\(n)")
                .font(.system(.title2, design: .rounded))
                .fontWeight(emphasized ? .bold : .semibold)
                .monospacedDigit()
                .foregroundStyle(c)
            Text(label.uppercased())
                .font(.caption2)
                .fontWeight(.medium)
                .tracking(0.5)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: governor

    @ViewBuilder
    private var governorRow: some View {
        let g = store.state.governor
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                sectionHeader("Usage limits")
                    .help(HelpText.governorSection)
                Spacer()
                Text(g.breakerTripped ? "Paused" : "OK")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(g.breakerTripped ? Color.orange
                                                       : Color.green)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(
                        Capsule().fill(
                            (g.breakerTripped ? Color.orange : Color.green)
                                .opacity(0.12)))
                    .help(HelpText.breaker)
            }
            if g.hasBudget {
                meter("Tasks started · last 5h",
                      g.invocations5h, g.maxInvocations5h,
                      breaker: g.breakerTripped)
                    .help(HelpText.invocations5h)
                meter("Tasks started · last 24h",
                      g.invocations24h, g.maxInvocations24h,
                      breaker: g.breakerTripped)
                    .help(HelpText.invocations24h)
                meter("Thinking effort · last 5h",
                      g.estTokens5h, g.maxEstTokens5h,
                      breaker: g.breakerTripped)
                    .help(HelpText.estTokens5h)
            } else if g.errorText != nil {
                Label("Usage info isn't available right now.",
                      systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            } else {
                Text("No usage info yet.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// Meter color reflects actual headroom: amber ≥70% of ceiling,
    /// red ≥90% or when the breaker is tripped. Pure presentation mapping
    /// of values the engine already computed — no admission decision here.
    private func meter(_ label: String, _ used: Int, _ max: Int,
                       breaker: Bool) -> some View {
        let frac = max > 0 ? min(1.0, Double(used) / Double(max)) : 0
        let color: Color = {
            if breaker || frac >= 0.9 { return .red }
            if frac >= 0.7 { return .orange }
            return .green
        }()
        return VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
                Text("\(used) / \(max)")
                    .font(.caption2)
                    .monospacedDigit()
                    .foregroundStyle(frac >= 0.7 ? color : .secondary)
            }
            ProgressView(value: frac)
                .tint(color)
                .scaleEffect(x: 1, y: 0.7, anchor: .center)
        }
    }

    // MARK: queue

    @ViewBuilder
    private var queueSection: some View {
        let count = store.state.queue.count
        HStack(spacing: 6) {
            sectionHeader("Lined up")
            Text("\(count)")
                .font(.caption)
                .fontWeight(.semibold)
                .monospacedDigit()
                .foregroundStyle(count > 0 ? .blue : .secondary)
                .padding(.horizontal, 6)
                .padding(.vertical, 1)
                .background(
                    Capsule().fill(
                        (count > 0 ? Color.blue : Color.secondary)
                            .opacity(0.12)))
        }
        .help(HelpText.queueSection)
        if store.state.queue.isEmpty {
            Text("Nothing lined up right now.")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else {
            VStack(alignment: .leading, spacing: 3) {
                ForEach(store.state.queue.prefix(8)) { r in
                    HStack(spacing: 6) {
                        Text(r.jobId)
                            .font(.caption)
                            .fontWeight(.medium)
                        Text(r.shortKey)
                            .font(.caption2)
                            .monospaced()
                            .foregroundStyle(.secondary)
                            .help(HelpText.idempotencyKey)
                        Spacer()
                        Text(r.model ?? "—")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    .help(HelpText.queueRow)
                }
                if store.state.queue.count > 8 {
                    Text("+\(store.state.queue.count - 8) more")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        if !store.ledger.unavailable && !store.ledger.rows.isEmpty {
            Text("Tally · \(store.ledger.queued) lined up · "
                + "\(store.ledger.running) in progress · "
                + "\(store.ledger.done) finished")
                .font(.caption2)
                .foregroundStyle(.secondary)
                .help(HelpText.ledgerLine)
        } else if store.ledger.unavailable {
            Text("Tally · not available yet")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: tick — aligned key/value mini-grid

    @ViewBuilder
    private var tickSection: some View {
        if let t = store.state.tick {
            VStack(alignment: .leading, spacing: 6) {
                sectionHeader("Last check")
                    .help(HelpText.tickSection)
                let cols = [
                    GridItem(.flexible(), spacing: 8),
                    GridItem(.flexible(), spacing: 8)
                ]
                LazyVGrid(columns: cols, alignment: .leading, spacing: 4) {
                    tickStat("Skills checked", t.discoveredJobs)
                        .help(HelpText.discovered)
                    tickStat("Found to prep", t.firedCandidates)
                        .help(HelpText.fired)
                    tickStat("Not the right moment", t.conditionSkipped)
                        .help(HelpText.condSkip)
                    tickStat("Already handled", t.claimSkipped)
                        .help(HelpText.claimSkip)
                    tickStat("Held back for limits", t.governorDenied,
                             warn: t.governorDenied > 0)
                        .help(HelpText.govDeny)
                }
                if t.queueAlert {
                    Label("More than usual this time",
                          systemImage: "exclamationmark.circle")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .help(HelpText.queueAlert)
                }
                errorsDisclosure(t.errors)
            }
        } else {
            Text("No check details yet.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func tickStat(_ label: String, _ value: Int,
                          warn: Bool = false) -> some View {
        HStack(spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
            Spacer(minLength: 4)
            Text("\(value)")
                .font(.caption2)
                .fontWeight(.medium)
                .monospacedDigit()
                .foregroundStyle(
                    warn && value > 0 ? Color.orange : Color.primary)
        }
    }

    /// Errors as a contained, count-summarized disclosure — never raw red
    /// text bleeding across the panel. Zero errors renders nothing.
    @ViewBuilder
    private func errorsDisclosure(_ errors: [String]) -> some View {
        if !errors.isEmpty {
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(errors.prefix(6).enumerated()),
                            id: \.offset) { _, e in
                        Text("• \(e)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if errors.count > 6 {
                        Text("+\(errors.count - 6) more")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.top, 4)
            } label: {
                Label(
                    "\(errors.count) skill "
                    + "\(errors.count == 1 ? "needs" : "need") a look",
                    systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .fontWeight(.medium)
                    .foregroundStyle(.red)
            }
            .padding(8)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color.red.opacity(0.08)))
            .help(HelpText.skillErrors)
        }
    }

    // MARK: actions

    private var actions: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                store.scanNow()
            } label: {
                Label(
                    store.scanInProgress ? "Checking…" : "Check now",
                    systemImage: "arrow.clockwise")
            }
            .disabled(store.scanInProgress)
            .help(HelpText.scanNow)

            Button {
                openStateFile()
            } label: {
                Label("Open Iga's notes file", systemImage: "doc.text")
            }
            .help(HelpText.openStateFile)

            Toggle(isOn: Binding(
                get: { scheduler.enabled },
                set: { _ in scheduler.toggle() })) {
                HStack {
                    Label("Scheduling", systemImage: "clock")
                    Spacer()
                    Text(scheduler.nextHint)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .help(HelpText.scheduling)

            Toggle(isOn: Binding(
                get: { loginItem.isEnabled },
                set: { _ in loginItem.toggle() })) {
                HStack {
                    Label("Launch at login", systemImage: "power")
                    Spacer()
                    Text(loginItemStatus)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .help(HelpText.launchAtLogin)

            Button(role: .destructive) {
                NSApplication.shared.terminate(nil)
            } label: {
                Label("Quit Iga", systemImage: "xmark.circle")
            }
        }
        .buttonStyle(.plain)
    }

    /// Honest, non-alarming login-item state. `.notFound` / `.notRegistered`
    /// is just the actionable off-state, never the bare word "Not found"
    /// as if the app were broken. Maps the existing `statusText` only.
    private var loginItemStatus: String {
        switch loginItem.statusText {
        case "Enabled":
            return "enabled"
        case "Not registered", "Not found":
            return "off"
        case "Requires approval in System Settings":
            return "needs approval"
        default:
            return loginItem.statusText.lowercased()
        }
    }

    // MARK: footer

    private var footer: some View {
        HStack {
            Text(updatedText)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .help("\(absoluteUpdatedText) — \(HelpText.footerTimestamps)")
            Spacer()
            if let r = store.lastScanResult {
                Text(r.ok ? "check done" : "check didn't finish")
                    .font(.caption2)
                    .fontWeight(.medium)
                    .foregroundStyle(r.ok ? Color.green : Color.red)
            }
        }
    }

    // MARK: shared bits

    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption)
            .fontWeight(.semibold)
            .tracking(0.6)
            .foregroundStyle(.secondary)
    }

    /// Relative ("12m ago") footer; absolute timestamps on hover via `.help`.
    private var updatedText: String {
        var parts: [String] = []
        if let g = store.state.generatedAt {
            parts.append("last checked \(Self.relative(g))")
        }
        if let p = store.lastPolled {
            parts.append("refreshed \(Self.relative(p))")
        }
        return parts.isEmpty ? "nothing yet"
                             : parts.joined(separator: " · ")
    }

    /// Cached absolute-timestamp formatter (fix #4) — was allocated on every
    /// access of `absoluteUpdatedText`, which is evaluated in `body` (the
    /// footer `.help`) on every render.
    private static let absoluteStamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()

    private var absoluteUpdatedText: String {
        let df = Self.absoluteStamp
        var parts: [String] = []
        if let g = store.state.generatedAt {
            parts.append("last checked \(df.string(from: g))")
        }
        if let p = store.lastPolled {
            parts.append("refreshed \(df.string(from: p))")
        }
        return parts.isEmpty ? "nothing yet"
                             : parts.joined(separator: " · ")
    }

    private static func relative(_ date: Date) -> String {
        let s = Int(Date().timeIntervalSince(date))
        if s < 5 { return "just now" }
        if s < 60 { return "\(s)s ago" }
        let m = s / 60
        if m < 60 { return "\(m)m ago" }
        let h = m / 60
        if h < 24 { return "\(h)h ago" }
        return "\(h / 24)d ago"
    }

    private func openStateFile() {
        let path = StateStore.defaultStatePath()
        if FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.activateFileViewerSelecting(
                [URL(fileURLWithPath: path)])
        } else {
            NSWorkspace.shared.activateFileViewerSelecting(
                [URL(fileURLWithPath:
                    (path as NSString).deletingLastPathComponent)])
        }
    }
}
