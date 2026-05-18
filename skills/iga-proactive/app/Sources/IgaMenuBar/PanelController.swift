import SwiftUI
import AppKit
import Observation

// MARK: - The unified two-panel surface (Wave C, v2)
//
// the user's locked design, corrected: ONE single click on the menu-bar icon
// opens BOTH panels at once, side by side, board on the RIGHT, edge-to-edge,
// tops aligned. There is NO "Open board" button and NO second gesture. The
// board can NEVER land under/over/below the fundamentals — because the two
// columns are structurally one `HStack` inside ONE borderless NSPanel, the
// "board on the right" relationship is a layout invariant, not a positioning
// race.
//
// Why a fully position-controlled NSPanel (not SwiftUI MenuBarExtra): a
// `MenuBarExtra` popover's geometry is owned by AppKit and cannot be widened
// to a second column or anchored deterministically — that is exactly why the
// old build's board landed "under" it. So the status item becomes a pure
// trigger (see StatusItemController) and the entire UI is this one panel,
// anchored to the NSStatusItem button's screen frame (iStat-Menus style),
// never cursor-anchored.
//
// CONTRACT NOTE (frozen invariant, MemPalace gaia/decisions/3542bae6):
// This controller is pure UI plumbing. It RENDERS two SwiftUI columns and
// RELAYS dismissal OS events. It constructs NO Process, writes NO file,
// encodes NO JSON, opens NO sqlite handle. It cannot violate the
// render/relay/trigger contract because it never touches the engine seam at
// all — the hosted habit view relays clicks through the same single
// `ContractGuard.runRecord` seam, unchanged. Deleting the app removes this
// panel (and the whole viewer); `/gm` inline keeps working untouched.
//
// Behaviour (corrected spec):
//   • borderless, non-activating utility panel (no Dock icon — LSUIElement)
//   • ONE panel ≈764pt wide = fundamentals(380) + 4pt seam + board(380)
//   • a single status-item click shows it; clicking it again hides it
//   • outside-click (global + local monitors) and Esc close BOTH (one panel)
//   • clicking INSIDE either column keeps both open (hit-tests panel frame)
//   • anchored under the status-item button; if the pair would overflow the
//     right screen edge it shifts the WHOLE pair LEFT so both stay visible
//   • does not steal focus (non-activating; .statusBar level)

@MainActor
@Observable
final class PanelController: NSObject {

    /// Each column's fixed width. The two columns are identical width so the
    /// pair reads as one coherent product (the ASCII spec: ~380pt each).
    /// `nonisolated` so the pure-geometry `computeFrame` (and its unit test)
    /// can read these without an actor hop — they are immutable value-type
    /// constants, never engine/UI state.
    nonisolated static let columnWidth: CGFloat = 380

    /// The seam between the two columns. The spec allows a ≤2pt seam; a 1pt
    /// `Divider()` plus its surrounding zero spacing keeps the right column's
    /// LEFT edge effectively touching the left column's RIGHT edge.
    nonisolated static let seamWidth: CGFloat = 1

    /// Total panel width: left column + 1pt divider seam + right column.
    nonisolated static let panelWidth: CGFloat =
        columnWidth * 2 + seamWidth

    /// Fixed panel height. Both columns are top-aligned and share this height
    /// (the ASCII spec: "Top-aligned … comparable height").
    nonisolated static let panelHeight: CGFloat = 640

    /// Gap below the menu-bar status item so the panel reads as a dropdown.
    nonisolated static let menuBarGap: CGFloat = 6

    /// Whether the unified panel is currently visible. The status-item
    /// controller renders its toggle from this (plain property read — not a
    /// SwiftUI view binding, so a direct `@Observable` property suffices).
    private(set) var isOpen = false

    @ObservationIgnored
    private var panel: NSPanel?
    @ObservationIgnored
    private var localMonitor: Any?
    @ObservationIgnored
    private var globalMonitor: Any?

    /// The shared observable stores both columns render from. Captured once;
    /// the panel re-hosts the same stores so both columns live-update with
    /// the pollers exactly like the old popover did. Not observed state of
    /// the controller itself — they are passed straight into the SwiftUI
    /// columns, which observe them directly.
    @ObservationIgnored
    private let store: StateStore
    @ObservationIgnored
    private let scheduler: Scheduler
    @ObservationIgnored
    private let loginItem: LoginItem
    @ObservationIgnored
    private let host: WidgetHostStore
    @ObservationIgnored
    private let habits: HabitsWidgetStore
    @ObservationIgnored
    private let mood: MoodWidgetStore
    @ObservationIgnored
    private let moodSync: MoodIngestWatcher

    /// Resolves the status-item button's frame in screen coordinates so the
    /// pair anchors under the icon (NOT the cursor). Injected by the status
    /// item controller; nil → fall back to a top-trailing screen anchor.
    /// Plumbing callback, not observed UI state.
    @ObservationIgnored
    var statusItemScreenFrame: (() -> NSRect?)?

    init(store: StateStore,
         scheduler: Scheduler,
         loginItem: LoginItem,
         host: WidgetHostStore,
         habits: HabitsWidgetStore,
         mood: MoodWidgetStore,
         moodSync: MoodIngestWatcher) {
        self.store = store
        self.scheduler = scheduler
        self.loginItem = loginItem
        self.host = host
        self.habits = habits
        self.mood = mood
        self.moodSync = moodSync
        super.init()
    }

    // MARK: open / close / toggle — always BOTH together (one panel)

    func toggle() {
        isOpen ? close() : open()
    }

    func open() {
        guard !isOpen else { return }
        let panel = makePanelIfNeeded()
        position(panel)
        panel.orderFrontRegardless()
        installDismissMonitors()
        isOpen = true
    }

    func close() {
        guard isOpen else { return }
        removeDismissMonitors()
        panel?.orderOut(nil)
        isOpen = false
    }

    // MARK: panel construction — ONE panel, two columns, board on the RIGHT

    private func makePanelIfNeeded() -> NSPanel {
        if let p = panel { return p }

        // Borderless, non-activating utility panel. `.nonactivatingPanel`
        // keeps it from stealing key focus from the foreground app (iStat
        // behaviour); `.utilityWindow` keeps it out of the window cycle.
        let p = NSPanel(
            contentRect: NSRect(
                x: 0, y: 0,
                width: Self.panelWidth, height: Self.panelHeight),
            styleMask: [.borderless, .nonactivatingPanel, .utilityWindow],
            backing: .buffered,
            defer: true)

        p.isFloatingPanel = true
        // .statusBar so it sits above normal windows AND above other floating
        // panels, like a real menu-bar dropdown.
        p.level = .statusBar
        p.hidesOnDeactivate = false
        p.isReleasedWhenClosed = false
        p.becomesKeyOnlyIfNeeded = true
        p.worksWhenModal = false
        p.collectionBehavior = [
            .canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        p.isMovableByWindowBackground = false
        p.backgroundColor = .clear
        p.hasShadow = true

        // Concrete root view (no AnyView — fix #3): the HStack is hosted via
        // a named `RootView` struct so the hierarchy keeps its static type
        // (better diffing, no type erasure). The layout invariant lives in
        // `RootView`: fundamentals LEFT, 1pt seam, board RIGHT, zero outer
        // spacing → siblings in one HStack in one window (the board can never
        // render under/over the fundamentals).
        let hosting = NSHostingView(rootView: RootView(
            store: store,
            scheduler: scheduler,
            loginItem: loginItem,
            host: host,
            habits: habits,
            mood: mood,
            moodSync: moodSync,
            onClose: { [weak self] in self?.close() }))
        hosting.translatesAutoresizingMaskIntoConstraints = true
        hosting.autoresizingMask = [.width, .height]

        // Rounded card so the borderless panel reads as a popover.
        let container = NSView(frame: NSRect(
            x: 0, y: 0,
            width: Self.panelWidth, height: Self.panelHeight))
        container.wantsLayer = true
        container.layer?.cornerRadius = 10
        container.layer?.masksToBounds = true
        hosting.frame = container.bounds
        container.addSubview(hosting)
        p.contentView = container

        panel = p
        return p
    }

    // MARK: anchoring — under the STATUS ITEM, not the cursor; clamp on-screen

    /// Compute the panel's screen origin so its TOP sits just under the
    /// menu-bar status-item button and its columns are fully on-screen. If the
    /// pair would overflow the right edge, the WHOLE pair shifts LEFT so both
    /// columns stay visible (never clip the board). This is the iStat-Menus
    /// anchoring model: anchor to the NSStatusItem button frame, not the
    /// mouse. Pure geometry; no engine interaction.
    ///
    /// Exposed `static` so a unit test can assert the invariant
    /// (board.origin.x ≥ fundamentals.maxX, tops aligned) without a running
    /// UI, for both the normal case and the right-screen-edge case.
    nonisolated static func computeFrame(
        statusItemFrame: NSRect?,
        screenVisibleFrame: NSRect
    ) -> NSRect {
        let vf = screenVisibleFrame
        let w = panelWidth
        let h = panelHeight

        // Desired: the panel's horizontal CENTER under the status-item's
        // center; its TOP `menuBarGap` below the status item's bottom.
        let desiredCenterX: CGFloat
        let topY: CGFloat
        if let s = statusItemFrame {
            desiredCenterX = s.midX
            topY = s.minY - menuBarGap
        } else {
            // No status item resolvable → top-trailing of the screen.
            desiredCenterX = vf.maxX - w / 2 - 8
            topY = vf.maxY - menuBarGap
        }

        var originX = desiredCenterX - w / 2
        // Clamp INSIDE the visible frame. The right-edge overflow case: shift
        // the whole pair LEFT (originX decreases) so the board's right edge
        // stays on-screen — the board is never clipped.
        if originX + w > vf.maxX - 8 {
            originX = vf.maxX - 8 - w
        }
        if originX < vf.minX + 8 {
            originX = vf.minX + 8
        }

        // Cocoa origin is bottom-left; topY is the desired TOP, so the origin
        // y is top - height. Clamp so the bottom stays on-screen too.
        var originY = topY - h
        if originY < vf.minY + 8 {
            originY = vf.minY + 8
        }
        if originY + h > vf.maxY {
            originY = vf.maxY - h
        }

        return NSRect(x: originX, y: originY, width: w, height: h)
    }

    private func position(_ panel: NSPanel) {
        let sFrame = statusItemScreenFrame?()
        // Choose the screen that contains the status item (its midpoint), or
        // the screen under the mouse, or main — in that order.
        let screen: NSScreen? = {
            if let s = sFrame {
                let mid = NSPoint(x: s.midX, y: s.midY)
                if let hit = NSScreen.screens.first(where: {
                    $0.frame.contains(mid)
                }) { return hit }
            }
            if let hit = NSScreen.screens.first(where: {
                $0.frame.contains(NSEvent.mouseLocation)
            }) { return hit }
            return NSScreen.main ?? NSScreen.screens.first
        }()
        guard let screen else { return }

        let frame = Self.computeFrame(
            statusItemFrame: sFrame,
            screenVisibleFrame: screen.visibleFrame)
        panel.setFrame(frame, display: false)
    }

    // MARK: dismissal — outside-click + Esc, closing BOTH (one panel)

    private func installDismissMonitors() {
        removeDismissMonitors()

        // Esc closes both (local: only while one of our windows is up).
        localMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.keyDown]) { [weak self] event in
            if event.keyCode == 53 {            // Esc
                self?.close()
                return nil
            }
            return event
        }

        // Any click OUTSIDE the panel closes both. A click inside either
        // column is inside the single panel frame, so both stay open. This is
        // the iStat "click anywhere outside to dismiss" behaviour, applied to
        // the whole pair atomically (it is one window).
        globalMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]) {
            [weak self] _ in
            guard let self, let panel = self.panel else { return }
            let mouse = NSEvent.mouseLocation
            if !panel.frame.contains(mouse) {
                self.close()
            }
        }
    }

    private func removeDismissMonitors() {
        if let m = localMonitor {
            NSEvent.removeMonitor(m)
            localMonitor = nil
        }
        if let m = globalMonitor {
            NSEvent.removeMonitor(m)
            globalMonitor = nil
        }
    }

    deinit {
        if let m = localMonitor { NSEvent.removeMonitor(m) }
        if let m = globalMonitor { NSEvent.removeMonitor(m) }
    }
}

// MARK: - The concrete two-column root (no AnyView — fix #3)
//
// The single hosted view for the unified panel. Extracting it as a named
// `struct ... : View` removes the `AnyView(root)` type erasure: the view
// hierarchy keeps its static type, which SwiftUI can diff precisely. This is
// pure layout plumbing — it owns NO state, computes nothing, and just hands
// the @Observable stores (received as plain `let`s, the single injection
// path — fix #5) to the two columns. THE layout invariant is here:
// fundamentals on the LEFT, a 1pt divider seam, board on the RIGHT, zero
// outer spacing → the two columns are siblings in one HStack in one window,
// so the board can never render under/over/below the fundamentals.
struct RootView: View {
    let store: StateStore
    let scheduler: Scheduler
    let loginItem: LoginItem
    let host: WidgetHostStore
    let habits: HabitsWidgetStore
    let mood: MoodWidgetStore
    let moodSync: MoodIngestWatcher
    let onClose: () -> Void

    var body: some View {
        HStack(spacing: 0) {
            FundamentalsView(
                store: store,
                scheduler: scheduler,
                loginItem: loginItem)
                .frame(width: PanelController.columnWidth,
                       height: PanelController.panelHeight,
                       alignment: .top)

            Divider()
                .frame(width: PanelController.seamWidth)

            BoardPanelView(
                host: host,
                habits: habits,
                mood: mood,
                moodSync: moodSync,
                store: store,
                onClose: onClose)
                .frame(width: PanelController.columnWidth,
                       height: PanelController.panelHeight,
                       alignment: .top)
        }
        .frame(width: PanelController.panelWidth,
               height: PanelController.panelHeight,
               alignment: .top)
    }
}
