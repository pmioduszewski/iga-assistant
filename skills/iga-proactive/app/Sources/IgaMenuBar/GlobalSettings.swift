import SwiftUI
import AppKit

// MARK: - Global (app-wide) settings
//
// The menu-bar panel is per-surface (proactive board + habits). APP-WIDE
// actions that are NOT scoped to one habit live HERE — opened from the
// status-item RIGHT-CLICK menu. Whole-tracker backup/restore moved out of
// the per-habit ⋯ sheet (it was vague there: it's ALL habits, not the
// selected one). Render+relay only — every mutation goes through the
// sanctioned manage seam via `store.relayManage`.

struct GlobalSettingsView: View {
    let habits: HabitsWidgetStore
    let onClose: () -> Void
    /// Called after a setting that affects a live status item changes
    /// (the AppDelegate refreshes the ticker).
    let onChanged: () -> Void

    @State private var tickerOn = HabitTickerStatusItem.enabled
    private var busy: Bool { habits.managePending }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Image(systemName: "brain.head.profile")
                    .foregroundStyle(.secondary)
                Text("Iga settings").font(.headline)
                Spacer()
                Button("Done") { onClose() }
                    .keyboardShortcut(.defaultAction)
            }

            if let err = habits.lastRelayError {
                Label(err, systemImage:
                        "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // All global (cross-habit) settings live under ONE Habits
            // section — future-proof home as more land here. Per-habit
            // settings stay in the row ⋯ sheet.
            section("Habits") {
                subhead("Backup & restore")
                Text("Your ENTIRE tracker — every habit and all history, "
                     + "one file. Use it to back up or move machines. "
                     + "Import merges (idempotent; safe to re-import).")
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Button("Export all…") { exportTracker() }
                        .disabled(busy)
                    Button("Import all…") { importTracker() }
                        .disabled(busy)
                    if busy {
                        ProgressView().controlSize(.small)
                        Text("Working…").font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }

                Divider().padding(.vertical, 4)

                subhead("Menu bar")
                Toggle(
                    "Show today's habits in the menu bar",
                    isOn: $tickerOn)
                    .onChange(of: tickerOn) { _, v in
                        HabitTickerStatusItem.enabled = v
                        onChanged()
                    }
                Text("A compact rotating mini-grid beside the Iga icon: "
                     + "each habit's last 3 days (today rightmost), with "
                     + "the same rings as Compact. Cycles every few "
                     + "seconds; hidden when you have no habits.")
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            section("App") {
                HStack(spacing: 10) {
                    Button(role: .destructive) {
                        NSApplication.shared.terminate(nil)
                    } label: {
                        Label("Quit Iga", systemImage: "power")
                    }
                    Spacer()
                }
                Text("Iga keeps running in the menu bar. Quitting stops "
                     + "the menu-bar app only — /gm and the engine work "
                     + "standalone regardless.")
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(20)
        .frame(width: 420, height: 420)
    }

    private func subhead(_ t: String) -> some View {
        Text(t)
            .font(.caption).fontWeight(.semibold)
            .foregroundStyle(.primary)
    }

    @ViewBuilder
    private func section(
        _ title: String, @ViewBuilder _ content: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.caption2).fontWeight(.semibold).tracking(0.6)
                .foregroundStyle(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func exportTracker() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "habits-backup.json"
        panel.allowedContentTypes = [.json]
        panel.canCreateDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            habits.relayManage(
                habitId: nil, op: .exportTo(path: url.path))
        }
    }

    private func importTracker() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.json]
        if panel.runModal() == .OK, let url = panel.url {
            habits.relayManage(
                habitId: nil, op: .importFrom(path: url.path))
        }
    }
}

// MARK: - Window host (LSUIElement app → an explicit titled window)

@MainActor
final class SettingsWindowController {
    private let habits: HabitsWidgetStore
    private let onChanged: () -> Void
    private var window: NSWindow?

    init(habits: HabitsWidgetStore, onChanged: @escaping () -> Void) {
        self.habits = habits
        self.onChanged = onChanged
    }

    func show() {
        if let w = window {
            NSApp.activate(ignoringOtherApps: true)
            w.makeKeyAndOrderFront(nil)
            return
        }
        let view = GlobalSettingsView(
            habits: habits,
            onClose: { [weak self] in self?.window?.close() },
            onChanged: onChanged)
        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 420),
            styleMask: [.titled, .closable],
            backing: .buffered, defer: false)
        w.title = "Iga Settings"
        w.isReleasedWhenClosed = false
        w.contentViewController = NSHostingController(rootView: view)
        w.center()
        window = w
        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }
}
