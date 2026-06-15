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
    private var moodWidget: MoodWidgetStore!
    private var moodIngest: MoodIngestWatcher!
    private var emailTriage: EmailTriageWatcher!
    private var researchDispatch: ResearchDispatchWatcher!
    private var panel: PanelController!
    private var statusItemController: StatusItemController!
    private var settings: SettingsWindowController!
    private var ticker: HabitTickerStatusItem!

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
        let mw = MoodWidgetStore()
        moodWidget = mw
        // Always-on host for the semi-auto mood ingest (reliable; NOT a
        // LaunchAgent). Triggers the sanctioned ingest entry point hourly and
        // refreshes the Mood widget on success.
        let miw = MoodIngestWatcher(onIngested: { [weak mw] in
            mw?.poll()
        })
        moodIngest = miw
        // Retires the last LaunchAgent: the always-on app fires the
        // email skill's proven triage wrapper once/day (≥06:00 local).
        let etw = EmailTriageWatcher()
        emailTriage = etw
        // Out-of-session proactive research: fires the research-dispatch
        // wrapper once/day (≥06:00 local), so `/gm` stays surfacing-only.
        let rdw = ResearchDispatchWatcher()
        researchDispatch = rdw

        // ONE panel hosting BOTH columns. It never touches the engine entry point —
        // pure UI plumbing (contract-safe).
        panel = PanelController(
            store: s,
            scheduler: scheduler,
            loginItem: loginItem,
            host: wh,
            habits: hw,
            mood: mw,
            moodSync: miw,
            emailTriage: etw)

        let p = panel
        ticker = HabitTickerStatusItem(
            store: hw, onClick: { [weak p] in p?.toggle() })
        let tk = ticker
        let sw = SettingsWindowController(
            habits: hw, onChanged: { [weak tk] in tk?.refresh() })
        settings = sw
        statusItemController = StatusItemController(
            panel: panel, store: s,
            onSettings: { [weak sw] in sw?.show() })

        Notifier.shared.requestAuthorizationIfNeeded()
        // Channel C: every posted nudge also lights the menu-bar dot —
        // the always-reliable signal even if a banner is missed.
        Notifier.shared.onPosted = { [weak self] in
            self?.statusItemController.flagAttention()
        }
        s.start()
        wh.start()
        hw.start()
        mw.start()
        miw.start()
        etw.start()
        rdw.start()
    }
}
