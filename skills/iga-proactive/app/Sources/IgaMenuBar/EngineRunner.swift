import Foundation

// MARK: - Engine runner (thin delegate)
//
// Triggers the frozen engine scan. ALL subprocess handling lives in
// ContractGuard (the single contract entry point). This type exists only to give
// callers a stable name and the public result shape; it contains no
// subprocess primitive and no job/admission logic.
//
// The engine itself writes the state file + ledger; this app only reads the
// result afterward.

typealias EngineRunResult = ContractGuard.ScanOutcome

enum EngineRunner {
    /// Run `engine scan --json` once via the sanctioned ContractGuard entry point.
    static func runScan(timeout: TimeInterval = 90) -> EngineRunResult {
        ContractGuard.runScan(timeout: timeout)
    }
}
