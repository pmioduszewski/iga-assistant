import Foundation
import UserNotifications

// MARK: - Native notifications
//
// UNUserNotificationCenter. Requests authorization on first launch; posts a
// notification on: new WORKER_REQUEST, governor breaker trip, counts.done
// increment. De-dupe is the caller's job (StateStore tracks seen idempotency
// keys); we also de-dupe by request identifier as a second layer.
//
// In a non-bundle / headless context UNUserNotificationCenter.current() can
// throw — every call is defensively guarded so tests and CLI runs don't crash.

final class Notifier: NSObject, UNUserNotificationCenterDelegate {

    static let shared = Notifier()

    private var authorized = false
    private var requested = false
    private var deliveredIds: Set<String> = []
    /// Disabled in unit tests / when no bundle is present.
    private let available: Bool

    override init() {
        // Accessing .current() without an app bundle aborts; only enable when
        // we have a real bundle identifier (the assembled Iga.app).
        self.available = Bundle.main.bundleIdentifier != nil
        super.init()
    }

    func requestAuthorizationIfNeeded() {
        guard available, !requested else { return }
        requested = true
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(
            options: [.alert, .sound, .badge]) { granted, _ in
            self.authorized = granted
        }
    }

    func notify(id: String, title: String, body: String) {
        guard available, !deliveredIds.contains(id) else { return }
        deliveredIds.insert(id)
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let req = UNNotificationRequest(
            identifier: id, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(req, withCompletionHandler: nil)
    }

    // Show banners even while the app is foreground (menu open).
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler handler:
            @escaping (UNNotificationPresentationOptions) -> Void) {
        handler([.banner, .sound])
    }
}
