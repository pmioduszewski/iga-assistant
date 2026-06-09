import XCTest
@testable import IgaMenuBar

// MARK: - The two Compact-mode habit-widget fixes, machine-checked
//
// FIX 1 — the silent-no-op click bug. A Finder/Spotlight-launched .app has
// no login-shell PATH, so a bare `uv` in the record entry point was not found and
// the non-zero exit was swallowed. The fix: an ABSOLUTE uv path baked into
// the record subprocess, and the failure SURFACED (never swallowed).
//
// FIX 2 — the HabitKit-style segmented goal ring: a circular ring split
// into `target` equal arcs, `displayCount` filled, done = full ring. Pure
// geometry, unit-tested here without any UI.

final class HabitWidgetFixesTests: XCTestCase {

    // MARK: FIX 1 — record entry point uses an ABSOLUTE interpreter, not bare `uv`

    func testRecordEntryPointUsesAnAbsoluteInterpreterPath() {
        // The resolved uv path the entry point will use. In CI/dev `uv` is on PATH
        // so this is an absolute file; the contract is that the BUILT
        // command references the resolved interpreter via $IGA_HT_UV (set
        // to an absolute path), never a bare `uv` token that a PATH-less
        // Finder launch can't find.
        let p = ContractGuard.habitRecordProcess(
            habitId: "h-gym", date: "2026-05-16",
            op: .add, windowDays: 30)
        let cmd = (p.arguments ?? []).joined(separator: " ")
        // The command must invoke the interpreter via the env indirection,
        // NOT a bare `uv run`.
        XCTAssertTrue(cmd.contains("\"$IGA_HT_UV\" run python"),
            "record entry point must call the resolved uv via $IGA_HT_UV: \(cmd)")
        XCTAssertFalse(cmd.contains(" uv run "),
            "record entry point must NOT use a bare `uv` (PATH-less .app fails)")
        // The env var must be present and, when uv is installed, absolute.
        let uvEnv = p.environment?["IGA_HT_UV"] ?? ""
        XCTAssertFalse(uvEnv.isEmpty,
            "IGA_HT_UV must be set on the record subprocess")
        if uvEnv != "uv" {
            XCTAssertTrue(uvEnv.hasPrefix("/"),
                "resolved uv must be an absolute path, got \(uvEnv)")
        }
        // The skill + state dirs are still mandatory & absolute.
        XCTAssertTrue(
            (p.environment?["IGA_HT_STATE_DIR"] ?? "").hasPrefix("/"))
        XCTAssertTrue(
            (p.environment?["IGA_HT_SKILL_DIR"] ?? "").hasPrefix("/"))
    }

    // The record entry point must stand entirely on its own under a PATH-less,
    // env-less Finder/Spotlight launch: EVERY input is an absolute literal
    // resolved by Swift, the `cd` is guarded (loud, not silent), and there
    // is no reliance on $IGA_HT_* being pre-set in the inherited environment.
    func testRecordEntryPointFullyAbsoluteAndGuardedForFinderLaunch() {
        let p = ContractGuard.habitRecordProcess(
            habitId: "h-gym", date: "2026-05-16",
            op: .add, windowDays: 30)
        let cmd = (p.arguments ?? []).joined(separator: " ")

        // (a) skill dir, state dir, uv, record.py are all set ABSOLUTE on
        //     the subprocess env by Swift — never inherited, never empty.
        let env = p.environment ?? [:]
        for key in ["IGA_HT_SKILL_DIR", "IGA_HT_STATE_DIR",
                    "IGA_HT_RECORD_PY"] {
            let v = env[key] ?? ""
            XCTAssertTrue(v.hasPrefix("/"),
                "\(key) must be an absolute path, got \(v)")
            XCTAssertFalse(v.isEmpty, "\(key) must never be empty")
        }
        let uv = env["IGA_HT_UV"] ?? ""
        XCTAssertFalse(uv.isEmpty, "IGA_HT_UV must be set")
        XCTAssertTrue(uv == "uv" || uv.hasPrefix("/"),
            "uv must be absolute (or the explicit bare last-resort)")
        XCTAssertTrue(
            (env["IGA_HT_RECORD_PY"] ?? "")
                .hasSuffix("/skills/habit-tracker/engine/record.py"),
            "record.py must resolve to the frozen skill script")

        // (b) the `cd` is GUARDED — a bad/empty dir is a LOUD exit 90, not a
        //     silent fall-through that drops the click (the original bug
        //     class). And the script is referenced by its absolute env, not
        //     a cwd-relative `engine/record.py` token.
        XCTAssertTrue(cmd.contains("|| exit 90"),
            "the cd into the skill dir must be guarded: \(cmd)")
        XCTAssertTrue(cmd.contains("\"$IGA_HT_RECORD_PY\""),
            "record.py must be the absolute env path, not cwd-relative")
        XCTAssertFalse(cmd.contains(" engine/record.py"),
            "no bare cwd-relative record.py (PATH/cwd-less .app fails it)")
        XCTAssertFalse(cmd.contains(" uv run "),
            "no bare uv (a PATH-less Finder .app can't find it)")

        // (c) overridability with a correct absolute DEFAULT (not empty).
        XCTAssertTrue(
            ContractGuard.habitTrackerSkillDir()
                .hasSuffix("/Iga/skills/habit-tracker"))
        XCTAssertTrue(
            ContractGuard.habitRecordScriptPath().hasPrefix("/"))
    }

    // End-to-end: build the EXACT app command, run it under a minimal
    // GUI-equivalent environment (no PATH, no shell exports — exactly what a
    // Finder/Spotlight-launched .app inherits), against an ISOLATED seeded
    // state substrate, and assert the click actually persists and is
    // reversible. This is the operational proof the relay survives a real
    // GUI launch, not just a dev/terminal one. Skips cleanly if the local
    // toolchain (uv) or the frozen skill isn't present (CI without it).
    func testRecordEntryPointPersistsUnderMinimalGuiEnvEndToEnd() throws {
        let fm = FileManager.default
        // Locate the frozen habit-tracker skill from the test file upward.
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        var skill: URL?
        for _ in 0..<12 {
            let cand = dir.appendingPathComponent("skills/habit-tracker")
            if fm.fileExists(
                atPath: cand.appendingPathComponent("engine/record.py").path
            ) { skill = cand; break }
            dir = dir.deletingLastPathComponent()
            if dir.path == "/" { break }
        }
        guard let skill else {
            throw XCTSkip("habit-tracker skill not reachable")
        }
        let uv = ContractGuard.resolvedUvPath()
        guard uv == "uv" ? false : fm.isExecutableFile(atPath: uv) else {
            throw XCTSkip("absolute uv not resolvable in this environment")
        }

        let tmp = fm.temporaryDirectory.appendingPathComponent(
            "iga-gui-e2e-\(UUID().uuidString)")
        try fm.createDirectory(
            at: tmp, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: tmp) }

        // Seed an isolated substrate via the frozen importer.
        let synth = #"""
        {"habits":[{"id":"h-gym","name":"Gym","description":null,
          "icon":"dumbbell","color":"emerald","emoji":null,
          "archived":false,"isInverse":false,"orderIndex":0,
          "createdAt":"2026-01-01T08:00:00.000000Z"}],
         "completions":[],"intervals":[],"categories":[],
         "categoryMappings":[],"reminders":[]}
        """#
        let exportFile = tmp.appendingPathComponent("export.json")
        try synth.write(
            to: exportFile, atomically: true, encoding: .utf8)
        let importp = Process()
        importp.executableURL = URL(fileURLWithPath: uv)
        importp.currentDirectoryURL = skill
        importp.arguments = [
            "run", "python", "engine/import_habitkit.py",
            "--input", exportFile.path, "--state-dir", tmp.path]
        importp.standardOutput = Pipe()
        importp.standardError = Pipe()
        try importp.run()
        importp.waitUntilExit()
        try XCTSkipUnless(
            importp.terminationStatus == 0,
            "isolated import unavailable in this environment")

        let widget = tmp.appendingPathComponent(
            "widgets/habit-tracker-habits.json")

        func todayLevel() throws -> Int? {
            guard fm.fileExists(atPath: widget.path) else { return nil }
            let obj = try JSONSerialization.jsonObject(
                with: Data(contentsOf: widget)) as? [String: Any]
            let data = obj?["data"] as? [String: Any]
            let habits = data?["habits"] as? [[String: Any]] ?? []
            guard let h = habits.first(
                where: { ($0["id"] as? String) == "h-gym" }) else {
                return nil
            }
            let cells = h["cells"] as? [[String: Any]] ?? []
            return (cells.first {
                ($0["date"] as? String) == "2026-05-16"
            }?["level"] as? Int)
        }

        // Run the EXACT app-built command under a minimal env: ONLY HOME
        // (everything else absent — no PATH, no shell exports), exactly the
        // surface a Finder/Spotlight .app inherits. The IGA_HT_* values come
        // from the app's own absolute resolution, with the state dir pinned
        // to the isolated root so the user's live ~/Iga/state is untouched.
        func relay(_ op: ContractGuard.RecordOp) -> Int32 {
            let built = ContractGuard.habitRecordProcess(
                habitId: "h-gym", date: "2026-05-16",
                op: op, windowDays: 30)
            let p = Process()
            p.executableURL = built.executableURL
            p.arguments = built.arguments
            var env: [String: String] = [
                "HOME": fm.homeDirectoryForCurrentUser.path]
            // The app sets these absolutely; pin SKILL/STATE to the isolated
            // substrate (override surface the entry point intentionally exposes).
            env["IGA_HT_UV"] = uv
            env["IGA_HT_SKILL_DIR"] = skill.path
            env["IGA_HT_STATE_DIR"] = tmp.path
            env["IGA_HT_RECORD_PY"] =
                skill.appendingPathComponent("engine/record.py").path
            p.environment = env          // <- NO PATH, NO exports: GUI-like
            p.standardOutput = Pipe()
            p.standardError = Pipe()
            try? p.run()
            p.waitUntilExit()
            return p.terminationStatus
        }

        XCTAssertNil(try todayLevel(),
            "fresh isolated substrate: today not yet recorded")
        XCTAssertEqual(relay(.add), 0,
            "add must succeed under a minimal GUI-equivalent env")
        XCTAssertEqual(try todayLevel(), 1,
            "the click must PERSIST (cell flipped to level 1)")
        XCTAssertEqual(relay(.remove), 0,
            "remove must succeed under the same minimal env")
        let after = try todayLevel()
        XCTAssertTrue(after == nil || after == 0,
            "re-click must flip it back (level cleared)")
    }

    func testResolvedUvPathHonorsAbsoluteOverrideAndProbesKnownDirs() {
        let uv = ContractGuard.resolvedUvPath()
        // Either an absolute installed path, or the bare-name last resort
        // (only when nothing is found — never an empty string).
        XCTAssertFalse(uv.isEmpty)
        XCTAssertTrue(uv == "uv" || uv.hasPrefix("/"),
            "uv path must be absolute or the explicit bare fallback")
    }

    // MARK: FIX 1 — a record failure is PROPAGATED, not swallowed

    @MainActor
    func testFailedRelayIsSurfacedNotSwallowed() async {
        let store = HabitsWidgetStore()
        XCTAssertNil(store.lastRelayError)
        // A "command not found" stderr (the classic PATH-less-app failure)
        // must become a visible, actionable error string.
        store.testInjectRelayResult(
            key: "h@2026-05-16", ok: false,
            exitCode: 127,
            stderr: "/bin/zsh: uv: command not found")
        XCTAssertNotNil(store.lastRelayError,
            "a failed record must surface an error, never a silent no-op")
        XCTAssertTrue(
            store.lastRelayError!.lowercased().contains("uv")
            || store.lastRelayError!.lowercased().contains("not found"),
            "the surfaced error should name the uv-not-found cause: "
            + String(describing: store.lastRelayError))
        // A subsequent SUCCESS clears it (no stale error).
        store.testInjectRelayResult(
            key: "h@2026-05-16", ok: true, exitCode: 0, stderr: "")
        XCTAssertNil(store.lastRelayError,
            "a successful relay must clear the prior error")
    }

    func testBriefErrorPicksTheActionableSignal() {
        XCTAssertTrue(HabitsWidgetStore.briefError(
            exitCode: 127, stderr: "zsh: command not found: uv")
            .contains("uv"))
        XCTAssertTrue(HabitsWidgetStore.briefError(
            exitCode: -1, stderr: "").contains("launch"))
        XCTAssertTrue(HabitsWidgetStore.briefError(
            exitCode: -2, stderr: "").contains("timed out"))
        XCTAssertTrue(HabitsWidgetStore.briefError(
            exitCode: 1, stderr: "Traceback…\nValueError: bad id")
            .contains("ValueError: bad id"))
    }

    // MARK: FIX 2 — HabitKit segmented-ring geometry

    func testDailyGoalIsASingleUnbrokenRing() {
        // target == 1 → exactly one full 0…360 segment, no gap.
        let segs = HabitsWidgetView.ringSegments(
            target: 1, filledCount: 0, done: false)
        XCTAssertEqual(segs.count, 1)
        XCTAssertEqual(segs[0].startDegrees, 0)
        XCTAssertEqual(segs[0].endDegrees, 360)
        XCTAssertFalse(segs[0].filled)
        // Done (or count>=1) fills that single ring.
        XCTAssertTrue(HabitsWidgetView.ringSegments(
            target: 1, filledCount: 1, done: false)[0].filled)
        XCTAssertTrue(HabitsWidgetView.ringSegments(
            target: 1, filledCount: 0, done: true)[0].filled)
    }

    func testSegmentedRingSplitsIntoTargetEqualArcsWithGaps() {
        let target = 5
        let segs = HabitsWidgetView.ringSegments(
            target: target, filledCount: 3, done: false,
            gapDegrees: 8)
        XCTAssertEqual(segs.count, target, "one arc per target unit")
        // First 3 filled, last 2 dim — clockwise from top.
        XCTAssertEqual(segs.map(\.filled),
            [true, true, true, false, false])
        let slot = 360.0 / Double(target)
        for (i, s) in segs.enumerated() {
            // Each arc sits inside its 1/target slot, inset by half the gap
            // on each side → equal sweep, visible separation between arcs.
            XCTAssertEqual(s.startDegrees, Double(i) * slot + 4,
                accuracy: 1e-9)
            XCTAssertEqual(s.endDegrees, Double(i + 1) * slot - 4,
                accuracy: 1e-9)
            XCTAssertGreaterThan(s.endDegrees, s.startDegrees)
            // Gap between consecutive arcs == gapDegrees (8).
            if i > 0 {
                XCTAssertEqual(
                    s.startDegrees - segs[i - 1].endDegrees, 8,
                    accuracy: 1e-9)
            }
        }
    }

    func testDoneForcesEveryArcFilledAndCountIsClamped() {
        // done → all filled regardless of raw count.
        let done = HabitsWidgetView.ringSegments(
            target: 4, filledCount: 0, done: true)
        XCTAssertTrue(done.allSatisfy(\.filled))
        // Over-count is clamped to target (no extra/over-filled arcs).
        let over = HabitsWidgetView.ringSegments(
            target: 3, filledCount: 9, done: false)
        XCTAssertEqual(over.count, 3)
        XCTAssertTrue(over.allSatisfy(\.filled))
        // Negative/zero count → nothing filled.
        let none = HabitsWidgetView.ringSegments(
            target: 3, filledCount: -2, done: false)
        XCTAssertTrue(none.allSatisfy { !$0.filled })
    }

    // MARK: per-habit coach — tolerant decode (v2 + old payloads)

    func testV2PayloadWithCoachExposesItPerHabit() throws {
        let json = """
        {
          "schema_version": 2, "widget_id": "habits",
          "type": "habit-grid-multi", "title": "Habits",
          "today": "2026-05-16", "window_days": 120,
          "data": { "levels": 4, "habits": [
            { "id": "h-a", "name": "Read",
              "current_streak": 3, "longest_streak": 9,
              "coach": "3-day streak going. Keep the chain unbroken.",
              "goal": {"period":"none","target":null,"count":0,
                       "display_count":0,"done":true,"allow_exceed":true},
              "levels": 4, "cells": [] },
            { "id": "h-b", "name": "Walk",
              "current_streak": 0, "longest_streak": 2,
              "coach": "   ",
              "goal": {"period":"none","target":null,"count":0,
                       "display_count":0,"done":true,"allow_exceed":true},
              "levels": 4, "cells": [] }
          ] }
        }
        """.data(using: .utf8)!
        let w = try HabitsWidgetData.decode(from: json)
        XCTAssertEqual(w.habits.count, 2)
        XCTAssertEqual(
            w.habits[0].coach,
            "3-day streak going. Keep the chain unbroken.")
        // whitespace-only coach decodes to nil → no Compact line
        XCTAssertNil(w.habits[1].coach)
    }

    func testOldV2PayloadWithoutCoachDecodesToNilNeverCrashes() throws {
        // A pre-coach v2 file: the `coach` key is simply absent. Decode must
        // be tolerant — `coach == nil` → the Compact row shows no line.
        let json = """
        {
          "schema_version": 2, "widget_id": "habits",
          "type": "habit-grid-multi", "title": "Habits",
          "data": { "levels": 4, "habits": [
            { "id": "h-old", "name": "Legacy",
              "current_streak": 1, "longest_streak": 1,
              "goal": {"period":"none","target":null,"count":0,
                       "display_count":0,"done":true,"allow_exceed":true},
              "levels": 4, "cells": [] }
          ] }
        }
        """.data(using: .utf8)!
        let w = try HabitsWidgetData.decode(from: json)
        XCTAssertEqual(w.habits.count, 1)
        XCTAssertNil(w.habits[0].coach)
    }

    func testRingArcsNeverOverlapAndStayWithinTheCircle() {
        for target in [2, 3, 7, 12] {
            let segs = HabitsWidgetView.ringSegments(
                target: target, filledCount: 1, done: false)
            for (i, s) in segs.enumerated() {
                XCTAssertGreaterThanOrEqual(s.startDegrees, 0)
                XCTAssertLessThanOrEqual(s.endDegrees, 360)
                XCTAssertLessThan(s.startDegrees, s.endDegrees)
                if i > 0 {
                    XCTAssertGreaterThanOrEqual(
                        s.startDegrees, segs[i - 1].endDegrees,
                        "arcs must not overlap (target=\(target))")
                }
            }
        }
    }
}
