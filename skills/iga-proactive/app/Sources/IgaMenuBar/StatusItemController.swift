import SwiftUI
import AppKit

// MARK: - The menu-bar status item = a PURE TRIGGER
//
// the user's corrected design: the menu-bar icon is ONLY a toggle. A single
// click shows BOTH panels (fundamentals + board) at once; clicking it again
// hides BOTH. There is no popover content on the status item anymore — the
// SwiftUI `MenuBarExtra` was removed entirely because its popover geometry is
// owned by AppKit and cannot host a deterministic two-column layout (that is
// exactly why the old build's board landed "under" the popover).
//
// This owns the real `NSStatusItem` so we can read its button's window frame
// in screen coordinates and anchor the unified panel directly under the icon
// — the iStat-Menus model, never cursor-anchored.
//
// CONTRACT NOTE: pure UI plumbing. No Process, no file write, no JSON, no
// sqlite. It only flips `PanelController.toggle()` and updates the glyph from
// already-decoded engine state. Deleting the app removes the status item and
// the panel; `/gm` inline keeps working untouched.

@MainActor
final class StatusItemController: NSObject {

    private let statusItem: NSStatusItem
    private let panel: PanelController
    private let store: StateStore
    private var healthObservation: Task<Void, Never>?
    /// Opens the app-wide settings window (right-click → "Iga Settings…").
    private let onSettings: () -> Void

    init(
        panel: PanelController, store: StateStore,
        onSettings: @escaping () -> Void = {}
    ) {
        self.statusItem = NSStatusBar.system.statusItem(
            withLength: NSStatusItem.variableLength)
        self.panel = panel
        self.store = store
        self.onSettings = onSettings
        super.init()

        if let button = statusItem.button {
            button.image = Self.glyph(
                breakerTripped: store.state.governor.breakerTripped)
            button.image?.isTemplate = true
            button.target = self
            button.action = #selector(statusItemClicked(_:))
            button.toolTip =
                "Iga — left-click: show/hide · right-click: settings"
            // Left toggles the panel; right opens the settings menu.
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        // The panel asks US for the status-item button's screen frame so it
        // anchors under the icon, not the cursor. `button.window` gives the
        // status-bar window; convert the button bounds to screen space.
        panel.statusItemScreenFrame = { [weak self] in
            self?.statusItemButtonScreenFrame()
        }

        // Keep the glyph honest as engine state changes (breaker trip flips
        // the brain → exclamation, exactly like the old MenuBarExtra label).
        startGlyphSync()
    }

    /// The status-item button's frame in SCREEN coordinates. This is the
    /// anchor the unified panel hangs under (iStat-Menus style). Returns nil
    /// if the button/window can't be resolved (panel then falls back to a
    /// top-trailing screen anchor).
    private func statusItemButtonScreenFrame() -> NSRect? {
        guard let button = statusItem.button,
              let window = button.window else { return nil }
        let inWindow = button.convert(button.bounds, to: nil)
        return window.convertToScreen(inWindow)
    }

    @objc private func statusItemClicked(_ sender: Any?) {
        // Right-click (or control-click) → the settings menu; otherwise the
        // left-click toggles the unified panel (unchanged behaviour).
        let ev = NSApp.currentEvent
        let isRight = ev?.type == .rightMouseUp
            || (ev?.type == .leftMouseUp
                && ev?.modifierFlags.contains(.control) == true)
        if isRight {
            showContextMenu()
            return
        }
        panel.toggle()
        statusItem.button?.highlight(panel.isOpen)
    }

    private func showContextMenu() {
        let menu = NSMenu()
        let settings = NSMenuItem(
            title: "Iga Settings…",
            action: #selector(openSettings),
            keyEquivalent: ",")
        settings.target = self
        menu.addItem(settings)
        menu.addItem(.separator())
        let quit = NSMenuItem(
            title: "Quit Iga",
            action: #selector(quitIga),
            keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        // Pop the menu under the status item, then clear (so the menu
        // doesn't become the permanent left-click behaviour).
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    @objc private func openSettings() { onSettings() }
    @objc private func quitIga() { NSApplication.shared.terminate(nil) }

    // MARK: glyph

    private static func glyph(breakerTripped: Bool) -> NSImage? {
        let name = breakerTripped
            ? "exclamationmark.brain"
            : "brain.head.profile"
        return NSImage(
            systemSymbolName: name,
            accessibilityDescription: "Iga")
    }

    /// Mirror the breaker state into the glyph on a light poll. Pure render
    /// of an already-decoded value — no logic, no decision.
    private func startGlyphSync() {
        healthObservation = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let tripped = self.store.state.governor.breakerTripped
                if let button = self.statusItem.button {
                    let img = Self.glyph(breakerTripped: tripped)
                    img?.isTemplate = true
                    button.image = img
                }
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    deinit {
        healthObservation?.cancel()
    }
}
