import SwiftUI
import AppKit

// MARK: - Per-habit management sheet (Wave D) — RELAY ONLY
//
// Opened from the Compact row's ⋯ menu. Rename, recolour, reorder, set the
// goal/schedule, archive, delete, and back up / restore the whole tracker.
// It holds ZERO habit logic and issues NO writes: every action names an
// intent and relays it via `store.relayManage` to the single sanctioned
// manage seam; the engine performs the mutation + re-emits the JSON.
//
// One op per explicit button (no op-chaining) so a multi-field "save" can't
// race the single-op engine seam. Controls disable while a relay is in
// flight; a failure is surfaced inline. Plain-language copy only.

struct HabitManageSheet: View {
    let habit: HabitEntry
    let store: HabitsWidgetStore
    let onClose: () -> Void

    @State private var name: String
    @State private var period: String          // none | day | week | month
    @State private var periodTarget: Int       // times per week/month
    @State private var perDayReps: Int         // 1 = simple yes/no; >1 reps
    @State private var allowExceed: Bool
    @State private var confirmingDelete = false
    @State private var position: Int
    @State private var pickColor: Color
    @State private var infoOpen: String?       // which ⓘ popover is open

    /// Curated palette — juicy primaries (row 1) + dreamy pastels (row 2).
    /// Good enough for ~80% without opening the system colour panel.
    static let palette: [(String, String)] = [
        ("Rose", "#e5484d"), ("Coral", "#f76b15"),
        ("Amber", "#ffb224"), ("Lime", "#99d52a"),
        ("Green", "#30a46c"), ("Teal", "#12a594"),
        ("Cyan", "#0fa3c2"), ("Blue", "#3e63dd"),
        ("Indigo", "#5b5bd6"), ("Violet", "#8e4ec6"),
        ("Pink", "#d6409f"),
        ("Blush", "#f3b8c2"), ("Peach", "#ffd6a5"),
        ("Butter", "#fbe7a1"), ("Mint", "#bDe9cf"),
        ("Sky", "#bfe0f5"), ("Lavender", "#d8cdf6"),
        ("Lilac", "#ead0f0"), ("Sage", "#cfe3cc"),
        ("Stone", "#d9d4cd"), ("Slate", "#647084"),
        ("Graphite", "#8b8d98"),
    ]

    init(
        habit: HabitEntry,
        store: HabitsWidgetStore,
        onClose: @escaping () -> Void
    ) {
        self.habit = habit
        self.store = store
        self.onClose = onClose
        _name = State(initialValue: habit.name)
        let g = habit.goal
        _period = State(
            initialValue: g.period == "none" ? "none" : g.period)
        _periodTarget = State(initialValue: g.target ?? 3)
        _perDayReps = State(initialValue: max(1, g.perDayTarget ?? 1))
        _allowExceed = State(initialValue: g.allowExceed)
        let idx = store.data.habits.firstIndex {
            $0.id == habit.id
        } ?? 0
        _position = State(initialValue: idx + 1)
        _pickColor = State(
            initialValue: HabitsWidgetView.color(habit.colorHex))
    }

    private var currentPosition: Int {
        (store.data.habits.firstIndex { $0.id == habit.id } ?? 0) + 1
    }
    private var habitCount: Int { max(1, store.data.habits.count) }
    private var busy: Bool { store.managePending }
    private var habitColor: Color {
        HabitsWidgetView.color(habit.colorHex)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                if let err = store.lastRelayError {
                    Label(err, systemImage:
                            "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                nameSection
                colourSection
                goalSection
                positionSection
                lifecycleSection
                dangerCard
                if busy {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Saving…").font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(18)
        }
        .frame(width: 400, height: 560)
    }

    // MARK: header

    private var header: some View {
        HStack(spacing: 10) {
            ZStack {
                RoundedRectangle(cornerRadius: 7)
                    .fill(habitColor.opacity(0.18))
                    .frame(width: 30, height: 30)
                Image(systemName:
                        HabitsWidgetView.sfSymbol(for: habit.icon))
                    .font(.system(size: 14))
                    .foregroundStyle(habitColor)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(habit.name).font(.headline).lineLimit(1)
                Text("Manage habit")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Done") { onClose() }
                .keyboardShortcut(.defaultAction)
        }
    }

    // MARK: name

    private var nameSection: some View {
        section("Name") {
            HStack {
                TextField("Habit name", text: $name)
                    .textFieldStyle(.roundedBorder)
                Button("Rename") {
                    relay(.rename(name: name.trimmingCharacters(
                        in: .whitespacesAndNewlines)))
                }
                .disabled(busy || !renameChanged)
            }
        }
    }

    // MARK: colour — swatch palette + custom

    private var colourSection: some View {
        section("Colour") {
            let cols = [GridItem(.adaptive(minimum: 26), spacing: 8)]
            LazyVGrid(columns: cols, alignment: .leading, spacing: 8) {
                ForEach(Self.palette, id: \.1) { _, hex in
                    let c = HabitsWidgetView.color(hex)
                    let sel = Self.hex(pickColor).lowercased()
                        == hex.lowercased()
                    Circle()
                        .fill(c)
                        .frame(width: 24, height: 24)
                        .overlay(
                            Circle().strokeBorder(
                                Color.primary.opacity(sel ? 0.9 : 0),
                                lineWidth: 2))
                        .overlay(
                            Circle().strokeBorder(
                                Color.black.opacity(0.12), lineWidth: 0.5))
                        .contentShape(Circle())
                        .onTapGesture { pickColor = c }
                        .help(hex)
                }
            }
            HStack(spacing: 10) {
                ColorPicker("Custom…", selection: $pickColor,
                            supportsOpacity: false)
                    .fixedSize()
                Text(Self.hex(pickColor))
                    .font(.caption2.monospaced())
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Apply") {
                    relay(.setColor(hex: Self.hex(pickColor)))
                }
                .disabled(busy
                    || Self.hex(pickColor).lowercased()
                        == habit.colorHex.lowercased())
            }
        }
    }

    // MARK: goal & schedule

    private var goalSection: some View {
        section("Goal & schedule") {
            Picker("", selection: $period) {
                Text("None").tag("none")
                Text("Daily").tag("day")
                Text("Weekly").tag("week")
                Text("Monthly").tag("month")
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            if period == "week" || period == "month" {
                Stepper(
                    "Do it \(periodTarget)× per \(periodWord)",
                    value: $periodTarget, in: 1...99)
            }

            HStack(spacing: 6) {
                Stepper(
                    "Per-day goal: \(perDayReps)"
                        + (perDayReps == 1 ? " (once)" : ""),
                    value: $perDayReps, in: 1...500)
                infoBadge(
                    "info-reps",
                    "How many counts make ONE day complete (e.g. 50 "
                    + "push-ups; 1 = a plain yes/no habit). The "
                    + "interaction is AUTOMATIC from this number: "
                    + "1–10 → tap the square to add one (segmented "
                    + "ring; tapping a full day resets it to 0). "
                    + ">10 → tap opens a +/- logger (percentage "
                    + "ring). You never choose the mode — the goal "
                    + "decides it.")
            }

            HStack(spacing: 6) {
                Toggle("Allow exceeding the goal",
                       isOn: $allowExceed)
                infoBadge(
                    "info-exceed",
                    "On: you can keep logging past the target "
                    + "(e.g. 60/50 push-ups). Off: the count caps at "
                    + "the target.")
            }

            Button("Apply goal") {
                relay(.setGoal(
                    period: period,
                    target: (period == "week" || period == "month")
                        ? periodTarget : nil,
                    perDayTarget: perDayReps > 1 ? perDayReps : nil,
                    allowExceed: allowExceed))
            }
            .disabled(busy)
        }
    }

    // MARK: position

    private var positionSection: some View {
        section("Position") {
            Stepper(
                "Show as #\(position) of \(habitCount)",
                value: $position, in: 1...habitCount)
            Button("Apply position") {
                relay(.setOrder(position: position))
            }
            .disabled(busy || position == currentPosition)
        }
    }

    // MARK: backup & restore (whole tracker)

    // Whole-tracker backup/restore moved OUT of this per-habit sheet to
    // app-wide Global Settings (right-click the menu-bar icon) — it was
    // vague here (it's ALL habits, not the selected one).

    // MARK: lifecycle (graduate / archive)

    private var lifecycleSection: some View {
        section("Lifecycle") {
            HStack(spacing: 6) {
                Button {
                    store.relayManage(
                        habitId: habit.id, op: .setArchived(true)
                    ) { ok in if ok { onClose() } }
                } label: {
                    Label("Graduate (archive)",
                          systemImage: "tray.and.arrow.down.fill")
                }
                .disabled(busy)
                infoBadge(
                    "info-archive",
                    "Archiving keeps ALL history but hides the habit "
                    + "from the active list and the focus count. "
                    + "Atomic Habits: graduate an automatic habit to "
                    + "free a focus slot. Find archived habits under "
                    + "“Archived” at the bottom of the list to restore.")
            }
        }
    }

    // MARK: danger — red tinted card

    private var dangerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Danger", systemImage: "exclamationmark.octagon.fill")
                .font(.caption2).fontWeight(.semibold)
                .foregroundStyle(.red)
            if confirmingDelete {
                Text("Delete “\(habit.name)” and ALL its history? "
                     + "This cannot be undone — archive instead if you "
                     + "just want it out of the way.")
                    .font(.caption)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button("Cancel") { confirmingDelete = false }
                    Spacer()
                    Button(role: .destructive) {
                        store.relayManage(
                            habitId: habit.id, op: .delete
                        ) { ok in if ok { onClose() } }
                    } label: {
                        Text("Delete permanently")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.red)
                    .disabled(busy)
                }
            } else {
                Button(role: .destructive) {
                    confirmingDelete = true
                } label: {
                    Label("Delete habit…", systemImage: "trash")
                        .foregroundStyle(.red)
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.red.opacity(0.08))
                .overlay(RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.red.opacity(0.25),
                                  lineWidth: 0.5)))
    }

    // MARK: helpers

    private func relay(_ op: ContractGuard.ManageOp) {
        store.relayManage(habitId: habit.id, op: op)
    }

    private var renameChanged: Bool {
        let t = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return t != habit.name && !t.isEmpty
    }

    private var periodWord: String {
        switch period {
        case "day": return "day"
        case "week": return "week"
        case "month": return "month"
        default: return "period"
        }
    }

    /// A small ⓘ that opens a styled popover (tinted, coherent with the
    /// coach/advisory cards) — replaces the truncated caption explanations.
    private func infoBadge(_ id: String, _ text: String) -> some View {
        Image(systemName: "info.circle")
            .font(.caption2)
            .foregroundStyle(.secondary)
            .onHover { inside in
                infoOpen = inside
                    ? id : (infoOpen == id ? nil : infoOpen)
            }
            .popover(isPresented: Binding(
                get: { infoOpen == id },
                set: { if !$0 { infoOpen = nil } }),
                arrowEdge: .top
            ) {
            Text(text)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(12)
                .frame(width: 260)
                .background(Color.secondary.opacity(0.10))
        }
    }

    /// SwiftUI `Color` → `#rrggbb` (sRGB). Exact inverse of the
    /// projection's hex→Color (unit-tested round-trip).
    static func hex(_ c: Color) -> String {
        let ns = NSColor(c).usingColorSpace(.sRGB)
        guard let ns else { return "#5b5bd6" }
        let r = Int((ns.redComponent * 255).rounded())
        let g = Int((ns.greenComponent * 255).rounded())
        let b = Int((ns.blueComponent * 255).rounded())
        return String(format: "#%02x%02x%02x",
                      max(0, min(255, r)),
                      max(0, min(255, g)),
                      max(0, min(255, b)))
    }

    @ViewBuilder
    private func section(
        _ title: String, @ViewBuilder _ content: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.caption2).fontWeight(.semibold)
                .tracking(0.6)
                .foregroundStyle(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

}
