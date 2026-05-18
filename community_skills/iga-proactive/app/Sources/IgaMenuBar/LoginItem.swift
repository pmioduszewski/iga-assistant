import Foundation
import Observation
import ServiceManagement

// MARK: - Login item (SMAppService)
//
// Registers the app to launch at login so the scheduler host is alive without
// the user opening it. macOS 13+ SMAppService.mainApp. Reflects current
// status; a menu toggle registers/unregisters. Pure OS plumbing — no engine
// interaction whatsoever.

@MainActor
@Observable
final class LoginItem {

    private(set) var statusText: String = "Unknown"
    private(set) var isEnabled: Bool = false

    private var service: SMAppService { SMAppService.mainApp }

    init() { refresh() }

    func refresh() {
        switch service.status {
        case .enabled:
            statusText = "Enabled"; isEnabled = true
        case .notRegistered:
            statusText = "Not registered"; isEnabled = false
        case .requiresApproval:
            statusText = "Requires approval in System Settings"
            isEnabled = false
        case .notFound:
            statusText = "Not found"; isEnabled = false
        @unknown default:
            statusText = "Unknown"; isEnabled = false
        }
    }

    func toggle() {
        do {
            if isEnabled {
                try service.unregister()
            } else {
                try service.register()
            }
        } catch {
            statusText = "Toggle failed: \(error.localizedDescription)"
        }
        refresh()
    }
}
