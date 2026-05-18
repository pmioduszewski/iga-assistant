import SwiftUI

// MARK: - Skills section (#3 — show WHICH skills feed the numbers)
//
// Lists each discovered skill that registers background tasks and/or widgets,
// so "Skills checked: 2" is no longer opaque — you see the actual skills,
// what each does in plain words, and whether it found anything this round.
//
// Read-only: it renders SkillDiscovery's scan plus the engine's already-
// decoded counts. No logic, no writes, no exec.

struct SkillsSectionView: View {
    /// Skills discovered by the read-only SKILL.md scan.
    let skills: [DiscoveredSkill]
    /// From the engine state: how many skills it checked / found work in.
    let discovered: Int
    let fired: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                sectionHeader("Skills")
                Text("\(skills.count) checked")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
            }
            .help(HelpText.skillsSection)

            if skills.isEmpty {
                Text("No skills register background tasks or widgets yet.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(skills) { skill in
                        skillRow(skill)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func skillRow(_ s: DiscoveredSkill) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(s.name)
                    .font(.caption)
                    .fontWeight(.semibold)
                Spacer()
                tagRow(s)
            }
            Text(s.summary)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .help(HelpText.skillRow)
    }

    @ViewBuilder
    private func tagRow(_ s: DiscoveredSkill) -> some View {
        HStack(spacing: 4) {
            if s.hasProactive {
                miniTag("background tasks", .blue)
            }
            if s.widgetCount > 0 {
                miniTag(
                    s.widgetCount == 1 ? "1 widget"
                                       : "\(s.widgetCount) widgets",
                    .purple)
            }
        }
    }

    private func miniTag(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.system(size: 9))
            .fontWeight(.medium)
            .foregroundStyle(color)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill(color.opacity(0.12)))
    }

    private func sectionHeader(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.caption)
            .fontWeight(.semibold)
            .tracking(0.6)
            .foregroundStyle(.secondary)
    }
}
