import Foundation
import Observation

// MARK: - Mood widget store (pure read-poll — ZERO engine seam)
//
// The mood-tracker's second-instance analogue of HabitsWidgetStore, but
// strictly READ-ONLY: the Mood grid is an analytics surface, never a
// marking one. This store holds NO mood logic and NEVER spawns a
// subprocess — it only polls the skill-produced `mood-tracker-mood.json`
// (schema_version 2, type `mood-grid`) and exposes the decoded per-day
// dominant-quadrant cells for the view. Marking/logging happens through
// the sanctioned `engine/record.py` chat seam, not here. Deleting the app
// removes this poller only; the engine + CLI keep working standalone.
//
// Robustness mirrors HabitsWidgetStore: an absent/partial/garbage file
// becomes empty data + a benign reason, never a crash. The producer
// writes via tmp+os.replace, so a reader sees whole-old or whole-new.

/// One civil day in the Mood grid: its dominant mood-meter quadrant
/// colour (hex) + how many emotions were logged. `count == 0` ⇒ the dim
/// "none" tile. The app invents none of this — it's verbatim engine output.
struct MoodDayCell: Equatable {
    let date: String
    let colorHex: String
    let quadrant: String
    let count: Int
}

/// One recent log surfaced for the "mood now ← previous" row. Emotion
/// display name + quadrant colour + timestamp ONLY — the engine never
/// includes the free-text note here (privacy).
/// One feeling within a log (a log may carry several — primary +
/// secondary, like the source app). Each has its OWN quadrant colour.
struct MoodPart: Equatable {
    let name: String
    let colorHex: String
}

struct MoodRecent: Equatable {
    let date: String
    let ts: String
    let emotion: String
    let quadrant: String
    let colorHex: String
    /// One entry per ';'-joined feeling. Never empty (decoder falls back
    /// to a single part from `emotion`/`colorHex` for old payloads).
    let parts: [MoodPart]
}

struct MoodWidgetData: Equatable {
    var schemaVersion: Int = 0
    var widgetId: String = ""
    var type: String = ""
    var title: String = "Mood"
    var label: String = ""
    var coachText: String = ""
    var cells: [MoodDayCell] = []
    /// Newest-first (max 2): [latest, previous]. Empty on old payloads.
    var recent: [MoodRecent] = []
    /// Quadrant→hex from the engine (`PALETTE`). Used for the legend +
    /// the no-log tile; never hard-coded in Swift.
    var palette: [String: String] = [:]

    /// Tolerant decoder — any missing/garbage field degrades to a default;
    /// only a non-JSON blob throws (caller treats a throw as "waiting").
    /// No mood logic — pure read of the v2 `mood-grid` payload.
    static func decode(from data: Data) throws -> MoodWidgetData {
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let root = obj as? [String: Any] else {
            throw WidgetDecodeError.notAnObject
        }
        var w = MoodWidgetData()
        w.schemaVersion = (root["schema_version"] as? Int) ?? 0
        w.widgetId = (root["widget_id"] as? String) ?? ""
        w.type = (root["type"] as? String) ?? ""
        w.title = (root["title"] as? String) ?? "Mood"
        if let p = root["palette"] as? [String: Any] {
            for (k, v) in p { if let s = v as? String { w.palette[k] = s } }
        }
        if let c = root["coach"] as? [String: Any],
           let t = c["text"] as? String {
            w.coachText = t
        }
        let dataObj = root["data"] as? [String: Any] ?? [:]
        w.label = (dataObj["label"] as? String) ?? ""
        if let raw = dataObj["qcells"] as? [[String: Any]] {
            w.cells = raw.compactMap { c in
                guard let d = c["date"] as? String else { return nil }
                return MoodDayCell(
                    date: d,
                    colorHex: (c["color"] as? String) ?? "",
                    quadrant: (c["quadrant"] as? String) ?? "none",
                    count: (c["count"] as? Int) ?? 0)
            }
        }
        if let raw = dataObj["recent"] as? [[String: Any]] {
            w.recent = raw.compactMap { r in
                guard let e = r["emotion"] as? String,
                      let ts = r["ts"] as? String else { return nil }
                let colorHex = (r["color"] as? String) ?? ""
                var parts: [MoodPart] = []
                if let rp = r["parts"] as? [[String: Any]] {
                    parts = rp.compactMap { p in
                        guard let nm = p["emotion"] as? String
                        else { return nil }
                        return MoodPart(
                            name: nm,
                            colorHex: (p["color"] as? String) ?? colorHex)
                    }
                }
                if parts.isEmpty {                 // old-payload fallback
                    parts = [MoodPart(name: e, colorHex: colorHex)]
                }
                return MoodRecent(
                    date: (r["date"] as? String) ?? "",
                    ts: ts, emotion: e,
                    quadrant: (r["quadrant"] as? String) ?? "unknown",
                    colorHex: colorHex, parts: parts)
            }
        }
        return w
    }
}

@MainActor
@Observable
final class MoodWidgetStore {

    private(set) var data = MoodWidgetData()
    private(set) var waitingReason: String? = "waiting for mood-tracker"
    private(set) var lastPolled: Date?

    @ObservationIgnored
    private(set) var pollInterval: TimeInterval = 60
    @ObservationIgnored
    private var timer: Timer?

    init() {
        if let env = ProcessInfo.processInfo
            .environment["IGA_WIDGET_POLL_SECONDS"],
           let v = TimeInterval(env), v >= 2 {
            pollInterval = v
        }
    }

    /// Resolve the data file the same way HabitsWidgetStore does: an
    /// explicit override, else `$IGA_STATE_DIR`, else `~/Gaia/state`.
    nonisolated static func dataPath() -> String {
        let env = ProcessInfo.processInfo.environment
        if let f = env["IGA_MOOD_DATA_FILE"], !f.isEmpty { return f }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let stateDir = env["IGA_STATE_DIR"].flatMap {
            $0.isEmpty ? nil : $0
        } ?? "\(home)/Gaia/state"
        return "\(stateDir)/widgets/mood-tracker-mood.json"
    }

    func start() {
        poll()
        let t = Timer(timeInterval: pollInterval, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.poll() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    /// Read + decode. Never throws to the caller — degrades to empty +
    /// a benign reason. Pure read; no subprocess, ever.
    func poll() {
        lastPolled = Date()
        let path = Self.dataPath()
        guard FileManager.default.fileExists(atPath: path) else {
            data = MoodWidgetData()
            waitingReason = "waiting for mood-tracker"
            return
        }
        guard let bytes = try? Data(
            contentsOf: URL(fileURLWithPath: path)) else {
            waitingReason = "waiting for mood-tracker (unreadable)"
            return
        }
        if bytes.isEmpty {
            waitingReason = "waiting for mood-tracker (writing…)"
            return
        }
        if let decoded = try? MoodWidgetData.decode(from: bytes) {
            data = decoded
            waitingReason = decoded.cells.contains(where: { $0.count > 0 })
                ? nil
                : "No moods yet — log one in chat, then it appears here."
        } else {
            waitingReason = "mood-tracker: unreadable data file"
        }
    }
}
