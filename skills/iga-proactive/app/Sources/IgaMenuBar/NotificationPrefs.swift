import Foundation

// MARK: - Notification preferences (per-source toggles)
//
// A tiny UserDefaults-backed gate so the user can silence any one class of
// notification without killing the others. All sources default to TRUE so
// a first-launch app is fully chatty; the user opts OUT, not in.
//
// API:
//   NotificationPrefs.enabled(.mood)          → Bool
//   NotificationPrefs.set(.mood, false)
//
// The Settings UI binds Toggles to these; every call site in StateStore,
// HabitsWidgetStore, MoodWidgetStore (and, when wired, EmailTriageWatcher)
// guards its `notifier.notify(...)` with `NotificationPrefs.enabled(src)`.
// The test-notification button in GlobalSettings is NOT gated — it must
// always fire so the user can verify macOS auth regardless of these prefs.

enum NotificationSource: String, CaseIterable {
    /// StateStore: worker-queued / breaker-tripped / done-count increments.
    case proactive    = "iga.notif.proactive"
    /// HabitsWidgetStore: daily habit-accountability nudge.
    case habit        = "iga.notif.habit"
    /// MoodWidgetStore: daily mood check-in nudge.
    case mood         = "iga.notif.mood"
    /// EmailTriageWatcher: triage result surfacing (toggle ready for when
    /// the seam exposes a per-run notification; currently always true).
    case emailTriage  = "iga.notif.emailTriage"
}

enum NotificationPrefs {

    /// Whether notifications from `source` are enabled.
    /// Defaults to `true` — the key is absent until the user disables it.
    static func enabled(_ source: NotificationSource) -> Bool {
        let d = UserDefaults.standard
        // `object(forKey:)` is nil when the key has never been set → default true.
        guard let stored = d.object(forKey: source.rawValue) as? Bool
        else { return true }
        return stored
    }

    /// Persist a preference.
    static func set(_ source: NotificationSource, _ value: Bool) {
        UserDefaults.standard.set(value, forKey: source.rawValue)
    }
}
