import SwiftUI

// MARK: - per-day quick-log drawer (Wave D) — RELAY ONLY
//
// Opened by tapping a per-day-GOAL habit's day square (a 40-rep habit must
// not complete in one blunt tap, and 40 caveman taps is worse). Mirrors
// a quick-log drawer: a progress bar, the current / target count, a
// −/＋ stepper, a batch-step selector (1·5·10·50·100), Reset (→0) and Fill
// Day (→target).
//
// Contract: holds ZERO habit logic and issues NO writes. Every control names
// an absolute desired amount and relays it via `store.relaySetAmount` to the
// single sanctioned record seam; the engine clamps/derives streak/goal/level
// and re-emits. The drawer's displayed value is the ENGINE TRUTH read back
// from the decoded state (`store.currentAmount`) — never a local optimistic
// guess that could diverge. Binary habits never reach this drawer (they stay
// an instant toggle).

/// Identifies which (habit, day) drawer is open. `HabitEntry` carries the
/// per-day target / allow-exceed / name; the date is the tapped civil day.
struct HabitLogContext: Identifiable {
    let habit: HabitEntry
    let date: String
    var id: String { "\(habit.id)@\(date)" }
}

struct HabitLogDrawer: View {
    let context: HabitLogContext
    let store: HabitsWidgetStore
    let onClose: () -> Void

    /// Batch step size for −/＋ (the chip row). Session-local; the
    /// engine never sees the step, only the resulting absolute amount.
    @State private var step: Int = 1

    static let steps = [1, 5, 10, 50, 100]

    /// Pure −/＋ arithmetic (unit-tested). The app only DERIVES the desired
    /// absolute amount; the engine still owns streak/goal/level. `delta` is
    /// ±step. Floor 0. Ceiling = `target` unless the habit allows exceeding
    /// it (the source `allowExceedingGoal`), in which case ＋ can go past
    /// target but never below current+step's natural bound.
    nonisolated static func nextAmount(
        current: Int, delta: Int, target: Int, allowExceed: Bool
    ) -> Int {
        let raw = current + delta
        if raw <= 0 { return 0 }
        if allowExceed { return raw }
        return min(raw, max(target, 0))
    }

    private var habit: HabitEntry { context.habit }
    private var date: String { context.date }

    /// The per-day target (always > 1 here — binary habits never open this).
    private var target: Int { max(1, habit.goal.perDayTarget ?? 1) }

    /// ENGINE TRUTH for this (habit, day). The drawer renders this, not a
    /// local guess, so it can never drift from what was actually recorded.
    private var amount: Int {
        store.currentAmount(habitId: habit.id, date: date) ?? 0
    }

    private var pending: Bool {
        store.isPending(habit.id, date)
    }

    private var isToday: Bool {
        date == HabitsWidgetStore.systemTodayISO()
    }

    private var progress: Double {
        min(1.0, max(0.0, Double(amount) / Double(target)))
    }

    /// ＋ is disabled once it can't raise the value (target reached and the
    /// habit doesn't allow exceeding it).
    private var canIncrement: Bool {
        Self.nextAmount(
            current: amount, delta: step, target: target,
            allowExceed: habit.goal.allowExceed) > amount
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            header

            if let err = store.lastRelayError {
                Label(err, systemImage:
                        "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Progress bar (amount / target).
            ProgressView(value: progress)
                .tint(HabitsWidgetView.color(habit.colorHex))

            // − [ current / target ] ＋
            HStack(spacing: 12) {
                stepButton("minus", enabled: amount > 0) {
                    relay(Self.nextAmount(
                        current: amount, delta: -step, target: target,
                        allowExceed: habit.goal.allowExceed))
                }
                Spacer()
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text("\(amount)")
                        .font(.system(
                            size: 30, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                    Text("/ \(target)")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
                Spacer()
                stepButton("plus", enabled: canIncrement) {
                    relay(Self.nextAmount(
                        current: amount, delta: step, target: target,
                        allowExceed: habit.goal.allowExceed))
                }
            }

            // Batch-step chips.
            HStack(spacing: 6) {
                ForEach(Self.steps, id: \.self) { s in
                    Button {
                        step = s
                    } label: {
                        Text("\(s)")
                            .font(.caption)
                            .fontWeight(step == s ? .bold : .regular)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                            .background(
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(step == s
                                          ? Color.accentColor.opacity(0.25)
                                          : Color.secondary.opacity(0.10)))
                    }
                    .buttonStyle(.plain)
                    .help("Each −/＋ changes the count by \(s)")
                }
            }

            // Fixed-height status row — ALWAYS present so a relay starting
            // /ending never resizes the dialog (the "jumping" bug). Shows
            // the spinner only while saving; an empty same-height spacer
            // otherwise.
            ZStack {
                if pending {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Saving…").font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .frame(height: 16, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)

            // Reset (left) · Fill Day (secondary) · Done (primary, right).
            HStack(spacing: 10) {
                Button("Reset") { relay(0) }
                    .buttonStyle(.bordered)
                    .disabled(pending || amount == 0)
                Spacer()
                Button("Fill Day") { relay(target) }
                    .buttonStyle(.bordered)
                    .disabled(pending || amount >= target)
                Button("Done") { onClose() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 320)
    }

    /// Compact-style identity so it's unmistakable which habit you're
    /// editing: the same glyph + colour treatment as the Compact row, the
    /// name, a streak chip, and the day pill. Mirrors `HabitsWidgetView`'s
    /// row visuals (shared static helpers — no habit logic here).
    private var header: some View {
        let color = HabitsWidgetView.color(habit.colorHex)
        return HStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 7)
                    .fill(color.opacity(0.15))
                    .frame(width: 30, height: 30)
                if let e = habit.emoji, !e.isEmpty {
                    Text(e).font(.body)
                } else {
                    Image(systemName:
                            HabitsWidgetView.sfSymbol(for: habit.icon))
                        .font(.system(size: 14))
                        .foregroundStyle(color)
                }
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(habit.name)
                    .font(.headline)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Text(isToday
                         ? "Today"
                         : (HabitsWidgetView.prettyDate(date) ?? date))
                        .font(.caption2)
                        .fontWeight(.semibold)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(Capsule()
                            .fill(Color.secondary.opacity(0.15)))
                    if habit.currentStreak > 0 {
                        HStack(spacing: 2) {
                            Image(systemName: "flame.fill")
                                .font(.system(size: 8))
                            Text("\(habit.currentStreak)")
                                .font(.caption2)
                                .fontWeight(.semibold)
                                .monospacedDigit()
                        }
                        .foregroundStyle(color)
                    }
                }
            }
            Spacer()
            Button {
                onClose()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.cancelAction)
            .help("Close")
        }
    }

    private func stepButton(
        _ symbol: String, enabled: Bool, _ action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 14, weight: .bold))
                .frame(width: 40, height: 32)
                .background(
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.secondary.opacity(0.12)))
        }
        .buttonStyle(.plain)
        .disabled(!enabled || pending)
    }

    /// Name an absolute desired amount → the sanctioned seam. The engine is
    /// the only mutator; the displayed value updates when the poll re-reads
    /// the re-emitted state.
    private func relay(_ newAmount: Int) {
        store.relaySetAmount(
            habitId: habit.id, date: date, amount: newAmount)
    }
}
