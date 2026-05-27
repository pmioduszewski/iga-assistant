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

    /// Set by AppDelegate → flags the menu-bar icon so a dropped/missed
    /// banner is never the only signal (channel C — always reliable, no
    /// signing). Called on the main actor after every post attempt.
    var onPosted: (() -> Void)?

    func notify(id: String, title: String, body: String) {
        guard available, !deliveredIds.contains(id) else { return }
        deliveredIds.insert(id)
        // Delivery goes through the sanctioned osascript entry point (UN is
        // dropped on this ad-hoc build). Blocking → off-main.
        DispatchQueue.global(qos: .utility).async {
            ContractGuard.runNotify(title: title, body: body)
            DispatchQueue.main.async { self.onPosted?() }
        }
    }

    // MARK: - Diagnostics + manual test (Settings ▸ Notifications)
    //
    // Honest probe of whether THIS build can actually deliver. The app is
    // ad-hoc signed (no Developer Team); macOS may silently drop
    // UNUserNotifications for such a build. The test reports the real
    // outcome instead of guessing.

    struct Diag {
        let bundleOK: Bool          // has a bundle identifier at all
        let authStatus: String      // notDetermined/denied/authorized/…
        let summary: String         // one-line human verdict
    }

    func diagnose(_ completion: @escaping (Diag) -> Void) {
        let bundle = available
        let verdict = bundle
            ? "Delivery via osascript entry point (works on ad-hoc builds). "
              + "Press Test to confirm a banner appears + grant the "
              + "one-time prompt if asked."
            : "No app bundle — running headless/from source."
        completion(Diag(bundleOK: bundle,
                        authStatus: "osascript",
                        summary: verdict))
    }

    /// Post a real test banner through the sanctioned osascript entry point and
    /// report the ACTUAL outcome (this is the honest probe).
    func sendTest(_ completion: @escaping (Bool, String) -> Void) {
        guard available else {
            completion(false, "No app bundle — cannot post notifications.")
            return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            let r = ContractGuard.runNotify(
                title: "Iga — test notification",
                body: "If you can see this banner, delivery works. 🎉")
            DispatchQueue.main.async {
                self.onPosted?()
                if r.ok {
                    completion(true, "osascript posted OK. You should see "
                        + "a banner now. (If a permission prompt appeared, "
                        + "allow it and press Test again.)")
                } else {
                    completion(false, r.detail)
                }
            }
        }
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
