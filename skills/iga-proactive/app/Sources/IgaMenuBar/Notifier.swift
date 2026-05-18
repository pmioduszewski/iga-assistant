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
        guard available else {
            completion(Diag(bundleOK: false, authStatus: "n/a",
                summary: "No app bundle — notifications unavailable "
                       + "(running headless / from source?)."))
            return
        }
        UNUserNotificationCenter.current().getNotificationSettings { s in
            let st: String
            switch s.authorizationStatus {
            case .notDetermined: st = "notDetermined"
            case .denied: st = "denied"
            case .authorized: st = "authorized"
            case .provisional: st = "provisional"
            case .ephemeral: st = "ephemeral"
            @unknown default: st = "unknown"
            }
            let verdict: String
            switch s.authorizationStatus {
            case .denied:
                verdict = "Denied in System Settings ▸ Notifications ▸ Iga."
            case .authorized, .provisional:
                verdict = "Authorized — banners should appear."
            default:
                verdict = "Not yet authorized — press Test to request."
            }
            DispatchQueue.main.async {
                completion(Diag(bundleOK: true, authStatus: st,
                                summary: verdict))
            }
        }
    }

    /// Request auth if needed, then post a real test notification and
    /// report the actual delivery outcome (incl. the `add` error, which
    /// is how an ad-hoc build's silent failure surfaces).
    func sendTest(_ completion: @escaping (Bool, String) -> Void) {
        guard available else {
            completion(false, "No app bundle — cannot post notifications.")
            return
        }
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound, .badge]) {
            granted, err in
            if let err = err {
                DispatchQueue.main.async {
                    completion(false, "Authorization error: "
                        + err.localizedDescription
                        + " — likely the ad-hoc signature (no Developer "
                        + "Team). See Notifications task.")
                }
                return
            }
            guard granted else {
                DispatchQueue.main.async {
                    completion(false, "Authorization not granted. Enable "
                        + "in System Settings ▸ Notifications ▸ Iga, "
                        + "or it's blocked by the ad-hoc signature.")
                }
                return
            }
            self.authorized = true
            let c = UNMutableNotificationContent()
            c.title = "Iga — test notification"
            c.body = "If you can see this, delivery works on this build. 🎉"
            c.sound = .default
            let req = UNNotificationRequest(
                identifier: "iga-test-\(Int(Date().timeIntervalSince1970))",
                content: c, trigger: nil)
            center.add(req) { addErr in
                DispatchQueue.main.async {
                    if let addErr = addErr {
                        completion(false, "Posted but failed: "
                            + addErr.localizedDescription)
                    } else {
                        completion(true, "Posted. If no banner appears "
                            + "within a few seconds, macOS is dropping it "
                            + "(ad-hoc signature) — not a wiring bug.")
                    }
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
