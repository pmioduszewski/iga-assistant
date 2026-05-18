import SwiftUI
import AppKit

// MARK: - App entry point
//
// Menu-bar-only (LSUIElement=true in Info.plist via build.sh), NO Dock icon.
//
// Wave C v2 (the user's corrected design): the SwiftUI `MenuBarExtra` is GONE.
// Its popover geometry is owned by AppKit and cannot host a deterministic
// two-column layout — which is exactly why the previous build's board landed
// "under" the popover. Instead:
//
//   • an AppDelegate owns a real `NSStatusItem` (StatusItemController) that
//     is a PURE TRIGGER — one click toggles the whole UI;
//   • the entire UI is ONE position-controlled borderless `NSPanel`
//     (PanelController) ≈764pt wide: the FUNDAMENTALS column on the LEFT and
//     the widget BOARD column on the RIGHT, edge-to-edge, tops aligned,
//     shown SIMULTANEOUSLY by that single click;
//   • the panel anchors under the status-item button's screen frame
//     (iStat-Menus style), never the cursor, and clamps fully on-screen.
//
// Invariant restated for any future reader: deleting this app removes the
// scheduler host + viewer + notifier only. `/gm` calling the engine in-session
// keeps working with zero external infrastructure. See ContractGuard.swift
// and the README "Hard contract" section.

@main
struct IgaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        // No visible scene: the whole UI is the AppDelegate's NSStatusItem +
        // NSPanel. `Settings` renders nothing for an LSUIElement app and gives
        // SwiftUI a valid (empty) scene graph.
        Settings {
            EmptyView()
        }
    }
}

// MARK: - AppDelegate: owns the status item + the unified two-column panel

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var store: StateStore!
    private var scheduler: Scheduler!
    private var loginItem: LoginItem!
    private var widgetHost: WidgetHostStore!
    private var habitsWidget: HabitsWidgetStore!
    private var panel: PanelController!
    private var statusItemController: StatusItemController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Belt-and-suspenders: LSUIElement already makes this accessory, but
        // assert it so the app never grabs a Dock icon / steals focus.
        NSApp.setActivationPolicy(.accessory)

        let s = StateStore()
        store = s
        scheduler = Scheduler(store: s)
        loginItem = LoginItem()
        let wh = WidgetHostStore()
        widgetHost = wh
        let hw = HabitsWidgetStore()
        habitsWidget = hw

        // ONE panel hosting BOTH columns. It never touches the engine seam —
        // pure UI plumbing (contract-safe).
        panel = PanelController(
            store: s,
            scheduler: scheduler,
            loginItem: loginItem,
            host: wh,
            habits: hw)

        statusItemController = StatusItemController(
            panel: panel, store: s)

        Notifier.shared.requestAuthorizationIfNeeded()
        s.start()
        wh.start()
        hw.start()
    }
}
