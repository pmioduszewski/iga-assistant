import AppKit
import SwiftUI

// MARK: - Optional habits ticker in the menu bar (Notion-Calendar style)
//
// A SECOND, optional `NSStatusItem` beside the brain icon. It rotates
// through the active habits and, for each, draws the SAME visual the
// Compact view uses — the last 3 days (today is the rightmost square),
// with the segmented / percentage ring for per-day-goal habits — rendered
// to a small NSImage. Render-only: it reads the already-decoded
// `HabitsWidgetStore.data` and reuses the exact same pure
// `HabitsWidgetView` helpers (compactWindow / squareStyle / fill /
// ringSegments / color). No engine call, no marking. Hidden when disabled
// or there are no habits. Click → toggle the panel.

@MainActor
final class HabitTickerStatusItem {

    /// UserDefaults flag (Global Settings → Habits → Menu bar). Default ON.
    static let enabledKey = "iga.habits.menubarTicker"
    static var enabled: Bool {
        get {
            UserDefaults.standard.object(forKey: enabledKey) as? Bool
                ?? true
        }
        set { UserDefaults.standard.set(newValue, forKey: enabledKey) }
    }

    /// PURE: the habits to rotate through — active habits NOT yet done
    /// today (the ticker is "what still needs you today"; a habit
    /// completed today drops out immediately, even though its 3-day grid
    /// would show it solid). "done today" = today's cell exists with the
    /// engine's success level > 0 (binary OR per-day-goal met); a
    /// partially-logged goal day (level 0) stays — still unfinished.
    /// Empty → the ticker hides. Unit-tested.
    nonisolated static func tickerHabits(
        _ habits: [HabitEntry], todayISO: String
    ) -> [HabitEntry] {
        habits.filter { h in
            let today = h.cells.first { $0.date == todayISO }
            return (today?.level ?? 0) <= 0
        }
    }

    private let statusItem: NSStatusItem
    private let store: HabitsWidgetStore
    private let onClick: () -> Void
    private var timer: Timer?
    private var idx = 0

    // Menu-bar geometry (small, fixed — like the contribution cells).
    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private let days = 3
    private let imgH: CGFloat = 16

    init(store: HabitsWidgetStore, onClick: @escaping () -> Void) {
        self.statusItem = NSStatusBar.system.statusItem(
            withLength: NSStatusItem.variableLength)
        self.store = store
        self.onClick = onClick
        if let b = statusItem.button {
            b.target = self
            b.action = #selector(clicked)
            b.imagePosition = .imageOnly
        }
        let t = Timer(timeInterval: 4, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.render() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
        render()
    }

    @objc private func clicked() { onClick() }

    func refresh() { render() }

    private func render() {
        guard let button = statusItem.button else { return }
        let habits = Self.enabled
            ? Self.tickerHabits(
                store.data.habits,
                todayISO: HabitsWidgetStore.systemTodayISO())
            : []
        guard !habits.isEmpty else {
            statusItem.length = 0
            button.image = nil
            return
        }
        if idx >= habits.count { idx = 0 }
        let h = habits[idx]
        let img = image(for: h)
        button.image = img
        button.toolTip =
            "\(h.name) — last \(days) days · click to open Iga"
        statusItem.length = img.size.width
        idx = (idx + 1) % habits.count
    }

    // MARK: drawing — the SAME Compact visual, menu-bar sized

    private func image(for h: HabitEntry) -> NSImage {
        let last = HabitsWidgetView.compactWindow(
            cells: h.cells,
            todayISO: HabitsWidgetStore.systemTodayISO(),
            days: days)
        let base = HabitsWidgetView.color(h.colorHex)

        // Name in the DYNAMIC system label colour (theme-aware, the same
        // high-contrast colour the OS uses for menu-bar text) — not the
        // washed-out secondary.
        let nameAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.menuBarFont(ofSize: 0),
            .foregroundColor: NSColor.labelColor,
        ]
        let name = String(h.name.prefix(14)) as NSString
        let nameSize = name.size(withAttributes: nameAttrs)
        let gridW = CGFloat(days) * cell + CGFloat(days - 1) * gap
        // Identity = a bold colour bar (Notion-style), the ONE place the
        // habit colour survives a transparent menu bar / any wallpaper.
        let barW: CGFloat = 3
        let barGap: CGFloat = 6
        let pad: CGFloat = 6
        let w = barW + barGap + ceil(nameSize.width) + pad + gridW + 2
        let size = NSSize(width: w, height: imgH)

        let img = NSImage(size: size, flipped: false) { _ in
            // colour identity bar
            let barH = self.imgH - 2
            NSColor(base).setFill()
            self.roundedRect(
                NSRect(x: 0, y: (self.imgH - barH) / 2,
                       width: barW, height: barH),
                r: barW / 2).fill()

            name.draw(
                at: NSPoint(
                    x: barW + barGap,
                    y: (self.imgH - nameSize.height) / 2),
                withAttributes: nameAttrs)
            var x = barW + barGap + ceil(nameSize.width) + pad
            let y = (self.imgH - self.cell) / 2
            for c in last {
                self.drawCell(
                    h: h, cell: c,
                    rect: NSRect(x: x, y: y,
                                 width: self.cell, height: self.cell))
                x += self.cell + self.gap
            }
            return true
        }
        img.isTemplate = false   // dynamic label colour + the colour bar
        return img
    }

    // Adaptive, Wi-Fi-style palette: the DYNAMIC system label colour
    // (white-ish on a dark menu bar, dark on a light one) at full alpha for
    // "marked"/progress and a brighter ~0.32 for "empty"/dim — legible on
    // ANY wallpaper + correct in dark/light, exactly like the native Wi-Fi
    // bars. Habit colour identity lives in the bar, not here.
    private var on: NSColor { .labelColor }
    private var dim: NSColor { NSColor.labelColor.withAlphaComponent(0.32) }
    private var track: NSColor {
        NSColor.labelColor.withAlphaComponent(0.16)
    }

    private func drawCell(
        h: HabitEntry, cell c: GridCell, rect: NSRect
    ) {
        let style = HabitsWidgetView.squareStyle(
            level: c.level, levels: h.levels,
            amount: c.amount, perDayTarget: h.goal.perDayTarget)
        switch style {
        case .flat:
            (c.level > 0 ? on : dim).setFill()
            roundedRect(rect, r: 2).fill()
        case .solid:
            on.setFill()
            roundedRect(rect, r: 2).fill()
        case let .ringSegmented(target, filled):
            dim.withAlphaComponent(0.16).setFill()
            roundedRect(rect, r: 2).fill()
            let segs = HabitsWidgetView.ringSegments(
                target: target, filledCount: filled, done: false)
            for s in segs {
                arc(in: rect,
                    from: s.startDegrees, to: s.endDegrees,
                    color: s.filled ? on : dim)
            }
        case let .ringContinuous(progress):
            track.setFill()
            roundedRect(rect, r: 2).fill()
            arc(in: rect, from: 0, to: 360, color: dim)   // track
            if progress > 0 {
                arc(in: rect, from: 0, to: 360 * progress, color: on)
            }
        }
    }

    private func roundedRect(_ r: NSRect, r radius: CGFloat) -> NSBezierPath {
        NSBezierPath(roundedRect: r, xRadius: radius, yRadius: radius)
    }

    /// Stroke a ring arc inside `rect`. `from`/`to` are degrees with 0° at
    /// 12 o'clock growing CLOCKWISE (matches `ringSegments`); converted to
    /// AppKit's CCW-from-+x convention.
    private func arc(
        in rect: NSRect, from: Double, to: Double, color: NSColor
    ) {
        let inset = rect.width * 0.16
        let r = (rect.width - inset * 2) / 2
        let center = NSPoint(x: rect.midX, y: rect.midY)
        let p = NSBezierPath()
        p.lineWidth = max(1.2, rect.width * 0.16)
        p.lineCapStyle = .round
        // top=90° in AppKit; clockwise → decreasing angle.
        let a0 = 90.0 - from
        let a1 = 90.0 - to
        p.appendArc(
            withCenter: center, radius: r,
            startAngle: a0, endAngle: a1, clockwise: true)
        color.setStroke()
        p.stroke()
    }

    deinit { timer?.invalidate() }
}
