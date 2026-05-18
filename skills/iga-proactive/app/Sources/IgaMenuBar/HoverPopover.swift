import SwiftUI

// MARK: - Delayed hover (shared UX primitive)
//
// A bare `.onHover` toggles instantly, so a popover flickers as the
// pointer crosses it — bad UX (the user called this out for the habit
// coach popovers). `onHoverDelayed` waits a short, cancellable interval
// before reporting "hovering = true" (cursor must DWELL), and reports
// "false" immediately on exit (no lingering popover). Pure UI; no engine
// logic, no subprocess, no writes — render-only, contract-safe.

private struct DelayedHover: ViewModifier {
    let delay: Double
    let action: (Bool) -> Void
    @State private var task: Task<Void, Never>?

    func body(content: Content) -> some View {
        content.onHover { inside in
            task?.cancel()
            if inside {
                task = Task { @MainActor in
                    try? await Task.sleep(
                        nanoseconds: UInt64(delay * 1_000_000_000))
                    if !Task.isCancelled { action(true) }
                }
            } else {
                // Leave is immediate — never strand a popover open.
                action(false)
            }
        }
    }
}

extension View {
    /// Report hover state, but only become `true` after the pointer has
    /// dwelt `delay` seconds (default 0.45 s); `false` fires immediately
    /// on exit. Cancellable: leaving before the delay elapses suppresses
    /// the show entirely.
    func onHoverDelayed(
        _ delay: Double = 0.45,
        perform action: @escaping (Bool) -> Void
    ) -> some View {
        modifier(DelayedHover(delay: delay, action: action))
    }
}
