import Foundation

// MARK: - Widget data-file contract (v1) — pure DECODER
//
// This file is a pure DECODER for the frozen skill→app widget data-file
// contract. A widget is a *declarative spec + a data file*; the skill
// produces the data file, the app renders ONLY known widget types from it.
// The app holds ZERO widget logic beyond render primitives. We never
// construct or write this document — read only. Every field is
// optional/defaulted so a partial, stale, or garbage file decodes cleanly to
// a "waiting" state instead of crashing.
//
// Schema v1 (mirrors skills/habit-tracker/engine/producer.py exactly):
// {
//   "schema_version": 1,
//   "widget_id": str,
//   "type": "message" | "contribution-grid",
//   "title": str,
//   "generated_at": ISO8601,
//   "data": <type-specific>,
//   "coach": { "text": str, "tone": str } | null
// }
//   contribution-grid data:
//     { "label": str, "levels": int, "cells": [ {date,level}, ... ] }
//   message data:
//     { "body": str }   (optional; title + coach carry the message too)

/// Process-wide cached ISO-8601 parsers (fix #4). Both `WidgetData` and
/// `HabitsWidgetData` parse `generated_at` on every poll-decode; allocating
/// two `ISO8601DateFormatter` per access was a hot-path cost. Configured
/// once, immutable thereafter; ISO8601DateFormatter parsing is thread-safe.
enum ISO8601Cache {
    static let withFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
}

/// The v2 widget type enum. Unknown strings decode to `.unknown(raw)` so a
/// newer skill emitting a future type degrades gracefully instead of failing.
enum WidgetKind: Equatable {
    case message
    case contributionGrid
    case unknown(String)

    init(raw: String) {
        switch raw {
        case "message": self = .message
        case "contribution-grid": self = .contributionGrid
        default: self = .unknown(raw)
        }
    }

    var raw: String {
        switch self {
        case .message: return "message"
        case .contributionGrid: return "contribution-grid"
        case .unknown(let s): return s
        }
    }
}

/// One contribution-grid cell. `level` is clamped 0...levels at render time.
struct GridCell: Equatable {
    let date: String
    let level: Int
}

/// Decoded `data` block for a `contribution-grid` widget.
struct ContributionGrid: Equatable {
    var label: String = ""
    var levels: Int = 4
    var cells: [GridCell] = []
}

/// Decoded coach block — optional. The app NEVER generates this; it only
/// renders whatever text the skill wrote.
struct WidgetCoach: Equatable {
    let text: String
    let tone: String
}

/// A fully decoded widget data file.
struct WidgetData: Equatable {
    var schemaVersion: Int = 0
    var widgetId: String = ""
    var kind: WidgetKind = .unknown("")
    var title: String = ""
    var generatedAtRaw: String?
    var grid: ContributionGrid?       // present iff kind == .contributionGrid
    var messageBody: String?          // present iff kind == .message
    var coach: WidgetCoach?

    var generatedAt: Date? {
        guard let raw = generatedAtRaw else { return nil }
        if let d = ISO8601Cache.withFractional.date(from: raw) { return d }
        return ISO8601Cache.plain.date(from: raw)
    }

    /// Decode from raw JSON bytes. Tolerant: any missing/garbage field falls
    /// back to a default; only a non-JSON blob throws (caller treats a throw
    /// as the "waiting for skill" state).
    static func decode(from data: Data) throws -> WidgetData {
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let root = obj as? [String: Any] else {
            throw WidgetDecodeError.notAnObject
        }
        var w = WidgetData()
        w.schemaVersion = (root["schema_version"] as? Int) ?? 0
        w.widgetId = (root["widget_id"] as? String) ?? ""
        w.title = (root["title"] as? String) ?? ""
        w.generatedAtRaw = root["generated_at"] as? String
        w.kind = WidgetKind(raw: (root["type"] as? String) ?? "")

        if let c = root["coach"] as? [String: Any],
           let t = c["text"] as? String {
            w.coach = WidgetCoach(
                text: t, tone: (c["tone"] as? String) ?? "neutral")
        }

        let dataObj = root["data"] as? [String: Any] ?? [:]
        switch w.kind {
        case .contributionGrid:
            var g = ContributionGrid()
            g.label = (dataObj["label"] as? String) ?? ""
            g.levels = max(1, (dataObj["levels"] as? Int) ?? 4)
            if let rawCells = dataObj["cells"] as? [[String: Any]] {
                g.cells = rawCells.compactMap { c in
                    guard let d = c["date"] as? String else { return nil }
                    let lvl = (c["level"] as? Int) ?? 0
                    return GridCell(date: d, level: lvl)
                }
            }
            w.grid = g
        case .message:
            w.messageBody = dataObj["body"] as? String
        case .unknown:
            break
        }
        return w
    }
}

enum WidgetDecodeError: Error { case notAnObject }

// MARK: - Wave B: multi-habit widget data (schema_version 2) — pure DECODER
//
// A SECOND, separately-versioned data file (`habit-tracker-habits.json`,
// schema_version 2, type `habit-grid-multi`) produced by the skill's
// widget_projection. The frozen v1 decoder above is untouched. Every habit
// value here — color, streaks, goal progress, the day cells — was computed by
// the FROZEN Wave-A engine (stats.py / widget_projection). This is a pure
// decoder: it constructs nothing, computes no streak/goal/grid math, and
// never writes. A partial / stale / garbage file decodes to an empty habit
// list, never a crash.

/// One habit's active-goal progress. All fields come verbatim from the
/// engine's `stats.GoalProgress`; the app only renders them.
struct HabitGoal: Equatable {
    var period: String = "none"          // day | week | month | none
    var periodStart: String?
    var target: Int?                     // nil = no goal (always "done")
    var count: Int = 0
    var displayCount: Int = 0
    var done: Bool = true
    var allowExceed: Bool = true

    /// True iff there is an actual countable goal to show a ring for.
    var hasGoal: Bool { target != nil && period != "none" }
}

/// One habit row in the multi-habit widget. `colorHex` is the concrete sRGB
/// hex the engine resolved from the substrate's named palette — the app paints
/// exactly this and invents no semantics.
struct HabitEntry: Equatable, Identifiable {
    var id: String = ""
    var name: String = ""
    var colorHex: String = "#5B5BD6"
    var colorName: String?
    var icon: String?
    var emoji: String?
    var isInverse: Bool = false
    var archived: Bool = false
    var orderIndex: Int = 0
    var currentStreak: Int = 0
    var longestStreak: Int = 0
    /// Deterministic, engine-built coach sentence for THIS habit (no LLM,
    /// no app logic). `nil` when the payload is an old v2 file without the
    /// field or the engine emitted an empty string — the Compact row then
    /// renders no secondary line. The app NEVER generates this text.
    var coach: String?
    var goal: HabitGoal = HabitGoal()
    var levels: Int = 4
    var cells: [GridCell] = []
}

/// A fully decoded multi-habit widget data file (schema_version 2).
struct HabitsWidgetData: Equatable {
    var schemaVersion: Int = 0
    var widgetId: String = ""
    var type: String = ""
    var title: String = "Habits"
    var generatedAtRaw: String?
    var today: String?
    var windowDays: Int = 0
    var levels: Int = 4
    var habits: [HabitEntry] = []

    var generatedAt: Date? {
        guard let raw = generatedAtRaw else { return nil }
        if let d = ISO8601Cache.withFractional.date(from: raw) { return d }
        return ISO8601Cache.plain.date(from: raw)
    }

    /// Decode from raw JSON bytes. Tolerant: any missing/garbage field falls
    /// back to a default; only a non-JSON blob throws (caller treats a throw
    /// as the "waiting for skill" state). No habit logic — pure read.
    static func decode(from data: Data) throws -> HabitsWidgetData {
        let obj = try JSONSerialization.jsonObject(with: data)
        guard let root = obj as? [String: Any] else {
            throw WidgetDecodeError.notAnObject
        }
        var w = HabitsWidgetData()
        w.schemaVersion = (root["schema_version"] as? Int) ?? 0
        w.widgetId = (root["widget_id"] as? String) ?? ""
        w.type = (root["type"] as? String) ?? ""
        w.title = (root["title"] as? String) ?? "Habits"
        w.generatedAtRaw = root["generated_at"] as? String
        w.today = root["today"] as? String
        w.windowDays = (root["window_days"] as? Int) ?? 0

        let dataObj = root["data"] as? [String: Any] ?? [:]
        w.levels = max(1, (dataObj["levels"] as? Int) ?? 4)
        if let rawHabits = dataObj["habits"] as? [[String: Any]] {
            w.habits = rawHabits.compactMap { Self.decodeHabit($0) }
        }
        return w
    }

    private static func decodeHabit(_ h: [String: Any]) -> HabitEntry? {
        guard let id = h["id"] as? String, !id.isEmpty else { return nil }
        var e = HabitEntry()
        e.id = id
        e.name = (h["name"] as? String) ?? id
        e.colorHex = (h["color"] as? String) ?? "#5B5BD6"
        e.colorName = h["color_name"] as? String
        e.icon = h["icon"] as? String
        e.emoji = h["emoji"] as? String
        e.isInverse = (h["is_inverse"] as? Bool) ?? false
        e.archived = (h["archived"] as? Bool) ?? false
        e.orderIndex = (h["order_index"] as? Int) ?? 0
        e.currentStreak = (h["current_streak"] as? Int) ?? 0
        e.longestStreak = (h["longest_streak"] as? Int) ?? 0
        // Tolerant: missing key (old payload) or empty string → nil → the
        // Compact row shows no coach line. Never crashes on old files.
        if let c = h["coach"] as? String,
           !c.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            e.coach = c
        }
        e.levels = max(1, (h["levels"] as? Int) ?? 4)
        if let g = h["goal"] as? [String: Any] {
            var goal = HabitGoal()
            goal.period = (g["period"] as? String) ?? "none"
            goal.periodStart = g["period_start"] as? String
            goal.target = g["target"] as? Int
            goal.count = (g["count"] as? Int) ?? 0
            goal.displayCount = (g["display_count"] as? Int) ?? 0
            goal.done = (g["done"] as? Bool) ?? true
            goal.allowExceed = (g["allow_exceed"] as? Bool) ?? true
            e.goal = goal
        }
        if let rawCells = h["cells"] as? [[String: Any]] {
            e.cells = rawCells.compactMap { c in
                guard let d = c["date"] as? String else { return nil }
                let lvl = (c["level"] as? Int) ?? 0
                return GridCell(date: d, level: lvl)
            }
        }
        return e
    }
}
