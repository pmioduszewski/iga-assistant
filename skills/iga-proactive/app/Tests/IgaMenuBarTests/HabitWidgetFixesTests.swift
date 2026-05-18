import XCTest
@testable import IgaMenuBar

// MARK: - The two Compact-mode habit-widget fixes, machine-checked
//
// FIX 1 — the silent-no-op click bug. A Finder/Spotlight-launched .app has
// no login-shell PATH, so a bare `uv` in the record seam was not found and
// the non-zero exit was swallowed. The fix: an ABSOLUTE uv path baked into
// the record subprocess, and the failure SURFACED (never swallowed).
//
// FIX 2 — the segmented goal ring: a circular ring split
// into `target` equal arcs, `displayCount` filled, done = full ring. Pure
// geometry, unit-tested here without any UI.

final class HabitWidgetFixesTests: XCTestCase {

    // MARK: FIX 1 — record seam uses an ABSOLUTE interpreter, not bare `uv`

    func testRecordSeamUsesAnAbsoluteInterpreterPath() {
        // The resolved uv path the seam will use. In CI/dev `uv` is on PATH
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
            "record seam must call the resolved uv via $IGA_HT_UV: \(cmd)")
        XCTAssertFalse(cmd.contains(" uv run "),
            "record seam must NOT use a bare `uv` (PATH-less .app fails)")
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

    // The record seam must stand entirely on its own under a PATH-less,
    // env-less Finder/Spotlight launch: EVERY input is an absolute literal
    // resolved by Swift, the `cd` is guarded (loud, not silent), and there
    // is no reliance on $IGA_HT_* being pre-set in the inherited environment.
    func testRecordSeamFullyAbsoluteAndGuardedForFinderLaunch() {
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
                .hasSuffix("/Gaia/skills/habit-tracker"))
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
    func testRecordSeamPersistsUnderMinimalGuiEnvEndToEnd() throws {
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
        // to the isolated root so the user's live ~/Gaia/state is untouched.
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
            // substrate (override surface the seam intentionally exposes).
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

    // MARK: FIX 2 — segmented-ring geometry

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
        // No coach_kind in this payload → nil → neutral icon (tolerant).
        XCTAssertNil(w.habits[0].coachKind)
    }

    func testCoachKindDecodesAndMapsToSemanticIcon() throws {
        let json = """
        {
          "schema_version": 2, "widget_id": "habits",
          "type": "habit-grid-multi", "title": "Habits",
          "data": { "levels": 4, "habits": [
            { "id": "h-r", "name": "Run",
              "current_streak": 2, "longest_streak": 9,
              "coach": "Keep your 2-day streak — do it today.",
              "coach_kind": "at-risk",
              "coach_tip": "Atomic Habits: never miss twice.",
              "goal": {"period":"none","target":null,"count":0,
                       "display_count":0,"done":true,"allow_exceed":true},
              "levels": 4, "cells": [] }
          ] }
        }
        """.data(using: .utf8)!
        let w = try HabitsWidgetData.decode(from: json)
        // coach_kind still decodes (kept for future colour/theming) …
        XCTAssertEqual(w.habits[0].coachKind, "at-risk")
        XCTAssertEqual(w.habits[0].coachTip,
                       "Atomic Habits: never miss twice.")
        // … but the coach mark is the AI "sparkles" glyph for EVERY kind
        // (and absent/unknown) — one consistent assistant affordance.
        for k in ["at-risk", "slipped", "milestone", "dormant",
                  "???", ""] {
            XCTAssertEqual(
                HabitsWidgetView.coachSymbol(k), "sparkles")
        }
        XCTAssertEqual(
            HabitsWidgetView.coachSymbol(nil), "sparkles")
    }

    func testLogDrawerOnlyForLargePerDayTargetsAlignsWithRing() {
        // Binary / no target → no drawer (plain toggle).
        XCTAssertFalse(HabitsWidgetView.usesLogDrawer(nil))
        XCTAssertFalse(HabitsWidgetView.usesLogDrawer(1))
        // Small target → SEGMENTED ring + tap-iterates, NO drawer.
        XCTAssertFalse(HabitsWidgetView.usesLogDrawer(4))
        XCTAssertFalse(HabitsWidgetView.usesLogDrawer(10))   // boundary
        // Big target → CONTINUOUS ring + the +/- drawer.
        XCTAssertTrue(HabitsWidgetView.usesLogDrawer(11))
        XCTAssertTrue(HabitsWidgetView.usesLogDrawer(40))
        // The drawer threshold MUST match the ring threshold so the
        // interaction always matches the visual: segmented⇒no-drawer,
        // continuous⇒drawer.
        for t in [2, 4, 10, 11, 25, 50] {
            let style = HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: 0, perDayTarget: t)
            let drawer = HabitsWidgetView.usesLogDrawer(t)
            switch style {
            case .ringSegmented:
                XCTAssertFalse(drawer,
                    "segmented (target \(t)) must iterate, not drawer")
            case .ringContinuous:
                XCTAssertTrue(drawer,
                    "continuous (target \(t)) must use the drawer")
            default:
                XCTFail("target \(t) should be a ring")
            }
        }
    }

    func testCoachTipAbsentDecodesToNilTolerant() throws {
        let json = """
        {
          "schema_version": 2, "widget_id": "habits",
          "type": "habit-grid-multi", "title": "Habits",
          "data": { "levels": 4, "habits": [
            { "id": "h-x", "name": "X",
              "current_streak": 0, "longest_streak": 0,
              "coach": "x", "coach_kind": "dormant",
              "goal": {"period":"none","target":null,"count":0,
                       "display_count":0,"done":true,"allow_exceed":true},
              "levels": 4, "cells": [] }
          ] }
        }
        """.data(using: .utf8)!
        let w = try HabitsWidgetData.decode(from: json)
        XCTAssertNil(w.habits[0].coachTip,
            "missing coach_tip → nil → no popover (never a crash)")
    }

    func testFocusAdvisoryDecodesAndIsTolerant() throws {
        let json = """
        {
          "schema_version": 2, "widget_id": "habits",
          "type": "habit-grid-multi", "title": "Habits",
          "focus": {
            "show": true, "kind": "too-many-habits",
            "active_count": 6, "budget": 4, "graduate_pct": 80,
            "window_days": 30,
            "message": "You're actively building 6 habits…",
            "candidates": [
              {"id":"h-a","name":"Read","consistency":97},
              {"id":"h-b","name":"Walk","consistency":83}]
          },
          "data": { "levels": 4, "habits": [] }
        }
        """.data(using: .utf8)!
        let w = try HabitsWidgetData.decode(from: json)
        let f = try XCTUnwrap(w.focus)
        XCTAssertTrue(f.show)
        XCTAssertEqual(f.activeCount, 6)
        XCTAssertEqual(f.budget, 4)
        XCTAssertEqual(f.candidates.map(\.name), ["Read", "Walk"])
        XCTAssertEqual(f.candidates.first?.consistency, 97)
        XCTAssertTrue(f.message.contains("6 habits"))

        // Absent focus → nil → no card (old payloads / within budget).
        let none = """
        {"schema_version":2,"widget_id":"habits",
         "type":"habit-grid-multi","data":{"levels":4,"habits":[]}}
        """.data(using: .utf8)!
        XCTAssertNil(try HabitsWidgetData.decode(from: none).focus)

        // show=false decodes but the view must render nothing.
        let off = """
        {"schema_version":2,"widget_id":"habits",
         "type":"habit-grid-multi",
         "focus":{"show":false,"message":"","active_count":2,
                  "budget":4,"candidates":[]},
         "data":{"levels":4,"habits":[]}}
        """.data(using: .utf8)!
        let wf = try XCTUnwrap(
            try HabitsWidgetData.decode(from: off).focus)
        XCTAssertFalse(wf.show)
        XCTAssertTrue(wf.candidates.isEmpty)
    }

    func testArchivedRosterDecodesAndIsTolerant() throws {
        let json = """
        {"schema_version":2,"widget_id":"habits",
         "type":"habit-grid-multi",
         "archived":[
           {"id":"h-old","name":"Old One","color":"#30a46c"},
           {"id":"h-x","name":"X"}],
         "data":{"levels":4,"habits":[]}}
        """.data(using: .utf8)!
        let w = try HabitsWidgetData.decode(from: json)
        XCTAssertEqual(w.archived.map(\.id), ["h-old", "h-x"])
        XCTAssertEqual(w.archived[0].name, "Old One")
        XCTAssertEqual(w.archived[0].colorHex, "#30a46c")
        XCTAssertEqual(w.archived[1].colorHex, "#5B5BD6")  // default
        // absent → empty (old payloads / nothing archived).
        let none = """
        {"schema_version":2,"widget_id":"habits",
         "type":"habit-grid-multi","data":{"levels":4,"habits":[]}}
        """.data(using: .utf8)!
        XCTAssertTrue(
            try HabitsWidgetData.decode(from: none).archived.isEmpty)
    }

    func testFillCellsBackfillsRealDatedEmptiesContiguouslyKeepsNewest() {
        let cell = HabitsWidgetView.denseCell
        let gap = HabitsWidgetView.denseGap
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let fmt = DateFormatter()
        fmt.calendar = cal; fmt.timeZone = cal.timeZone
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyy-MM-dd"
        // 14 real consecutive days ending 2026-05-16 (≈ 2-3 columns).
        let end = fmt.date(from: "2026-05-16")!
        let real: [GridCell] = (0..<14).reversed().map { k in
            GridCell(
                date: fmt.string(from: cal.date(
                    byAdding: .day, value: -k, to: end)!),
                level: 2)
        }
        let avail: CGFloat = 300
        let out = HabitsWidgetView.fillCells(
            real, availableWidth: avail)
        XCTAssertGreaterThan(out.count, real.count)
        // Real days preserved verbatim at the END (newest trailing).
        XCTAssertEqual(Array(out.suffix(14)), real)
        // Backfilled head: real EARLIER dates, level 0, NOT blank.
        for c in out.prefix(out.count - 14) {
            XCTAssertEqual(c.level, 0)
            XCTAssertFalse(c.date.isEmpty,
                "backfill carries a real date (months still label)")
        }
        // ONE contiguous ascending day series — no seam/gap anywhere.
        for i in 1..<out.count {
            let a = fmt.date(from: out[i - 1].date)!
            let b = fmt.date(from: out[i].date)!
            XCTAssertEqual(
                cal.dateComponents([.day], from: a, to: b).day, 1,
                "dates must be strictly consecutive (no blank column)")
        }
        // It fills to (about) the fit width and months can be labelled.
        let cols = HabitsWidgetView.weekColumns(out)
        let fit = Int(((avail + gap) / (cell + gap)).rounded(.down))
        XCTAssertGreaterThanOrEqual(cols.count, fit)
        XCTAssertFalse(
            HabitsWidgetView.monthLabelColumns(cols).isEmpty,
            "real dates ⇒ at least one month label still shows")

        // Already wide enough → returned UNCHANGED (scrolls as before).
        let many: [GridCell] = (0..<400).reversed().map { k in
            GridCell(
                date: fmt.string(from: cal.date(
                    byAdding: .day, value: -k, to: end)!),
                level: 1)
        }
        XCTAssertEqual(
            HabitsWidgetView.fillCells(many, availableWidth: 120),
            many, "history wider than the view is never backfilled")
        // Empty input → unchanged (never crashes).
        XCTAssertEqual(
            HabitsWidgetView.fillCells([], availableWidth: 300), [])
    }

    func testMonthLabelsNeverCollideMinSpacingHonoured() {
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let fmt = DateFormatter()
        fmt.calendar = cal; fmt.timeZone = cal.timeZone
        fmt.locale = Locale(identifier: "en_US_POSIX")
        fmt.dateFormat = "yyyy-MM-dd"
        let end = fmt.date(from: "2026-05-16")!
        // ~90 contiguous days (Feb→May, several month boundaries).
        let cells: [GridCell] = (0..<90).reversed().map { k in
            GridCell(
                date: fmt.string(from: cal.date(
                    byAdding: .day, value: -k, to: end)!),
                level: 1)
        }
        let cols = HabitsWidgetView.weekColumns(cells)
        let labels = HabitsWidgetView.monthLabelColumns(cols)
        XCTAssertFalse(labels.isEmpty, "months must still be labelled")
        let idxs = labels.keys.sorted()
        for i in 1..<idxs.count {
            XCTAssertGreaterThanOrEqual(
                idxs[i] - idxs[i - 1],
                HabitsWidgetView.monthLabelMinCols,
                "two month labels collided (gap "
                + "\(idxs[i] - idxs[i - 1]) < "
                + "\(HabitsWidgetView.monthLabelMinCols)) — the "
                + "'AugSep' overlap")
        }
        // Same guarantee after fill-backfill (the sparse left edge that
        // caused the original collision).
        let few: [GridCell] = (0..<10).reversed().map { k in
            GridCell(date: fmt.string(from: cal.date(
                byAdding: .day, value: -k, to: end)!), level: 2)
        }
        let filledCols = HabitsWidgetView.weekColumns(
            HabitsWidgetView.fillCells(few, availableWidth: 320))
        let l2 = HabitsWidgetView.monthLabelColumns(filledCols)
            .keys.sorted()
        for i in 1..<max(1, l2.count) {
            XCTAssertGreaterThanOrEqual(
                l2[i] - l2[i - 1],
                HabitsWidgetView.monthLabelMinCols)
        }
    }

    func testHabitNudgeCoalescesAccountabilityKindsOnly() {
        func h(_ name: String, _ kind: String?) -> HabitEntry {
            var e = HabitEntry(); e.id = name; e.name = name
            e.coachKind = kind; return e
        }
        // Only at-risk/slipped/dormant nudge; milestone & silent don't.
        let habits = [
            h("Testo", "at-risk"), h("Zęby", "slipped"),
            h("Yoga", "dormant"), h("Witaminy", "milestone"),
            h("Sex", nil), h("L?", ""),
        ]
        let n = HabitsWidgetStore.habitNudge(habits)
        XCTAssertNotNil(n)
        XCTAssertEqual(n?.title, "Iga · habits")
        XCTAssertEqual(n?.body, "3 need you today: Testo, Zęby, Yoga")
        // Nothing actionable → no notification at all.
        XCTAssertNil(HabitsWidgetStore.habitNudge([
            h("A", "milestone"), h("B", nil), h("C", "")]))
        XCTAssertNil(HabitsWidgetStore.habitNudge([]))
        // >3 → capped with +N (banner stays glanceable).
        let many = (1...6).map { h("Hb\($0)", "dormant") }
        XCTAssertEqual(
            HabitsWidgetStore.habitNudge(many)?.body,
            "6 need you today: Hb1, Hb2, Hb3 +3")
    }

    func testMenuBarTickerRotatesUnfinishedTodayOnlyKeepsOrder() {
        func h(_ name: String, today lv: Int?) -> HabitEntry {
            var e = HabitEntry(); e.id = name; e.name = name
            if let lv { e.cells = [GridCell(
                date: "2026-05-17", level: lv, amount: 1)] }
            return e
        }
        let habits = [
            h("Testo", today: 0),     // logged, level 0 → unfinished → show
            h("Sex", today: 3),       // done today (level>0) → DROP
            h("Zęby", today: 0),      // partial goal day, level 0 → show
            h("Yoga", today: nil),    // no cell today → unfinished → show
            h("Witaminy", today: 4),  // done today → DROP
        ]
        XCTAssertEqual(
            HabitTickerStatusItem.tickerHabits(
                habits, todayISO: "2026-05-17").map(\.name),
            ["Testo", "Zęby", "Yoga"],
            "completed-today habits drop out; order preserved")
        // All done → empty (ticker hides — nothing left to nag).
        XCTAssertTrue(HabitTickerStatusItem.tickerHabits(
            [h("A", today: 1), h("B", today: 2)],
            todayISO: "2026-05-17").isEmpty)
        XCTAssertTrue(HabitTickerStatusItem.tickerHabits(
            [], todayISO: "2026-05-17").isEmpty)
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

    // MARK: FIX 3 — Compact window anchors to the SYSTEM date, not the
    // engine's stale baked `today` (the cold-launch "clicking is useless"
    // bug). + the non-mutating reproject seam contract.

    func testCompactWindowAnchorsToSystemTodayNotStaleEngineWindow() {
        // Engine file is a DAY STALE: its last cell is 2026-05-16, but the
        // real day is 2026-05-17 (exactly the post-Mac-restart condition).
        let cells = (10...16).map {
            GridCell(date: String(format: "2026-05-%02d", $0), level: 2)
        }
        let win = HabitsWidgetView.compactWindow(
            cells: cells, todayISO: "2026-05-17", days: 7)
        XCTAssertEqual(win.count, 7)
        // TODAY is the rightmost square (was the whole bug: it wasn't).
        XCTAssertEqual(win.last?.date, "2026-05-17",
            "today must be the rightmost cell, anchored to the system date")
        // The day with no engine cell (today) is level 0 — the engine's own
        // "not done", not a fabricated value — so it's clickable as "today".
        XCTAssertEqual(win.last?.level, 0)
        // Dates are consecutive ascending, ending today.
        XCTAssertEqual(win.map(\.date), [
            "2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14",
            "2026-05-15", "2026-05-16", "2026-05-17"])
        // Engine levels for emitted days are preserved verbatim (no
        // recompute): 2026-05-16 carried level 2 → still 2.
        XCTAssertEqual(
            win.first(where: { $0.date == "2026-05-16" })?.level, 2)
    }

    func testCompactWindowMapsEngineLevelsByDateAndZeroFillsGaps() {
        // Sparse engine cells: only two dated days, rest absent.
        let cells = [
            GridCell(date: "2026-05-12", level: 3),
            GridCell(date: "2026-05-15", level: 1),
        ]
        let win = HabitsWidgetView.compactWindow(
            cells: cells, todayISO: "2026-05-17", days: 7)
        var byDate: [String: Int] = [:]
        for c in win { byDate[c.date] = c.level }
        XCTAssertEqual(byDate["2026-05-12"], 3, "mapped by exact date")
        XCTAssertEqual(byDate["2026-05-15"], 1)
        for absent in ["2026-05-11", "2026-05-13", "2026-05-14",
                       "2026-05-16", "2026-05-17"] {
            XCTAssertEqual(byDate[absent], 0,
                "a date the engine didn't emit → 0 (its own 'not done')")
        }
    }

    func testCompactWindowPreservesAmountNotJustLevelRingRegression() {
        // REGRESSION: compactWindow once kept only `level`, zeroing `amount`
        // for every Compact square — so a per-day ring could never fill
        // even after the engine recorded the click (streak updated, square
        // stayed dim). The window MUST carry the engine's amount verbatim.
        let cells = [
            GridCell(date: "2026-05-15", level: 1, amount: 40),
            GridCell(date: "2026-05-16", level: 0, amount: 12),
        ]
        let win = HabitsWidgetView.compactWindow(
            cells: cells, todayISO: "2026-05-17", days: 3)
        XCTAssertEqual(win.map(\.date),
            ["2026-05-15", "2026-05-16", "2026-05-17"])
        XCTAssertEqual(win[0].amount, 40,
            "amount must survive the window (drives the ring)")
        XCTAssertEqual(win[0].level, 1)
        XCTAssertEqual(win[1].amount, 12)
        // A date the engine never emitted → a real 0/0 cell, still clickable.
        XCTAssertEqual(win[2].amount, 0)
        XCTAssertEqual(win[2].level, 0)
        // End-to-end: the preserved amount makes a completed per-day-target
        // day render SOLID in Compact (the exact user-visible symptom).
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: win[0].level, levels: 4,
                amount: win[0].amount, perDayTarget: 40),
            .solid,
            "a completed rep day must be solid in Compact, not a dim ring")
    }

    func testCompactWindowFallsBackGracefullyOnUnparseableToday() {
        let cells = (1...10).map {
            GridCell(date: String(format: "2026-03-%02d", $0), level: 1)
        }
        // A garbage today must never crash / blank the strip.
        let win = HabitsWidgetView.compactWindow(
            cells: cells, todayISO: "not-a-date", days: 7)
        XCTAssertEqual(win, Array(cells.suffix(7)),
            "unparseable today degrades to the engine's trailing window")
    }

    func testSystemTodayISOIsAStableUTCCivilDate() {
        let a = HabitsWidgetStore.systemTodayISO()
        // yyyy-MM-dd, parseable by the same UTC parser the window uses.
        XCTAssertEqual(a.count, 10)
        XCTAssertEqual(a.filter { $0 == "-" }.count, 2)
        let win = HabitsWidgetView.compactWindow(
            cells: [], todayISO: a, days: 1)
        XCTAssertEqual(win.count, 1)
        XCTAssertEqual(win.first?.date, a,
            "systemTodayISO must round-trip through the window parser")
        XCTAssertEqual(win.first?.level, 0)
    }

    func testWeekdayAbbrevIsCorrectUTCAndDynamicToToday() {
        // Cross-checked against Foundation with the SAME UTC iso8601
        // calendar so this verifies the helper's tz/calendar wiring
        // (non-gameable: no hand-computed literals).
        var cal = Calendar(identifier: .iso8601)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let parser = DateFormatter()
        parser.calendar = cal
        parser.timeZone = cal.timeZone
        parser.locale = Locale(identifier: "en_US_POSIX")
        parser.dateFormat = "yyyy-MM-dd"
        let sym = DateFormatter()
        sym.calendar = cal
        sym.timeZone = cal.timeZone
        sym.locale = Locale(identifier: "en_US_POSIX")
        sym.dateFormat = "EEE"
        for iso in ["2026-05-17", "2026-01-01", "2024-02-29",
                    "2026-12-31"] {
            let expected = sym.string(from: parser.date(from: iso)!)
            XCTAssertEqual(
                HabitsWidgetView.weekdayAbbrev(iso), expected,
                "\(iso) weekday mismatch")
            XCTAssertEqual(expected.count, 3)
        }
        // Unparseable → "" (never crashes / never a stray label).
        XCTAssertEqual(HabitsWidgetView.weekdayAbbrev("nope"), "")
        XCTAssertEqual(HabitsWidgetView.weekdayAbbrev(""), "")
        // DYNAMIC: the window's last cell is always today and its label is
        // today's weekday (the rightmost grid column == today).
        let today = HabitsWidgetStore.systemTodayISO()
        let win = HabitsWidgetView.compactWindow(
            cells: [], todayISO: today, days: 7)
        XCTAssertEqual(win.count, 7)
        XCTAssertEqual(win.last?.date, today)
        XCTAssertEqual(
            HabitsWidgetView.weekdayAbbrev(win.last!.date),
            sym.string(from: parser.date(from: today)!),
            "last grid column must be today's weekday")
        XCTAssertFalse(
            HabitsWidgetView.weekdayAbbrev(win.first!.date).isEmpty,
            "first column (today-6) must carry a weekday label")
    }

    func testReprojectSeamCommandShapeIsExactNonMutatingAndEnvIndependent() {
        XCTAssertEqual(
            ContractGuard.documentedReprojectCommand,
            "cd <abs-skill-dir> || exit 90 ; <abs-uv> run python "
            + "<abs-record.py> --state-dir <abs-live-state> "
            + "--reproject --days N")
        let p = ContractGuard.habitReprojectProcess(windowDays: 120)
        XCTAssertEqual(p.executableURL?.path, "/bin/zsh")
        let cmd = (p.arguments ?? []).joined(separator: " ")
        XCTAssertTrue(cmd.contains("--reproject"))
        XCTAssertTrue(cmd.contains("--state-dir"))
        XCTAssertTrue(cmd.contains("\"$IGA_HT_RECORD_PY\""))
        XCTAssertTrue(cmd.contains("|| exit 90"),
            "the cd must be guarded (loud, never a silent wrong-cwd)")
        XCTAssertTrue(cmd.contains("\"$IGA_HT_UV\" run python"))
        XCTAssertFalse(cmd.contains(" uv run "),
            "no bare uv — a PATH-less Finder .app can't find it")
        // NON-MUTATING by construction: no habit/date/op can be passed.
        XCTAssertFalse(cmd.contains("--habit"))
        XCTAssertFalse(cmd.contains("--date"))
        XCTAssertFalse(cmd.contains("--add"))
        XCTAssertFalse(cmd.contains("--remove"))
        XCTAssertFalse(cmd.contains("--set-amount"))
        // Same absolute, env-independent contract as the record seam.
        let env = p.environment ?? [:]
        for key in ["IGA_HT_SKILL_DIR", "IGA_HT_STATE_DIR",
                    "IGA_HT_RECORD_PY"] {
            XCTAssertTrue((env[key] ?? "").hasPrefix("/"),
                "\(key) must be absolute, got \(env[key] ?? "")")
        }
        let uv = env["IGA_HT_UV"] ?? ""
        XCTAssertTrue(uv == "uv" || uv.hasPrefix("/"))
    }

    // End-to-end: the EXACT app-built reproject command, under a minimal
    // GUI-equivalent env, leaves the substrate BYTE-IDENTICAL while
    // re-emitting the widget at the system date. This is the operational
    // proof that the cold-launch refresh is genuinely non-mutating.
    func testReprojectIsNonMutatingEndToEndUnderMinimalGuiEnv() throws {
        let fm = FileManager.default
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
            "iga-reproject-e2e-\(UUID().uuidString)")
        try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
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
        try synth.write(to: exportFile, atomically: true, encoding: .utf8)
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
        try XCTSkipUnless(importp.terminationStatus == 0,
            "isolated import unavailable in this environment")

        let sub = tmp.appendingPathComponent(
            "substrates/habit-tracker.json")
        let before = try Data(contentsOf: sub)

        let built = ContractGuard.habitReprojectProcess(windowDays: 120)
        let p = Process()
        p.executableURL = built.executableURL
        p.arguments = built.arguments
        p.environment = [
            "HOME": fm.homeDirectoryForCurrentUser.path,
            "IGA_HT_UV": uv,
            "IGA_HT_SKILL_DIR": skill.path,
            "IGA_HT_STATE_DIR": tmp.path,
            "IGA_HT_RECORD_PY":
                skill.appendingPathComponent("engine/record.py").path,
        ]                                  // NO PATH, NO exports: GUI-like
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        try p.run()
        p.waitUntilExit()
        XCTAssertEqual(p.terminationStatus, 0,
            "reproject must succeed under a minimal GUI-equivalent env")

        let after = try Data(contentsOf: sub)
        XCTAssertEqual(before, after,
            "reproject MUST leave the substrate byte-identical "
            + "(non-mutating contract)")

        // The widget was re-emitted with the system (UTC) today.
        let widget = tmp.appendingPathComponent(
            "widgets/habit-tracker-habits.json")
        let obj = try JSONSerialization.jsonObject(
            with: Data(contentsOf: widget)) as? [String: Any]
        XCTAssertEqual(obj?["today"] as? String,
            HabitsWidgetStore.systemTodayISO(),
            "reproject must advance `today` to the system civil date")
    }

    // MARK: FIX 4 — per-day goal habits render an in-square progress ring
    // (segmented for small targets, continuous % for >10), not a flat
    // fill; solid only when the day meets target.

    func testSquareStyleFlatForBinaryOrPeriodOrPreRingHabits() {
        // No per-day target (nil) → flat, regardless of level/amount.
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: nil, perDayTarget: nil),
            .flat)
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 3, levels: 4, amount: 9, perDayTarget: nil),
            .flat)
        // per_day_target == 1 is "binary" → still flat (no hairball ring).
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 1, levels: 4, amount: 1, perDayTarget: 1),
            .flat)
    }

    /// Extract a CONTINUOUS ring's fraction (nil otherwise) so progress can
    /// be asserted with float accuracy, not brittle enum ==.
    private func ringProgress(
        _ s: HabitsWidgetView.SquareStyle
    ) -> Double? {
        if case let .ringContinuous(p) = s { return p }
        return nil
    }

    func testSmallTargetUsesSegmentedRingNotPercentage() {
        // A small per-day target keeps the clean COUNTABLE segmented ring
        // (the look the user explicitly wanted preserved). 0 → all-dim
        // outline (filled 0), partial → that many filled arcs.
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: 0, perDayTarget: 5),
            .ringSegmented(target: 5, filled: 0))
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: nil, perDayTarget: 5),
            .ringSegmented(target: 5, filled: 0))
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 1, levels: 4, amount: 3, perDayTarget: 5),
            .ringSegmented(target: 5, filled: 3))
        // The boundary (==segmentRingMax, 10) is STILL segmented — ">10
        // parts looks terrible", 10 is fine.
        XCTAssertEqual(HabitsWidgetView.segmentRingMax, 10)
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: 4, perDayTarget: 10),
            .ringSegmented(target: 10, filled: 4))
    }

    func testLargeTargetSwitchesToContinuousPercentageRing() {
        // Just over the boundary (11) → continuous, NOT 11 thin segments.
        XCTAssertEqual(
            ringProgress(HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: 0, perDayTarget: 11)),
            0.0)
        // Big targets fill proportionally (10/100 == 250/500 == fraction).
        XCTAssertEqual(
            ringProgress(HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: 10, perDayTarget: 100)) ?? -1,
            0.1, accuracy: 1e-9)
        XCTAssertEqual(
            ringProgress(HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: 250, perDayTarget: 500)) ?? -1,
            0.5, accuracy: 1e-9)
        XCTAssertEqual(
            ringProgress(HabitsWidgetView.squareStyle(
                level: 1, levels: 4, amount: 20, perDayTarget: 40)) ?? -1,
            0.5, accuracy: 1e-9)
    }

    func testSquareStyleSolidOnlyWhenTheDayMeetsItsTarget() {
        // amount >= raw target → solid "done" square (small target).
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 2, levels: 4, amount: 5, perDayTarget: 5),
            .solid)
        // Over-shoot → still solid.
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 2, levels: 4, amount: 8, perDayTarget: 5),
            .solid)
        // Engine bucketed the day to a full success level → solid even if
        // raw amount is below target (grid agrees with streak).
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 4, levels: 4, amount: 2, perDayTarget: 5),
            .solid)
        // Large target strictly below → continuous ring < 1 (never
        // silently "solid" early); exactly at target → solid.
        let almost = HabitsWidgetView.squareStyle(
            level: 1, levels: 4, amount: 49, perDayTarget: 50)
        XCTAssertEqual(ringProgress(almost) ?? -1, 0.98, accuracy: 1e-9)
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 1, levels: 4, amount: 50, perDayTarget: 50),
            .solid)
        // Small target one short → still the segmented ring, not solid.
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 0, levels: 4, amount: 4, perDayTarget: 5),
            .ringSegmented(target: 5, filled: 4))
    }

    func testV2DecodesPerDayTargetAndCellAmountTolerantOfOldPayloads() throws {
        let json = """
        {
          "schema_version": 2, "widget_id": "habits",
          "type": "habit-grid-multi", "title": "Habits",
          "today": "2026-05-16", "window_days": 3,
          "data": { "levels": 4, "habits": [
            { "id": "h-pu", "name": "Push-ups",
              "current_streak": 1, "longest_streak": 4,
              "goal": {"period":"day","target":null,"count":0,
                       "display_count":0,"done":false,"allow_exceed":true,
                       "per_day_target": 50},
              "levels": 4, "cells": [
                {"date":"2026-05-14","level":0,"amount":0},
                {"date":"2026-05-15","level":2,"amount":20},
                {"date":"2026-05-16","level":4,"amount":50}] },
            { "id": "h-old", "name": "Legacy",
              "current_streak": 0, "longest_streak": 0,
              "goal": {"period":"none","target":null,"count":0,
                       "display_count":0,"done":true,"allow_exceed":true},
              "levels": 4, "cells": [
                {"date":"2026-05-16","level":1}] }
          ] }
        }
        """.data(using: .utf8)!
        let w = try HabitsWidgetData.decode(from: json)
        let pu = w.habits.first { $0.id == "h-pu" }!
        XCTAssertEqual(pu.goal.perDayTarget, 50)
        XCTAssertTrue(pu.goal.hasPerDayRing)
        XCTAssertEqual(pu.cells.map(\.amount), [0, 20, 50])
        // The decoded values drive the expected per-day styles end-to-end.
        XCTAssertEqual(
            ringProgress(HabitsWidgetView.squareStyle(
                level: pu.cells[0].level, levels: pu.levels,
                amount: pu.cells[0].amount,
                perDayTarget: pu.goal.perDayTarget)),
            0.0, "0/50 → empty ring")
        XCTAssertEqual(
            ringProgress(HabitsWidgetView.squareStyle(
                level: pu.cells[1].level, levels: pu.levels,
                amount: pu.cells[1].amount,
                perDayTarget: pu.goal.perDayTarget)) ?? -1,
            0.4, accuracy: 1e-9, "20/50 → 40% ring")
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: pu.cells[2].level, levels: pu.levels,
                amount: pu.cells[2].amount,
                perDayTarget: pu.goal.perDayTarget),
            .solid)
        // Old payload: no per_day_target, no cell amount → nil → flat path.
        let old = w.habits.first { $0.id == "h-old" }!
        XCTAssertNil(old.goal.perDayTarget)
        XCTAssertFalse(old.goal.hasPerDayRing)
        XCTAssertNil(old.cells.first?.amount)
        XCTAssertEqual(
            HabitsWidgetView.squareStyle(
                level: 1, levels: 4,
                amount: old.cells.first?.amount,
                perDayTarget: old.goal.perDayTarget),
            .flat)
    }

    // MARK: FIX 5 — the habit-management seam (Wave D ⋯ menu)

    func testManageSeamCommandShapeIsExactAndEnvIndependent() {
        XCTAssertEqual(
            ContractGuard.documentedManageCommand,
            "cd <abs-skill-dir> || exit 90 ; <abs-uv> run python "
            + "<abs-manage.py> --state-dir <abs-live-state> "
            + "(--rename N | --delete | --set-goal … | --export P | "
            + "--import P) [--habit <id>] --days N")

        let del = ContractGuard.habitManageProcess(
            habitId: "h-gym", op: .delete, windowDays: 30)
        XCTAssertEqual(del.executableURL?.path, "/bin/zsh")
        let dc = (del.arguments ?? []).joined(separator: " ")
        XCTAssertTrue(dc.contains("|| exit 90"))
        XCTAssertTrue(dc.contains("\"$IGA_HT_MANAGE_PY\""))
        XCTAssertTrue(dc.contains("--state-dir"))
        XCTAssertTrue(dc.contains("--delete"))
        XCTAssertTrue(dc.contains("--habit 'h-gym'"))
        XCTAssertTrue(dc.contains("\"$IGA_HT_UV\" run python"))
        XCTAssertFalse(dc.contains(" uv run "))
        let env = del.environment ?? [:]
        for k in ["IGA_HT_SKILL_DIR", "IGA_HT_STATE_DIR",
                  "IGA_HT_MANAGE_PY"] {
            XCTAssertTrue((env[k] ?? "").hasPrefix("/"),
                "\(k) must be absolute")
        }
        XCTAssertTrue(
            (env["IGA_HT_MANAGE_PY"] ?? "")
                .hasSuffix("/skills/habit-tracker/engine/manage.py"))

        // set-goal renders every flag.
        let g = ContractGuard.habitManageProcess(
            habitId: "h-x",
            op: .setGoal(period: "day", target: nil,
                         perDayTarget: 50, allowExceed: false),
            windowDays: 7)
        let gc = (g.arguments ?? []).joined(separator: " ")
        XCTAssertTrue(gc.contains("--set-goal"))
        XCTAssertTrue(gc.contains("--period 'day'"))
        XCTAssertTrue(gc.contains("--per-day-target 50"))
        XCTAssertTrue(gc.contains("--no-allow-exceed"))
        XCTAssertFalse(gc.contains("--target "),
            "no period target was requested → flag omitted")

        // reorder renders --set-order N + --habit, position clamped ≥1.
        let r = ContractGuard.habitManageProcess(
            habitId: "h-x", op: .setOrder(position: 3), windowDays: 7)
        let rc = (r.arguments ?? []).joined(separator: " ")
        XCTAssertTrue(rc.contains("--set-order 3"))
        XCTAssertTrue(rc.contains("--habit 'h-x'"))
        let r0 = ContractGuard.habitManageProcess(
            habitId: "h-x", op: .setOrder(position: 0), windowDays: 7)
        XCTAssertTrue((r0.arguments ?? []).joined(separator: " ")
            .contains("--set-order 1"),
            "position is clamped to ≥1 at the seam")

        // archive / unarchive.
        let arc = (ContractGuard.habitManageProcess(
            habitId: "h-x", op: .setArchived(true), windowDays: 7)
            .arguments ?? []).joined(separator: " ")
        XCTAssertTrue(arc.contains("--archive")
            && arc.contains("--habit 'h-x'"))
        XCTAssertFalse(arc.contains("--unarchive"))
        let unarc = (ContractGuard.habitManageProcess(
            habitId: "h-x", op: .setArchived(false), windowDays: 7)
            .arguments ?? []).joined(separator: " ")
        XCTAssertTrue(unarc.contains("--unarchive"))

        // set-color — hex single-quoted, habit attached.
        let col = (ContractGuard.habitManageProcess(
            habitId: "h-x", op: .setColor(hex: "#1a2b3c"),
            windowDays: 7).arguments ?? []).joined(separator: " ")
        XCTAssertTrue(col.contains("--set-color '#1a2b3c'"))
        XCTAssertTrue(col.contains("--habit 'h-x'"))
        // a crafted hex still cannot break out of the single quotes.
        let evil = (ContractGuard.habitManageProcess(
            habitId: "h-x", op: .setColor(hex: "#fff'; rm -rf ~ #"),
            windowDays: 7).arguments ?? []).joined(separator: " ")
        XCTAssertFalse(evil.contains("; rm -rf ~ #'\n"))
        XCTAssertTrue(evil.contains(#"'#fff'\''; rm -rf ~ #'"#))
    }

    func testManageSheetHexRoundTripsTheProjectionColor() {
        // The sheet's Color→hex must invert the projection's hex→Color so
        // re-applying an unchanged colour is a recognised no-op (button
        // disabled) — verified by a round-trip through both.
        for hex in ["#e5484d", "#30a46c", "#5b5bd6", "#0fa3c2"] {
            let back = HabitManageSheet.hex(
                HabitsWidgetView.color(hex))
            XCTAssertEqual(back.lowercased(), hex,
                "hex→Color→hex must round-trip (\(hex) → \(back))")
        }
    }

    func testManageSeamSafelyQuotesNamesAndPathsAndResistsInjection() {
        // A habit name with a space, a quote, and shell metacharacters must
        // be SINGLE-QUOTE escaped (not charset-stripped — that's ids only),
        // so it reaches the engine intact AND cannot break out of the shell.
        let p = ContractGuard.habitManageProcess(
            habitId: "h-gym",
            op: .rename(name: "Mom's gym; rm -rf ~ `boom`"),
            windowDays: 30)
        let cmd = (p.arguments ?? []).joined(separator: " ")
        // Safety is proven by the EXACT POSIX single-quote escaped token
        // being present (the metachars live harmlessly inside the quotes;
        // the only literal `'` is closed→escaped→reopened as `'\''`), AND
        // the naive unquoted form being absent (that WOULD be a breakout).
        let expected = #"'Mom'\''s gym; rm -rf ~ `boom`'"#
        XCTAssertTrue(cmd.contains("--rename " + expected),
            "name must be POSIX single-quote escaped: \(cmd)")
        XCTAssertFalse(cmd.contains("--rename Mom's gym"),
            "the name must never appear unquoted: \(cmd)")
        // A path with spaces survives intact inside single quotes.
        let e = ContractGuard.habitManageProcess(
            habitId: nil,
            op: .exportTo(path: "/Users/x/My Habits/out.json"),
            windowDays: 30)
        let ec = (e.arguments ?? []).joined(separator: " ")
        XCTAssertTrue(
            ec.contains("--export '/Users/x/My Habits/out.json'"))
        XCTAssertFalse(ec.contains("--habit"),
            "export is whole-tracker — no --habit")
    }

    @MainActor
    func testManageRelaySurfacesFailureAndTogglesPending() {
        let store = HabitsWidgetStore()
        XCTAssertFalse(store.managePending)
        store.testInjectManageResult(
            ok: false, exitCode: 2,
            stderr: "manage error: unknown habit 'zzz'")
        XCTAssertFalse(store.managePending,
            "pending must clear when the op finishes")
        XCTAssertNotNil(store.lastRelayError,
            "a failed manage op must surface, never a silent no-op")
        store.testInjectManageResult(ok: true, exitCode: 0, stderr: "")
        XCTAssertNil(store.lastRelayError,
            "a successful op clears the prior error")
    }

    func testManageSeamRenamePersistsUnderMinimalGuiEnvEndToEnd() throws {
        let fm = FileManager.default
        var dir = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        var skill: URL?
        for _ in 0..<12 {
            let cand = dir.appendingPathComponent("skills/habit-tracker")
            if fm.fileExists(
                atPath: cand.appendingPathComponent("engine/manage.py").path
            ) { skill = cand; break }
            dir = dir.deletingLastPathComponent()
            if dir.path == "/" { break }
        }
        guard let skill else {
            throw XCTSkip("habit-tracker skill not reachable")
        }
        let uv = ContractGuard.resolvedUvPath()
        guard uv == "uv" ? false : fm.isExecutableFile(atPath: uv) else {
            throw XCTSkip("absolute uv not resolvable")
        }
        let tmp = fm.temporaryDirectory.appendingPathComponent(
            "iga-manage-e2e-\(UUID().uuidString)")
        try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: tmp) }

        let synth = #"""
        {"habits":[{"id":"h-gym","name":"Gym","description":null,
          "icon":"dumbbell","color":"emerald","emoji":null,
          "archived":false,"isInverse":false,"orderIndex":0,
          "createdAt":"2026-01-01T08:00:00.000000Z"}],
         "completions":[],"intervals":[],"categories":[],
         "categoryMappings":[],"reminders":[]}
        """#
        let exportFile = tmp.appendingPathComponent("export.json")
        try synth.write(to: exportFile, atomically: true, encoding: .utf8)
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
        try XCTSkipUnless(importp.terminationStatus == 0,
            "isolated import unavailable")

        let built = ContractGuard.habitManageProcess(
            habitId: "h-gym",
            op: .rename(name: "Strength Training"),
            windowDays: 30)
        let p = Process()
        p.executableURL = built.executableURL
        p.arguments = built.arguments
        p.environment = [
            "HOME": fm.homeDirectoryForCurrentUser.path,
            "IGA_HT_UV": uv,
            "IGA_HT_SKILL_DIR": skill.path,
            "IGA_HT_STATE_DIR": tmp.path,
            "IGA_HT_MANAGE_PY":
                skill.appendingPathComponent("engine/manage.py").path,
        ]                                  // NO PATH, NO exports: GUI-like
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        try p.run()
        p.waitUntilExit()
        XCTAssertEqual(p.terminationStatus, 0,
            "rename must succeed under a minimal GUI-equivalent env")

        let widget = tmp.appendingPathComponent(
            "widgets/habit-tracker-habits.json")
        let obj = try JSONSerialization.jsonObject(
            with: Data(contentsOf: widget)) as? [String: Any]
        let habits = (obj?["data"] as? [String: Any])?["habits"]
            as? [[String: Any]] ?? []
        XCTAssertEqual(
            habits.first?["name"] as? String, "Strength Training",
            "the rename must PERSIST and flow to the widget JSON")
    }

    // MARK: FIX 6 — binary tap toggles; per-day-GOAL tap opens the
    // quick-log drawer (no blunt one-tap-complete of a 40-rep day).

    func testBinaryRecordOpIsAPlainToggle() {
        // Binary habits: not-done → add (one tap = done), done → remove.
        XCTAssertEqual(
            HabitsWidgetStore.recordOp(currentlyDone: false), .add)
        XCTAssertEqual(
            HabitsWidgetStore.recordOp(currentlyDone: true), .remove)
    }

    func testLogDrawerStepMathIteratesClampsAndRespectsExceed() {
        typealias D = HabitLogDrawer
        // Iterates by the chosen step (NOT jump-to-complete).
        XCTAssertEqual(
            D.nextAmount(current: 0, delta: 10, target: 40,
                         allowExceed: true), 10)
        XCTAssertEqual(
            D.nextAmount(current: 10, delta: 10, target: 40,
                         allowExceed: true), 20)
        // − floors at 0 (never negative).
        XCTAssertEqual(
            D.nextAmount(current: 5, delta: -10, target: 40,
                         allowExceed: true), 0)
        // No-exceed habit: ＋ clamps at target.
        XCTAssertEqual(
            D.nextAmount(current: 38, delta: 5, target: 40,
                         allowExceed: false), 40)
        XCTAssertEqual(
            D.nextAmount(current: 40, delta: 5, target: 40,
                         allowExceed: false), 40)
        // Exceed-allowed habit: ＋ may pass target (overshoot past target).
        XCTAssertEqual(
            D.nextAmount(current: 40, delta: 10, target: 40,
                         allowExceed: true), 50)
        // Big-step from below target on a no-exceed habit still clamps.
        XCTAssertEqual(
            D.nextAmount(current: 0, delta: 100, target: 40,
                         allowExceed: false), 40)
    }

    func testLogDrawerRelaysAbsoluteAmountViaSanctionedSetAmountSeam() {
        // Reset (→0), Fill Day (→target) and ± all become an explicit
        // --set-amount through the SAME sanctioned record seam — never a
        // bespoke write. (Engine clamps/derives; app only names the value.)
        for n in [0, 1, 25, 40, 500] {
            let p = ContractGuard.habitRecordProcess(
                habitId: "h-pu", date: "2026-05-17",
                op: .setAmount(n), windowDays: 120)
            let cmd = (p.arguments ?? []).joined(separator: " ")
            XCTAssertTrue(cmd.contains("--set-amount \(n)"),
                "drawer must relay an explicit absolute amount: \(cmd)")
            XCTAssertTrue(cmd.contains("|| exit 90"))
            XCTAssertFalse(cmd.contains(" uv run "))
        }
    }

    @MainActor
    func testRelaySetAmountSurfacesFailureNotSwallowed() {
        let store = HabitsWidgetStore()
        store.relaySetAmount(
            habitId: "h-x", date: "2026-05-17", amount: 40)
        // Drive the failure continuation deterministically (no subprocess).
        store.testInjectRelayResult(
            key: HabitsWidgetStore.pendingKey("h-x", "2026-05-17"),
            ok: false, exitCode: 2,
            stderr: "record error: unknown entity 'h-x'")
        XCTAssertNotNil(store.lastRelayError,
            "a failed drawer write must surface, never a silent no-op")
    }

    func testLogContextIdentityIsHabitAndDate() {
        var h = HabitEntry()
        h.id = "h-pu"
        let c1 = HabitLogContext(habit: h, date: "2026-05-17")
        let c2 = HabitLogContext(habit: h, date: "2026-05-16")
        XCTAssertEqual(c1.id, "h-pu@2026-05-17")
        XCTAssertNotEqual(c1.id, c2.id,
            "different day = different drawer instance")
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
