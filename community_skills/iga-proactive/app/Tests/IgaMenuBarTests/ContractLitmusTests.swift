import XCTest
@testable import IgaMenuBar

// MARK: - The hard-contract litmus
//
// Frozen invariant (MemPalace iga/decisions/3542bae6):
//   The app renders + relays + triggers ONLY. Zero job logic in Swift. It
//   issues NO writes to the engine state JSON or the sqlite ledger, and the
//   ONLY engine side effect it may cause is exec'ing the documented scan
//   command. Deleting the app leaves `/gm` inline fully working.
//
// This suite enforces that two ways:
//   1. RUNTIME — assert ContractGuard's sanctioned command is exactly the
//      documented engine scan, and the ledger connection is read-only
//      (also covered in LedgerReaderTests).
//   2. SOURCE GREP — scan every Sources/*.swift file and fail if any
//      forbidden write/dispatch primitive appears outside the sanctioned
//      ContractGuard entry point.

final class ContractLitmusTests: XCTestCase {

    /// Resolve the Sources dir relative to this test file at compile time.
    private func sourcesDir() -> URL {
        // .../app/Tests/IgaMenuBarTests/ContractLitmusTests.swift
        let thisFile = URL(fileURLWithPath: #filePath)
        let appDir = thisFile
            .deletingLastPathComponent()   // IgaMenuBarTests
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // app
        return appDir
            .appendingPathComponent("Sources")
            .appendingPathComponent("IgaMenuBar")
    }

    private func swiftSources() throws -> [(name: String, body: String)] {
        let dir = sourcesDir()
        let urls = try FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "swift" }
        XCTAssertFalse(urls.isEmpty, "no Swift sources found at \(dir.path)")
        return try urls.map {
            ($0.lastPathComponent, try String(contentsOf: $0, encoding: .utf8))
        }
    }

    // MARK: 1. runtime — the only sanctioned engine command

    func testSanctionedCommandIsExactlyTheDocumentedScan() {
        XCTAssertEqual(
            ContractGuard.documentedCommand,
            "cd ~/Iga/skills/iga-proactive && "
            + "PYTHONPATH=engine uv run python -m engine scan --json")

        let joined = ContractGuard.engineScanArgv.joined(separator: " ")
        XCTAssertTrue(joined.contains("PYTHONPATH=engine"))
        XCTAssertTrue(joined.contains("uv run python -m engine scan --json"))
        // It must NOT carry any write/mutate verb.
        for forbidden in ["mark", "claim", "record", "INSERT",
                          "UPDATE", "DELETE", "--write"] {
            XCTAssertFalse(
                joined.contains(forbidden),
                "sanctioned command must not contain \(forbidden)")
        }
    }

    func testEngineProcessOnlyRunsScan() {
        let p = ContractGuard.engineScanProcess()
        XCTAssertEqual(p.executableURL?.path, "/bin/zsh")
        let args = (p.arguments ?? []).joined(separator: " ")
        XCTAssertTrue(args.contains("engine scan --json"))
        XCTAssertFalse(args.contains("python -c"))   // no inline engine code
    }

    // MARK: 2. source grep — no write/dispatch primitive escapes the entry point

    func testNoForbiddenWriteOrDispatchPrimitivesInSource() throws {
        // Primitives that would let Swift mutate engine state or invent job
        // logic. Each must be ABSENT from every source file (the only Process
        // is constructed in ContractGuard, which is itself whitelisted).
        // Primitives banned EVERYWHERE (no legitimate use anywhere).
        let bannedEverywhere: [String] = [
            "sqlite3_open(",            // RW sqlite open (we only use _v2 RO)
            "SQLITE_OPEN_READWRITE",
            "SQLITE_OPEN_CREATE",
            ".write(to:",               // Data/String write to disk
            "write(toFile:",
            "FileHandle(forWritingAtPath:",
            "FileHandle(forWritingTo:",
            "createFile(atPath:",
            "JSONEncoder(",             // app never re-encodes state
            "NSTask(",                  // legacy subprocess constructor
        ]
        // Subprocess construction is sanctioned ONLY inside ContractGuard.
        let subprocessOnlyInGuard = ["Process()"]

        for (name, body) in try swiftSources() {
            // Strip line + block comments so prose mentions of a primitive
            // (e.g. documentation of the contract) don't trip the grep.
            let code = stripComments(body)
            for token in bannedEverywhere {
                XCTAssertFalse(
                    code.contains(token),
                    "\(name) contains forbidden primitive '\(token)' — "
                    + "violates the render/relay/trigger-only contract")
            }
            for token in subprocessOnlyInGuard
            where name != "ContractGuard.swift" {
                XCTAssertFalse(
                    code.contains(token),
                    "\(name) constructs '\(token)' outside ContractGuard — "
                    + "all engine execution must go through the entry point")
            }
        }
    }

    /// Remove `//` line comments and `/* */` block comments so the grep
    /// asserts on CODE, not on documentation that legitimately names a
    /// forbidden primitive while explaining the contract.
    private func stripComments(_ src: String) -> String {
        var out = ""
        out.reserveCapacity(src.count)
        var i = src.startIndex
        var inBlock = false
        while i < src.endIndex {
            let c = src[i]
            let next = src.index(after: i)
            if inBlock {
                if c == "*", next < src.endIndex, src[next] == "/" {
                    inBlock = false
                    i = src.index(after: next)
                    continue
                }
                i = next
                continue
            }
            if c == "/", next < src.endIndex, src[next] == "/" {
                while i < src.endIndex, src[i] != "\n" {
                    i = src.index(after: i)
                }
                continue
            }
            if c == "/", next < src.endIndex, src[next] == "*" {
                inBlock = true
                i = src.index(after: next)
                continue
            }
            out.append(c)
            i = next
        }
        return out
    }

    func testOnlyContractGuardConstructsAProcess() throws {
        for (name, body) in try swiftSources()
        where name != "ContractGuard.swift" {
            XCTAssertFalse(
                stripComments(body).contains("Process()"),
                "\(name) constructs a Process directly — all engine "
                + "execution must go through ContractGuard")
        }
    }

    func testNoStateFileWritePathAnywhere() throws {
        // The app must never write proactive-state.json. Assert no source
        // both references the state filename AND a write primitive.
        for (name, body) in try swiftSources() {
            let code = stripComments(body)
            if code.contains("proactive-state.json") {
                XCTAssertFalse(
                    code.contains(".write(to:")
                        || code.contains("write(toFile:")
                        || code.contains("_atomic_write"),
                    "\(name) appears to write the engine state file")
            }
        }
    }

    // MARK: 3. widget host — render + relay ONLY (v2 contract extension)
    //
    // A widget = a declarative spec + a data file. The skill produces the
    // data file; the app renders ONLY known widget types from it, holding
    // ZERO widget logic beyond render primitives. The litmus must therefore
    // also prove: the widget host issues no writes, execs nothing, and never
    // produces widget data or coach text itself.

    /// The widget-host sources, by name, that must contain ZERO write/exec
    /// and zero data-production logic.
    private let widgetHostFiles = [
        "WidgetData.swift",
        "SkillDiscovery.swift",
        "WidgetHostStore.swift",
        "WidgetHostView.swift",
        "SkillsSectionView.swift",
        // Wave B multi-habit widget — same render/relay-only contract.
        "HabitsWidgetStore.swift",
        "HabitsWidgetView.swift",
        // Wave C v2 — the unified two-column panel + status-item trigger.
        // Same render/relay-only contract: the panel manager and the
        // status-item controller are pure UI plumbing; the columns host the
        // existing fundamentals/board widgets + relay only.
        "PanelController.swift",
        "BoardPanelView.swift",
        "StatusItemController.swift",
        "FundamentalsView.swift",
    ]

    func testWidgetHostSourcesExistAndAreReadOnly() throws {
        let sources = try swiftSources()
        let names = Set(sources.map(\.name))
        for f in widgetHostFiles {
            XCTAssertTrue(
                names.contains(f),
                "widget host file \(f) missing — the v2 host must exist")
        }
        // The widget host must never WRITE a widget data file, never re-encode
        // it, never exec, never open a RW sqlite handle. (The blanket grep in
        // testNoForbiddenWriteOrDispatchPrimitivesInSource already covers all
        // sources; this is the explicit, named widget-contract assertion.)
        let forbidden = [
            "Process()", ".write(to:", "write(toFile:",
            "createFile(atPath:", "JSONEncoder(",
            "FileHandle(forWritingAtPath:", "FileHandle(forWritingTo:",
            "sqlite3_open(", "SQLITE_OPEN_READWRITE", "SQLITE_OPEN_CREATE",
        ]
        for (name, body) in sources where widgetHostFiles.contains(name) {
            let code = stripComments(body)
            for token in forbidden {
                XCTAssertFalse(
                    code.contains(token),
                    "\(name) contains '\(token)' — the widget host must "
                    + "render/relay only, never write/exec/produce data")
            }
        }
    }

    func testWidgetHostNeverWritesADataFile() throws {
        // No source may reference the widget data dir AND a write primitive.
        for (name, body) in try swiftSources() {
            let code = stripComments(body)
            if code.contains("state/widgets") || code.contains("widgets/") {
                XCTAssertFalse(
                    code.contains(".write(to:")
                        || code.contains("write(toFile:")
                        || code.contains("createFile(atPath:")
                        || code.contains("_atomic_write"),
                    "\(name) appears to WRITE a widget data file — only "
                    + "skills produce widget data; the app renders it")
            }
        }
    }

    func testWidgetDecodeIsTolerantNotCrashy() throws {
        // Garbage / partial / unknown-type files must degrade, never throw
        // past the decoder boundary in a way that could crash the poller.
        // (a) non-JSON → throws (caller treats as "waiting") — must not trap.
        XCTAssertThrowsError(
            try WidgetData.decode(from: Data("not json".utf8)))
        // (b) empty object → defaults, no crash.
        let empty = try WidgetData.decode(from: Data("{}".utf8))
        XCTAssertEqual(empty.schemaVersion, 0)
        XCTAssertEqual(empty.kind, .unknown(""))
        // (c) unknown type → .unknown, still decodes.
        let unknown = try WidgetData.decode(
            from: Data(#"{"type":"hologram","title":"x"}"#.utf8))
        XCTAssertEqual(unknown.kind, .unknown("hologram"))
        XCTAssertEqual(unknown.title, "x")
        // (d) a valid contribution-grid decodes its cells + coach.
        let json = #"""
        {"schema_version":1,"widget_id":"habit-grid",
         "type":"contribution-grid","title":"Habit streak",
         "generated_at":"2026-05-16T11:30:26.694964+00:00",
         "data":{"label":"example — 6-day streak","levels":4,
                 "cells":[{"date":"2026-05-16","level":4},
                          {"date":"2026-05-15","level":3}]},
         "coach":{"text":"6-day streak going.","tone":"encouraging"}}
        """#
        let w = try WidgetData.decode(from: Data(json.utf8))
        XCTAssertEqual(w.kind, .contributionGrid)
        XCTAssertEqual(w.grid?.cells.count, 2)
        XCTAssertEqual(w.grid?.levels, 4)
        XCTAssertEqual(w.coach?.tone, "encouraging")
    }

    func testGridLayoutIsPureNoLevelComputation() {
        // The Swift side must NOT compute a level — it only buckets the
        // provided cells into week columns and maps an existing level to a
        // color. Assert columns() preserves the input cells verbatim.
        let cells = (0..<10).map {
            GridCell(date: "2026-05-\(String(format: "%02d", $0 + 1))",
                     level: $0 % 5)
        }
        let cols = WidgetHostView.columns(from: cells)
        XCTAssertEqual(cols.flatMap { $0 }, cells,
            "columns() must not alter cells — pure layout, no logic")
        XCTAssertEqual(cols.first?.count, 7)
        // color() is a frozen presentation mapping: level 0 is the empty
        // tile, padding (-1) is clear, higher level → more opaque.
        XCTAssertEqual(WidgetHostView.color(for: -1, levels: 4), .clear)
        XCTAssertNotEqual(
            WidgetHostView.color(for: 0, levels: 4),
            WidgetHostView.color(for: 4, levels: 4))
    }

    func testDiscoveryIsAReadOnlyFrontmatterScan() {
        // Discovery must parse SKILL.md frontmatter only — never write or
        // exec. Functionally: a synthetic widgets: block parses correctly.
        let fm = """
        name: demo
        description: A demo skill. Second sentence ignored.
        proactive:
          - id: j1
        widgets:
          - id: habit-grid
            type: contribution-grid
            title: Habit streak
            data_source: ~/Iga/state/widgets/habit-tracker-habit-grid.json
            refresh: 60
            coach:
              tone: encouraging
              text_field: coach
        """
        XCTAssertTrue(SkillDiscovery.hasProactiveBlock(fm))
        // description() returns the raw frontmatter value verbatim;
        // plainSummary() is what trims it to a short first clause.
        XCTAssertEqual(
            SkillDiscovery.description(fm),
            "A demo skill. Second sentence ignored.")
        XCTAssertEqual(
            SkillDiscovery.plainSummary(SkillDiscovery.description(fm)),
            "A demo skill")
        let ws = SkillDiscovery.widgets(
            in: fm, skill: "habit-tracker",
            defaultDataDir: "/tmp/widgets")
        XCTAssertEqual(ws.count, 1)
        XCTAssertEqual(ws.first?.id, "habit-grid")
        XCTAssertEqual(ws.first?.type, "contribution-grid")
        XCTAssertEqual(ws.first?.refresh, 60)
        XCTAssertTrue(
            ws.first?.dataSource.hasSuffix(
                "habit-tracker-habit-grid.json") ?? false)
        // Nested `coach:` mapping must not leak into the next field or
        // create a bogus widget.
        XCTAssertEqual(ws.first?.title, "Habit streak")
    }

    func testDeletionInvariantCoversTheProducerStandalone() throws {
        // The hard contract extended to widgets: deleting the app leaves the
        // skill's producer working standalone (it is plain stdlib Python with
        // no app dependency). Assert the producer exists and imports nothing
        // from the app, and that SKILL.md documents the deletion invariant.
        // Walk up from this test file to the repo root (the dir that
        // contains `skills/habit-tracker/engine/producer.py`). This resolves
        // correctly whether the suite runs from the live tree
        // (skills/iga-proactive/app) OR the OSS mirror
        // (community_skills/iga-proactive/app) — both sit in the same repo,
        // so the producer is reachable by upward search from either.
        let fm = FileManager.default
        var dir = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        var producer: URL?
        for _ in 0..<12 {
            let cand = dir.appendingPathComponent(
                "skills/habit-tracker/engine/producer.py")
            if fm.fileExists(atPath: cand.path) {
                producer = cand
                break
            }
            dir = dir.deletingLastPathComponent()
            if dir.path == "/" { break }
        }
        guard let producer else {
            return XCTFail(
                "skills/habit-tracker/engine/producer.py not reachable — "
                + "the producer must exist independent of the app so "
                + "deleting the app leaves the widget pipeline working")
        }
        let skillMD = producer
            .deletingLastPathComponent()   // engine
            .deletingLastPathComponent()   // habit-tracker
            .appendingPathComponent("SKILL.md")
        let pcode = try String(contentsOf: producer, encoding: .utf8)
        XCTAssertFalse(
            pcode.contains("IgaMenuBar") || pcode.contains("import Swift"),
            "the producer must not depend on the app in any way")
        XCTAssertTrue(
            pcode.contains("os.replace"),
            "producer must write its data file atomically (tmp+replace)")
        let md = try String(contentsOf: skillMD, encoding: .utf8)
        XCTAssertTrue(
            md.lowercased().contains("delet")
            && md.lowercased().contains("app"),
            "habit-tracker SKILL.md must document the deletion invariant")

        // OPERATIONAL proof of the invariant, fully ISOLATED.
        //
        // Reading the source is necessary but not sufficient — actually
        // RUN the producer with the app absent from the equation and
        // assert it emits a valid widget JSON. Critically, this MUST NOT
        // touch the user's live ~/Iga/state: point IGA_STATE_DIR at a
        // throwaway temp dir so all producer reads/writes are rooted
        // there. (Process() here is fine — the contract grep only scans
        // Sources/IgaMenuBar, never the test target.)
        let tmpRoot = fm.temporaryDirectory.appendingPathComponent(
            "iga-habit-deletion-invariant-\(UUID().uuidString)")
        try fm.createDirectory(
            at: tmpRoot.appendingPathComponent("habits"),
            withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: tmpRoot) }

        let realState = fm.homeDirectoryForCurrentUser
            .appendingPathComponent("Iga/state/widgets")
            .appendingPathComponent("habit-tracker-habit-grid.json")
        let realExisted = fm.fileExists(atPath: realState.path)
        let realAttrsBefore = realExisted
            ? try? fm.attributesOfItem(atPath: realState.path)
            : nil
        let realMTimeBefore =
            realAttrsBefore?[.modificationDate] as? Date

        // Seed an isolated habit log (the app plays NO part in this).
        let log = tmpRoot.appendingPathComponent("habits/example.log")
        try "2026-05-16\n2026-05-15\n2026-05-14\n"
            .write(to: log, atomically: true, encoding: .utf8)

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/zsh")
        proc.arguments = [
            "-c",
            "uv run python "
            + producer.path.replacingOccurrences(of: " ", with: "\\ ")
            + " --name example --days 30",
        ]
        var env = ProcessInfo.processInfo.environment
        env["IGA_STATE_DIR"] = tmpRoot.path     // <-- isolation
        proc.environment = env
        proc.standardOutput = Pipe()
        proc.standardError = Pipe()
        try proc.run()
        proc.waitUntilExit()
        XCTAssertEqual(
            proc.terminationStatus, 0,
            "producer must run standalone (app deleted) and succeed")

        // The producer emitted a valid widget JSON into the ISOLATED root.
        let isolatedOut = tmpRoot
            .appendingPathComponent("widgets")
            .appendingPathComponent("habit-tracker-habit-grid.json")
        XCTAssertTrue(
            fm.fileExists(atPath: isolatedOut.path),
            "producer (app absent) must emit the widget data file")
        let emitted = try Data(contentsOf: isolatedOut)
        let obj = try JSONSerialization.jsonObject(with: emitted)
            as? [String: Any]
        XCTAssertEqual(obj?["type"] as? String, "contribution-grid",
            "standalone producer must emit a valid v1 widget JSON")

        // And it NEVER touched the user's live ~/Iga/state.
        if realExisted {
            XCTAssertTrue(
                fm.fileExists(atPath: realState.path),
                "isolated producer run must not delete real state")
            let after = try? fm.attributesOfItem(atPath: realState.path)
            XCTAssertEqual(
                after?[.modificationDate] as? Date, realMTimeBefore,
                "REAL ~/Iga/state widget JSON changed — the Swift "
                + "deletion-invariant test wrote to live data")
        } else {
            XCTAssertFalse(
                fm.fileExists(atPath: realState.path),
                "isolated producer run created a file under real state")
        }
    }

    // MARK: 4. Wave B — the record (MUTATION) entry point contract
    //
    // Clicking a habit square is a MUTATION. The hard contract extended:
    //   • the app issues NO write itself — it relays to exactly ONE engine
    //     entry point (the habit-tracker `record` CLI), analogous to the read-only
    //     scan entry point; the engine mutates the substrate + re-emits the JSON;
    //   • the entry point carries a MANDATORY explicit --state-dir (no implicit
    //     real-state default — the privacy/data-loss guard);
    //   • the widget computes NO streak/goal/grid math (zero habit logic);
    //   • deleting the app leaves the record CLI fully working standalone.

    func testRecordEntryPointCommandShapeIsExactAndStateDirMandatory() {
        XCTAssertEqual(
            ContractGuard.documentedRecordCommand,
            "cd <abs-skill-dir> || exit 90 ; <abs-uv> run python "
            + "<abs-record.py> --state-dir <abs-live-state> --habit <id> "
            + "--date <YYYY-MM-DD> (--add | --remove | --set-amount N) "
            + "--days N")
        let p = ContractGuard.habitRecordProcess(
            habitId: "h-gym", date: "2026-05-16",
            op: .add, windowDays: 30)
        XCTAssertEqual(p.executableURL?.path, "/bin/zsh")
        let args = (p.arguments ?? []).joined(separator: " ")
        // record.py is now passed as an ABSOLUTE path via $IGA_HT_RECORD_PY
        // (a Finder/Spotlight .app has no reliable cwd-relative resolution).
        XCTAssertTrue(args.contains("\"$IGA_HT_RECORD_PY\""))
        XCTAssertTrue(
            (p.environment?["IGA_HT_RECORD_PY"] ?? "")
                .hasSuffix("/skills/habit-tracker/engine/record.py"))
        XCTAssertTrue(
            (p.environment?["IGA_HT_RECORD_PY"] ?? "").hasPrefix("/"))
        XCTAssertTrue(args.contains("--state-dir"))
        XCTAssertTrue(args.contains("--habit 'h-gym'"))
        XCTAssertTrue(args.contains("--date '2026-05-16'"))
        XCTAssertTrue(args.contains("--add"))
        // The entry point never embeds inline python or a write verb on JSON.
        XCTAssertFalse(args.contains("python -c"))
        XCTAssertFalse(args.contains(".json"))
        // remove + set-amount variants render the right flag.
        let pr = ContractGuard.habitRecordProcess(
            habitId: "h-x", date: "2026-01-01",
            op: .remove, windowDays: 7)
        XCTAssertTrue((pr.arguments ?? []).joined(separator: " ")
            .contains("--remove"))
        let ps = ContractGuard.habitRecordProcess(
            habitId: "h-x", date: "2026-01-01",
            op: .setAmount(4), windowDays: 7)
        XCTAssertTrue((ps.arguments ?? []).joined(separator: " ")
            .contains("--set-amount 4"))
    }

    func testRecordEntryPointSanitizesDynamicValues() {
        // A crafted id/date cannot break out of the single-quoted arg: the
        // shell-safe filter strips everything but [A-Za-z0-9-_:].
        let p = ContractGuard.habitRecordProcess(
            habitId: "h-gym'; rm -rf ~ #",
            date: "2026-05-16$(touch /tmp/x)",
            op: .add, windowDays: 30)
        let args = (p.arguments ?? []).joined(separator: " ")
        // Every shell metacharacter is stripped before it reaches the
        // command — no injection survives the safe-charset filter.
        XCTAssertFalse(args.contains("rm -rf"))
        XCTAssertFalse(args.contains("$("))
        XCTAssertFalse(args.contains(";"))
        XCTAssertFalse(args.contains("#"))
        XCTAssertFalse(args.contains("'; "))
        // The surviving id is purely [A-Za-z0-9-_:] inside single quotes;
        // engine-side validation rejects an unknown id regardless.
        XCTAssertTrue(args.contains("--habit 'h-gymrm-rf'"),
            "only the safe-charset residue survives: \(args)")
        // A clean id/date passes through intact.
        let ok = ContractGuard.habitRecordProcess(
            habitId: "h-gym", date: "2026-05-16",
            op: .add, windowDays: 30)
        let okArgs = (ok.arguments ?? []).joined(separator: " ")
        XCTAssertTrue(okArgs.contains("--habit 'h-gym'"))
        XCTAssertTrue(okArgs.contains("--date '2026-05-16'"))
    }

    func testOnlyContractGuardConstructsTheMutationSubprocess() throws {
        // The blanket grep already bans Process() outside ContractGuard.
        // This is the explicit, named mutation-entry point assertion: no source but
        // ContractGuard references runRecord's subprocess, and the widget
        // host relays ONLY via ContractGuard.runRecord — never a Process,
        // never a JSON write, never engine math.
        for (name, body) in try swiftSources()
        where name != "ContractGuard.swift" {
            let code = stripComments(body)
            XCTAssertFalse(
                code.contains("Process()"),
                "\(name) constructs a Process — the record mutation must "
                + "go through ContractGuard.runRecord only")
        }
        // The relay store must call the sanctioned entry point and nothing else.
        let store = try swiftSources()
            .first { $0.name == "HabitsWidgetStore.swift" }
        XCTAssertNotNil(store)
        let code = stripComments(store!.body)
        XCTAssertTrue(
            code.contains("ContractGuard.runRecord"),
            "the habits store must relay clicks via the sanctioned entry point")
        for forbidden in [
            "Process()", ".write(to:", "write(toFile:", "JSONEncoder(",
            "createFile(atPath:", "current_streak =", "longest_streak =",
        ] {
            XCTAssertFalse(
                code.contains(forbidden),
                "HabitsWidgetStore contains '\(forbidden)' — it must "
                + "render/relay only, never write or compute habit logic")
        }
    }

    func testHabitsViewComputesNoHabitLogicPurePresentation() throws {
        // The view may map an ALREADY-DECIDED level→color and group cells
        // into week columns; it must not compute a streak/goal/level.
        let v = try swiftSources()
            .first { $0.name == "HabitsWidgetView.swift" }
        XCTAssertNotNil(v)
        let code = stripComments(v!.body)
        // No re-derivation of engine numbers (assignment to the decoded
        // fields would be logic; reading them is fine).
        for forbidden in [
            "currentStreak =", "longestStreak =", ".level =",
            "Process()", ".write(to:", "JSONEncoder(",
        ] {
            XCTAssertFalse(
                code.contains(forbidden),
                "HabitsWidgetView contains '\(forbidden)' — the widget "
                + "must render engine-computed values, not recompute them")
        }
        // weekColumns is pure grouping: it must preserve the input cells
        // (modulo explicit -1 alignment padding) verbatim.
        let cells = (1...20).map {
            GridCell(date: String(format: "2026-03-%02d", $0),
                     level: $0 % 5)
        }
        let cols = HabitsWidgetView.weekColumns(cells)
        let flatReal = cols.flatMap { $0 }.filter { $0.level >= 0 }
        XCTAssertEqual(flatReal, cells,
            "weekColumns must not alter real cells — pure layout")
        for c in cols { XCTAssertLessThanOrEqual(c.count, 7) }
        // color()/fill() are deterministic presentation maps (composed via
        // the test-safe levelColor so no Color literal is needed here).
        // Same hex → same color; a malformed hex converges to ONE safe
        // default (proves no per-call invention).
        XCTAssertEqual(
            HabitsWidgetView.levelColor(level: 2, hex: "#1FAD71", levels: 4),
            HabitsWidgetView.levelColor(level: 2, hex: "#1FAD71", levels: 4))
        XCTAssertEqual(
            HabitsWidgetView.levelColor(
                level: 2, hex: "not-a-hex", levels: 4),
            HabitsWidgetView.levelColor(
                level: 2, hex: "also-bad", levels: 4),
            "a malformed hex must converge to one deterministic default")
        XCTAssertNotEqual(
            HabitsWidgetView.levelColor(level: 2, hex: "#1FAD71", levels: 4),
            HabitsWidgetView.levelColor(level: 2, hex: "#E5484D", levels: 4),
            "distinct engine hexes must render distinct colors")
        // padding is clear regardless of the habit's hex; level 0 is the
        // shared neutral tile (not the habit color); higher level → a
        // different (more opaque) shade.
        XCTAssertEqual(
            HabitsWidgetView.levelColor(
                level: -1, hex: "#1FAD71", levels: 4),
            HabitsWidgetView.levelColor(
                level: -1, hex: "#E5484D", levels: 4),
            "padding cell must be hex-independent (clear)")
        XCTAssertEqual(
            HabitsWidgetView.levelColor(level: 0, hex: "#1FAD71", levels: 4),
            HabitsWidgetView.levelColor(level: 0, hex: "#E5484D", levels: 4),
            "the empty (level 0) tile is the shared neutral, not the "
            + "habit color")
        XCTAssertNotEqual(
            HabitsWidgetView.levelColor(level: 1, hex: "#1FAD71", levels: 4),
            HabitsWidgetView.levelColor(level: 4, hex: "#1FAD71", levels: 4))
    }

    func testHabitsWidgetDecodeIsTolerantNotCrashy() throws {
        // Garbage / partial / unknown decode to defaults, never a crash.
        XCTAssertThrowsError(
            try HabitsWidgetData.decode(from: Data("nope".utf8)))
        let empty = try HabitsWidgetData.decode(from: Data("{}".utf8))
        XCTAssertEqual(empty.schemaVersion, 0)
        XCTAssertTrue(empty.habits.isEmpty)
        // A valid schema_version 2 multi-habit payload decodes fully,
        // carrying the engine-computed color/streak/goal/cells verbatim.
        let json = #"""
        {"schema_version":2,"widget_id":"habits",
         "type":"habit-grid-multi","title":"Habits",
         "generated_at":"2026-05-16T12:00:00.000000+00:00",
         "today":"2026-05-16","window_days":30,
         "data":{"levels":4,"habits":[
           {"id":"h-gym","name":"Gym","color":"#1FAD71",
            "color_name":"emerald","icon":"dumbbell","emoji":null,
            "is_inverse":false,"archived":false,"order_index":3,
            "current_streak":5,"longest_streak":9,
            "goal":{"period":"week","period_start":"2026-05-11",
                    "target":3,"count":2,"display_count":2,
                    "done":false,"allow_exceed":false},
            "levels":4,
            "cells":[{"date":"2026-05-15","level":3},
                     {"date":"2026-05-16","level":4}]},
           {"id":"h-ns","name":"NoSnack","color":"#E5484D",
            "color_name":"red","icon":"leaf","emoji":null,
            "is_inverse":true,"archived":false,"order_index":1,
            "current_streak":0,"longest_streak":4,
            "goal":{"period":"day","period_start":null,"target":null,
                    "count":0,"display_count":0,"done":true,
                    "allow_exceed":false},
            "levels":4,"cells":[]}]}}
        """#
        let w = try HabitsWidgetData.decode(from: Data(json.utf8))
        XCTAssertEqual(w.schemaVersion, 2)
        XCTAssertEqual(w.type, "habit-grid-multi")
        XCTAssertEqual(w.windowDays, 30)
        XCTAssertEqual(w.habits.count, 2)
        let gym = w.habits[0]
        XCTAssertEqual(gym.id, "h-gym")
        XCTAssertEqual(gym.colorHex, "#1FAD71")
        XCTAssertEqual(gym.currentStreak, 5)
        XCTAssertEqual(gym.longestStreak, 9)
        XCTAssertEqual(gym.goal.period, "week")
        XCTAssertEqual(gym.goal.target, 3)
        XCTAssertFalse(gym.goal.done)
        XCTAssertTrue(gym.goal.hasGoal)
        XCTAssertEqual(gym.cells.count, 2)
        let ns = w.habits[1]
        XCTAssertTrue(ns.isInverse)
        XCTAssertNil(ns.goal.target)
        XCTAssertFalse(ns.goal.hasGoal)   // no goal → no ring
    }

    func testRecordEntryPointDeletionInvariantStandaloneAndIsolated() throws {
        // OPERATIONAL proof: the record CLI runs WITHOUT the app, mutates an
        // isolated substrate, re-emits the Wave-B widget JSON, and the grid +
        // streak it produces match the FROZEN stats.py — proving the Swift
        // side needs zero habit logic. Fully isolated: IGA_STATE_DIR points
        // at a throwaway dir so the user's live ~/Iga/state is never
        // touched (mtime-asserted). (Process() here is fine — the contract
        // grep only scans Sources/IgaMenuBar, never the test target.)
        let fm = FileManager.default
        var dir = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
        var record: URL?
        for _ in 0..<12 {
            let cand = dir.appendingPathComponent(
                "skills/habit-tracker/engine/record.py")
            if fm.fileExists(atPath: cand.path) { record = cand; break }
            dir = dir.deletingLastPathComponent()
            if dir.path == "/" { break }
        }
        guard let record else {
            return XCTFail(
                "skills/habit-tracker/engine/record.py not reachable — the "
                + "record entry point must exist independent of the app")
        }
        let engineDir = record.deletingLastPathComponent()
        let rcode = try String(contentsOf: record, encoding: .utf8)
        XCTAssertFalse(
            rcode.contains("IgaMenuBar") || rcode.contains("import Swift"),
            "the record entry point must not depend on the app in any way")
        XCTAssertTrue(
            rcode.contains("SubstrateStore"),
            "record must mutate via the frozen Wave-A substrate store")

        let realRoot = fm.homeDirectoryForCurrentUser
            .appendingPathComponent("Iga/state")
        let watched = [
            realRoot.appendingPathComponent(
                "substrates/habit-tracker.json"),
            realRoot.appendingPathComponent(
                "widgets/habit-tracker-habit-grid.json"),
            realRoot.appendingPathComponent(
                "widgets/habit-tracker-habits.json"),
        ]
        func snap(_ p: URL) -> Date? {
            (try? fm.attributesOfItem(atPath: p.path))?[
                .modificationDate] as? Date
        }
        let before = watched.map { ($0, fm.fileExists(atPath: $0.path),
                                    snap($0)) }

        let tmpRoot = fm.temporaryDirectory.appendingPathComponent(
            "iga-record-ep-\(UUID().uuidString)")
        try fm.createDirectory(at: tmpRoot,
                               withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: tmpRoot) }

        // Seed an isolated substrate via the frozen importer, then drive the
        // record CLI exactly as the app's relay would. Plain Python, app
        // absent from the equation entirely.
        let synth = #"""
        {"habits":[{"id":"h-gym","name":"Gym","description":null,
          "icon":"dumbbell","color":"emerald","emoji":null,
          "archived":false,"isInverse":false,"orderIndex":0,
          "createdAt":"2026-01-01T08:00:00.000000Z"}],
         "completions":[],"intervals":[],"categories":[],
         "categoryMappings":[],"reminders":[]}
        """#
        let exportFile = tmpRoot.appendingPathComponent("export.json")
        try synth.write(to: exportFile, atomically: true, encoding: .utf8)

        func run(_ script: String) -> Int32 {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/zsh")
            p.arguments = ["-c", script]
            var env = ProcessInfo.processInfo.environment
            env["IGA_STATE_DIR"] = tmpRoot.path     // <- isolation
            p.environment = env
            p.standardOutput = Pipe()
            p.standardError = Pipe()
            try? p.run()
            p.waitUntilExit()
            return p.terminationStatus
        }
        let eng = engineDir.path.replacingOccurrences(
            of: " ", with: "\\ ")
        XCTAssertEqual(
            run("uv run python \(eng)/import_habitkit.py --input "
                + "\(exportFile.path.replacingOccurrences(of: " ", with: "\\ ")) "
                + "--state-dir \(tmpRoot.path)"),
            0, "isolated import must succeed")
        // Click three consecutive days via the record entry point CLI.
        for d in ["2026-05-14", "2026-05-15", "2026-05-16"] {
            XCTAssertEqual(
                run("uv run python \(eng)/record.py --state-dir "
                    + "\(tmpRoot.path) --habit h-gym --date \(d) --add "
                    + "--days 30"),
                0, "record entry point (app absent) must succeed for \(d)")
        }

        // The entry point re-emitted the Wave-B widget JSON into the ISOLATED root.
        let hb = tmpRoot.appendingPathComponent(
            "widgets/habit-tracker-habits.json")
        XCTAssertTrue(fm.fileExists(atPath: hb.path),
            "record entry point must re-emit the multi-habit widget JSON")
        let obj = try JSONSerialization.jsonObject(
            with: Data(contentsOf: hb)) as? [String: Any]
        XCTAssertEqual(obj?["schema_version"] as? Int, 2)
        let habits = (obj?["data"] as? [String: Any])?[
            "habits"] as? [[String: Any]]
        let gym = habits?.first { $0["id"] as? String == "h-gym" }
        // 3 consecutive added days ending TODAY-ish → streak == 3, computed
        // by stats.py and surfaced verbatim. The Swift side computed nothing.
        XCTAssertEqual(gym?["current_streak"] as? Int, 3,
            "engine-computed streak after 3 clicks must be 3 (stats.py "
            + "is the oracle; the app/entry point added no logic)")
        let gcells = gym?["cells"] as? [[String: Any]] ?? []
        let lit = Set(gcells.compactMap { c -> String? in
            (c["level"] as? Int ?? 0) > 0 ? c["date"] as? String : nil
        })
        for d in ["2026-05-14", "2026-05-15", "2026-05-16"] {
            XCTAssertTrue(lit.contains(d),
                "clicked day \(d) must be lit in the re-emitted grid")
        }

        // And the user's REAL ~/Iga/state was never touched.
        for (p, existed, mtime) in before {
            if existed {
                XCTAssertTrue(fm.fileExists(atPath: p.path),
                    "isolated record run deleted real state \(p.path)")
                XCTAssertEqual(snap(p), mtime,
                    "REAL ~/Iga/state changed (\(p.lastPathComponent)) — "
                    + "the record entry point wrote to live data")
            } else {
                XCTAssertFalse(fm.fileExists(atPath: p.path),
                    "isolated record run created \(p.path) under real state")
            }
        }
    }

    // MARK: 5. Wave C v2 — the UNIFIED two-column panel (the user's correction)
    //
    // the user's corrected, NON-NEGOTIABLE design: ONE click on the menu-bar
    // icon opens BOTH panels at once — the FUNDAMENTALS column on the LEFT
    // and the widget BOARD column on the RIGHT, edge-to-edge, tops aligned.
    // There is NO "Open board" button and NO board toggle; `boardControl` /
    // the relay are GONE. The status item is a pure trigger; the whole UI is
    // ONE position-controlled NSPanel anchored to the status-item button.
    // The hard contract is unchanged, so the litmus must prove the new
    // surfaces are STILL render/relay-only AND assert the NEW shape:
    //   • the panel manager + status item + both columns issue NO write,
    //     exec nothing, encode no JSON, open no sqlite — pure UI plumbing;
    //   • the deletion invariant still holds (no new engine dependency);
    //   • the dense grid height is HARD-CAPPED and period-INVARIANT (defect
    //     #1 — machine-checked, not a prose claim);
    //   • the board's children own no nested vertical ScrollView (defect #2);
    //   • ONE click opens BOTH; no boardControl / "Open board"; board RIGHT;
    //   • the geometry invariant: board.origin.x ≥ fundamentals.maxX and
    //     tops aligned, for the normal AND right-screen-edge cases.

    func testWaveCPanelSourcesExistAndAreRenderRelayOnly() throws {
        let sources = try swiftSources()
        let names = Set(sources.map(\.name))
        // The old single-board controller is GONE; the unified panel +
        // status-item trigger + the two columns must all exist.
        XCTAssertFalse(
            names.contains("BoardPanelController.swift"),
            "BoardPanelController.swift must be removed — the second "
            + "cursor-anchored panel + 'Open board' toggle is the rejected "
            + "design; the UI is now ONE PanelController")
        for f in ["PanelController.swift", "BoardPanelView.swift",
                  "StatusItemController.swift", "FundamentalsView.swift"] {
            XCTAssertTrue(
                names.contains(f),
                "Wave-C v2 source \(f) missing — the unified two-column "
                + "panel + status-item trigger must exist")
        }
        // None of the UI-plumbing sources may touch the engine entry point: no
        // Process, no write, no JSON encode, no sqlite, no inline data.
        let forbidden = [
            "Process()", ".write(to:", "write(toFile:",
            "createFile(atPath:", "JSONEncoder(",
            "FileHandle(forWritingAtPath:", "FileHandle(forWritingTo:",
            "sqlite3_open(", "SQLITE_OPEN_READWRITE", "SQLITE_OPEN_CREATE",
            "ContractGuard.runScan", "ContractGuard.runRecord",
        ]
        for (name, body) in sources
        where ["PanelController.swift", "BoardPanelView.swift",
               "StatusItemController.swift"].contains(name) {
            let code = stripComments(body)
            for token in forbidden {
                XCTAssertFalse(
                    code.contains(token),
                    "\(name) contains '\(token)' — the unified panel + "
                    + "status-item trigger must render/relay only and must "
                    + "NOT itself touch the engine entry point (the hosted habit "
                    + "view relays clicks via the existing single entry point)")
            }
        }
    }

    func testOneClickOpensBothPanelsBoardOnTheRight() throws {
        let sources = try swiftSources()

        // (a) The board toggle / "Open board" button is GONE everywhere.
        for (name, body) in sources {
            let code = stripComments(body)
            XCTAssertFalse(
                code.contains("boardControl"),
                "\(name) still references boardControl — the manual "
                + "'Open board' control is the rejected design (the user)")
            XCTAssertFalse(
                code.contains("boardPanel.toggle()")
                || code.contains("boardPanel.isOpen"),
                "\(name) still relays a separate board toggle — one "
                + "click must open BOTH panels, no second gesture")
            XCTAssertFalse(
                code.contains("Open board") || code.contains("Hide board"),
                "\(name) still has an 'Open/Hide board' label — there is "
                + "no manual board button anymore")
            XCTAssertFalse(
                code.contains("BoardPanelController"),
                "\(name) still references the removed BoardPanelController")
        }

        // (b) The LEFT column (FundamentalsView) hosts NO board content and
        // owns no board toggle — it is fundamentals only.
        let fv = sources.first { $0.name == "FundamentalsView.swift" }
        XCTAssertNotNil(fv, "FundamentalsView.swift must exist")
        let fcode = stripComments(fv!.body)
        XCTAssertFalse(
            fcode.contains("HabitsWidgetView(")
            || fcode.contains("WidgetHostView("),
            "FundamentalsView must NOT embed the widget board — the board "
            + "is the SEPARATE RIGHT column shown simultaneously")
        XCTAssertFalse(
            fcode.contains("For your next briefing"),
            "research surfacing must be a board card, not a left-column "
            + "section")

        // (c) The PanelController builds ONE panel with BOTH columns, board
        // to the RIGHT of fundamentals (the layout invariant), and the
        // status item is a PURE TRIGGER that toggles the whole pair.
        let pc = sources.first { $0.name == "PanelController.swift" }
        XCTAssertNotNil(pc, "PanelController.swift must exist")
        let pcode = stripComments(pc!.body)
        XCTAssertTrue(
            pcode.contains("HStack(spacing: 0)"),
            "PanelController must lay the two columns in one zero-spacing "
            + "HStack (edge-to-edge, board on the RIGHT)")
        XCTAssertTrue(
            pcode.contains("FundamentalsView(")
            && pcode.contains("BoardPanelView("),
            "the single panel must host BOTH columns simultaneously")
        // Source order proves the layout: FundamentalsView appears BEFORE
        // BoardPanelView in the HStack → board renders to its RIGHT.
        if let fIdx = pcode.range(of: "FundamentalsView("),
           let bIdx = pcode.range(of: "BoardPanelView(") {
            XCTAssertTrue(
                fIdx.lowerBound < bIdx.lowerBound,
                "FundamentalsView must precede BoardPanelView in the "
                + "HStack so the board is on the RIGHT")
        } else {
            XCTFail("both columns must be constructed in PanelController")
        }

        let si = sources.first { $0.name == "StatusItemController.swift" }
        XCTAssertNotNil(si, "StatusItemController.swift must exist")
        let scode = stripComments(si!.body)
        XCTAssertTrue(
            scode.contains("NSStatusItem") && scode.contains("panel.toggle()"),
            "the status item must own an NSStatusItem and be a PURE "
            + "TRIGGER that toggles the unified panel on one click")
        XCTAssertTrue(
            scode.contains("convertToScreen"),
            "anchoring must use the status-item button's SCREEN frame "
            + "(iStat-Menus style), never the cursor")

        // (d) MenuBarExtra is GONE — its popover geometry is exactly why the
        // old board landed 'under' the popover.
        for (name, body) in sources {
            XCTAssertFalse(
                stripComments(body).contains("MenuBarExtra"),
                "\(name) still uses MenuBarExtra — its AppKit-owned popover "
                + "geometry cannot host the deterministic two-column layout")
        }

        // (e) The board view still renders the migrated research card.
        let bv = sources.first { $0.name == "BoardPanelView.swift" }
        XCTAssertNotNil(bv)
        XCTAssertTrue(
            bv!.body.contains("researchSurfacingWidget"),
            "the research surfacing must render as a board widget card")
    }

    // MARK: geometry invariant — board is to the RIGHT, never under
    //
    // DoD #2: assert PanelController.computeFrame places the board column's
    // origin.x ≥ the fundamentals column's maxX (board is RIGHT, not under)
    // and the two columns share the SAME top, for BOTH the normal case and
    // the right-screen-edge overflow case (where the whole pair shifts LEFT
    // but the board is never clipped and stays to the RIGHT of fundamentals).

    func testTwoColumnGeometryBoardIsRightAndTopsAligned() {
        let W = PanelController.columnWidth
        let gap = PanelController.gapWidth
        let H = PanelController.panelHeight

        // A typical large screen visible frame.
        let screen = NSRect(x: 0, y: 0, width: 2560, height: 1440)

        func assertInvariant(
            _ panelFrame: NSRect, _ label: String) {
            // The panel hosts: [ fundamentals(W) | gap | board(W) ].
            // Compute each column's screen sub-rect from the panel origin.
            let fundamentals = NSRect(
                x: panelFrame.minX, y: panelFrame.minY,
                width: W, height: H)
            let board = NSRect(
                x: panelFrame.minX + W + gap, y: panelFrame.minY,
                width: W, height: H)

            // Board is to the RIGHT of fundamentals — its leading edge is
            // at/after the fundamentals' trailing edge. Never under/over.
            XCTAssertGreaterThanOrEqual(
                board.minX, fundamentals.maxX,
                "\(label): board.origin.x must be ≥ fundamentals.maxX "
                + "(board to the RIGHT, never under) — got board.minX="
                + "\(board.minX) fundamentals.maxX=\(fundamentals.maxX)")
            // Edge-to-edge: the gap between them is ≤ 2pt (touching).
            XCTAssertLessThanOrEqual(
                board.minX - fundamentals.maxX, 2.0,
                "\(label): the gap between the columns must be ≤2pt "
                + "(edge-to-edge)")
            // Tops aligned: identical maxY (Cocoa top = origin.y + height).
            XCTAssertEqual(
                board.maxY, fundamentals.maxY, accuracy: 0.0001,
                "\(label): both columns must share the SAME top")
            // The board must stay fully on-screen (never clipped).
            XCTAssertLessThanOrEqual(
                board.maxX, screen.maxX,
                "\(label): the board must stay fully on-screen (never "
                + "clipped at the right edge)")
            XCTAssertGreaterThanOrEqual(
                fundamentals.minX, screen.minX,
                "\(label): the pair must stay fully on-screen")
        }

        // Case 1 — NORMAL: status item near the screen's left/center; the
        // pair fits comfortably and centers under the icon.
        let normal = PanelController.computeFrame(
            statusItemFrame: NSRect(
                x: 400, y: 1416, width: 24, height: 22),
            screenVisibleFrame: screen)
        assertInvariant(normal, "normal")

        // Case 2 — RIGHT SCREEN EDGE: status item at the far right (typical
        // menu-bar position). The desired centered origin would overflow the
        // right edge, so computeFrame must shift the WHOLE pair LEFT — the
        // board is still to the RIGHT of fundamentals and fully visible.
        let rightEdge = PanelController.computeFrame(
            statusItemFrame: NSRect(
                x: 2540, y: 1416, width: 24, height: 22),
            screenVisibleFrame: screen)
        assertInvariant(rightEdge, "right-screen-edge")
        // Prove the shift actually happened (origin clamped off the naive
        // centered position) and the board's right edge is on-screen.
        XCTAssertLessThanOrEqual(
            rightEdge.maxX, screen.maxX - 8 + 0.0001,
            "right-edge case must shift the pair LEFT so the board's "
            + "right edge stays on-screen")

        // Case 3 — no status item resolvable: falls back to a top-trailing
        // anchor; the invariant must STILL hold (board right, tops aligned).
        let fallback = PanelController.computeFrame(
            statusItemFrame: nil,
            screenVisibleFrame: screen)
        assertInvariant(fallback, "no-status-item-fallback")
    }

    func testDenseGridHeightIsHardCappedAndPeriodInvariant() {
        // Defect #1 — MACHINE-CHECKED, not a prose claim. The GitHub model:
        // exactly 7 weekday rows; a longer period adds week-COLUMNS only.
        // `denseGridHeight()` deliberately takes NO period argument; assert
        // it is identical across every engine-supported window so the grid
        // can never grow taller for a longer period again.
        let h = HabitsWidgetView.denseGridHeight()
        // The exact GitHub-model cap: 7 cells + 6 inter-row gaps.
        let expected =
            HabitsWidgetView.cell * 7 + HabitsWidgetView.cellGap * 6
        XCTAssertEqual(h, expected, accuracy: 0.0001,
            "dense grid height must be exactly 7 cells + 6 gaps")
        // Period-invariance: the height is the SAME for 30/90/120/365d.
        // (The function takes no period — that is the structural guarantee;
        // we still assert the value is stable and equals the cap for each.)
        for _ in [30, 90, 120, 365] {
            XCTAssertEqual(
                HabitsWidgetView.denseGridHeight(), expected,
                accuracy: 0.0001,
                "dense grid height changed across periods — the 'too "
                + "tall / ~13 rows' defect regressed")
        }
        // The cap is a small, bounded value — a sanity bound so a future
        // edit that (e.g.) multiplied by week-count would fail here.
        XCTAssertLessThan(h, 120,
            "dense grid height must stay a tight 7-row cap, never grow "
            + "with the period")
        // weekColumns proves the GROWTH axis is horizontal: more days →
        // more COLUMNS, each still <= 7 rows tall.
        func cols(_ days: Int) -> Int {
            let cells = (0..<days).map {
                GridCell(
                    date: String(format: "2026-%02d-%02d",
                                 ($0 / 28) % 12 + 1, $0 % 28 + 1),
                    level: $0 % 5)
            }
            return HabitsWidgetView.weekColumns(cells).count
        }
        XCTAssertGreaterThan(cols(365), cols(120))
        XCTAssertGreaterThan(cols(120), cols(30),
            "a longer period must add week-COLUMNS (horizontal growth), "
            + "never rows")
        for days in [30, 90, 120, 365] {
            let cells = (0..<days).map {
                GridCell(date: String(format: "2026-01-%02d",
                                      $0 % 28 + 1), level: 1)
            }
            for c in HabitsWidgetView.weekColumns(cells) {
                XCTAssertLessThanOrEqual(c.count, 7,
                    "every week column must be <= 7 rows tall regardless "
                    + "of period (period \(days)d)")
            }
        }
    }

    func testGridIsReadOnlyCompactIsTheOnlyMutator() throws {
        // The Grid (dense) mode is now a READ-ONLY HabitKit contribution
        // chart: it shows the full history, scrolls horizontally, and NEVER
        // mutates. Marking lives in Compact only — its interactive `square`
        // relays via the single sanctioned `store.relayToggle` entry point. Assert
        // (source-grep, comment-stripped) that no `dense*` Grid render member
        // contains a mutator, while the Compact `square` still does, and the
        // period selector / densePeriodDays knob is gone.
        let v = try swiftSources()
            .first { $0.name == "HabitsWidgetView.swift" }
        XCTAssertNotNil(v)
        let code = stripComments(v!.body)

        // No leftover period selector / period knob anywhere in the view.
        XCTAssertFalse(code.contains("periodSelector"),
            "the dense period selector must be removed (full history)")
        XCTAssertFalse(code.contains("densePeriodDays"),
            "the view must not reference densePeriodDays anymore")

        // Grid always scrolls horizontally, anchored to the newest edge.
        XCTAssertTrue(code.contains("ScrollView(.horizontal"),
            "Grid must always use a horizontal ScrollView")
        XCTAssertTrue(code.contains("defaultScrollAnchor(.trailing)"),
            "Grid must anchor to the trailing (newest) edge")

        // Slice top-level members; assert every Grid-path member is inert.
        let members = Self.litmusMemberBodies(code)
        XCTAssertFalse(members.isEmpty, "could not slice member bodies")
        let gridMembers = members.filter {
            $0.name.hasPrefix("dense") || $0.name == "monthLabelColumns"
        }
        XCTAssertGreaterThanOrEqual(gridMembers.count, 4,
            "the dense* Grid render path must exist as several members")
        for m in gridMembers {
            for tok in ["relayToggle", "runRecord",
                        "onTapGesture", "Button("] {
                XCTAssertFalse(m.body.contains(tok),
                    "Grid render member `\(m.name)` contains '\(tok)' — "
                    + "Grid is READ-ONLY; only Compact may mutate")
            }
        }
        // Compact's interactive `square` is STILL the mutator.
        let compactSquare = members.first { $0.name == "square" }
        XCTAssertNotNil(compactSquare,
            "Compact's interactive `square(...)` must still exist")
        XCTAssertTrue(
            compactSquare!.body.contains("onTapGesture")
                && compactSquare!.body.contains("relayToggle"),
            "Compact's `square` must still relay clicks via the entry point")
        // The read-only Grid cell renderer must exist and be inert.
        let denseSquare = members.first { $0.name == "denseSquare" }
        XCTAssertNotNil(denseSquare,
            "the read-only Grid `denseSquare(...)` must exist")
        XCTAssertFalse(
            denseSquare!.body.contains("onTapGesture")
                || denseSquare!.body.contains("relayToggle"),
            "`denseSquare` must be non-interactive (no gesture/relay)")
    }

    func testPerHabitCoachIsCompactOnlyGridNeverRendersIt() throws {
        // The per-habit deterministic coach line is a Compact-only secondary
        // line. Grid stays purely visual: no `dense*` / month-label Grid
        // render member may reference `.coach`. Compact's `compactRow` must.
        let v = try swiftSources()
            .first { $0.name == "HabitsWidgetView.swift" }
        XCTAssertNotNil(v)
        let code = stripComments(v!.body)

        let members = Self.litmusMemberBodies(code)
        XCTAssertFalse(members.isEmpty, "could not slice member bodies")

        let compactRow = members.first { $0.name == "compactRow" }
        XCTAssertNotNil(compactRow, "Compact's `compactRow` must exist")
        XCTAssertTrue(compactRow!.body.contains(".coach"),
            "Compact's `compactRow` must render the per-habit coach line")

        let gridMembers = members.filter {
            $0.name.hasPrefix("dense") || $0.name == "monthLabelColumns"
        }
        XCTAssertGreaterThanOrEqual(gridMembers.count, 4,
            "the dense* Grid render path must exist as several members")
        for m in gridMembers {
            XCTAssertFalse(m.body.contains(".coach"),
                "Grid render member `\(m.name)` references `.coach` — "
                + "the coach line is Compact-only; Grid stays visual")
        }
    }

    func testLegacyHabitGridWidgetIsExcludedFromBoardDiscovery() throws {
        // The legacy v1 single-habit widget (habit-tracker/habit-grid) is
        // still emitted by the engine for back-compat but must NOT surface
        // on the generic board (it would be a stale "1 habit + coach"
        // card). WidgetHostStore filters it out by its stable uniqueKey.
        XCTAssertEqual(
            WidgetHostStore.legacyHabitGridKey, "habit-tracker/habit-grid")
        let v = try swiftSources()
            .first { $0.name == "WidgetHostStore.swift" }
        XCTAssertNotNil(v)
        let code = stripComments(v!.body)
        XCTAssertTrue(
            code.contains("SkillDiscovery.scan().widgets")
                && code.contains(
                    "filter { $0.uniqueKey != Self.legacyHabitGridKey }"),
            "rediscover() must filter the legacy habit-grid widget out")
    }

    /// Coarse top-level member slicer (comment-stripped input): (name, body)
    /// per `func`/`var` declaration up to the next sibling. Sufficient to
    /// grep one render path without bleeding into another.
    private static func litmusMemberBodies(
        _ code: String
    ) -> [(name: String, body: String)] {
        let lines = code.components(separatedBy: "\n")
        var starts: [(name: String, line: Int)] = []
        for (i, raw) in lines.enumerated() {
            let l = raw.trimmingCharacters(in: .whitespaces)
            let isDecl =
                l.hasPrefix("func ") || l.hasPrefix("private func ")
                || l.hasPrefix("static func ")
                || l.hasPrefix("nonisolated static func ")
                || l.hasPrefix("private static func ")
                || l.hasPrefix("var ") || l.hasPrefix("private var ")
                || l.hasPrefix("nonisolated static let ")
                || l.hasPrefix("static let ")
            guard isDecl else { continue }
            guard let kw = l.range(of: "func ")
                ?? l.range(of: "var ")
                ?? l.range(of: "let ") else { continue }
            let name = l[kw.upperBound...].prefix {
                $0.isLetter || $0.isNumber || $0 == "_"
            }
            guard !name.isEmpty else { continue }
            starts.append((String(name), i))
        }
        var out: [(name: String, body: String)] = []
        for (idx, s) in starts.enumerated() {
            let end = idx + 1 < starts.count
                ? starts[idx + 1].line : lines.count
            out.append((s.name,
                        lines[s.line..<end].joined(separator: "\n")))
        }
        return out
    }

    func testSingleScrollContextNoNestedVerticalScroll() throws {
        // Defect #2 — eliminate the double scrollbar. The board owns ONE
        // vertical ScrollView; the habit/widget child views must NOT nest
        // their own vertical ScrollView. (A horizontal ScrollView in the
        // dense grid is a DIFFERENT axis and is allowed — it does not
        // compete with the board's vertical scroll.)
        let sources = try swiftSources()

        // The board has exactly one vertical scroll context.
        let board = sources.first { $0.name == "BoardPanelView.swift" }
        XCTAssertNotNil(board)
        let bcode = stripComments(board!.body)
        XCTAssertTrue(
            bcode.contains("ScrollView(.vertical)"),
            "the board must own the single vertical scroll context")

        // The habit + widget host views must contain NO bare/vertical
        // ScrollView anymore (only the dense grid's explicit .horizontal
        // one is permitted).
        for n in ["HabitsWidgetView.swift", "WidgetHostView.swift"] {
            let v = sources.first { $0.name == n }
            XCTAssertNotNil(v, "\(n) must exist")
            let code = stripComments(v!.body)
            // No default (vertical) ScrollView: a `ScrollView {` or
            // `ScrollView(` not immediately scoped to `.horizontal`.
            XCTAssertFalse(
                code.contains("ScrollView {"),
                "\(n) still has a bare (vertical) ScrollView — the board "
                + "owns the single scroll context (defect #2)")
            XCTAssertFalse(
                code.contains("ScrollView(.vertical"),
                "\(n) still nests a vertical ScrollView — one scroll "
                + "context only (defect #2)")
            // Any ScrollView present must be the horizontal dense grid.
            if code.contains("ScrollView(") {
                XCTAssertTrue(
                    code.contains("ScrollView(.horizontal"),
                    "\(n) may only use a .horizontal ScrollView (the "
                    + "dense grid's sideways scroll), never a vertical one")
            }
        }
    }

    // MARK: invariant documentation assertion
    //
    // A failing compile-time-ish guarantee that the deletion invariant is
    // documented in the README (the human-facing contract surface).

    func testDeletionInvariantIsDocumented() throws {
        let thisFile = URL(fileURLWithPath: #filePath)
        let readme = thisFile
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("README.md")
        let text = try String(contentsOf: readme, encoding: .utf8)
        XCTAssertTrue(
            text.lowercased().contains("deleting")
            && text.contains("/gm"),
            "README must document the deletion invariant (/gm keeps working)")
    }
}
