import Foundation

// MARK: - The hard contract, encoded
//
// INVARIANT (frozen, MemPalace decision iga/decisions/3542bae6):
//
//   The engine decides. The app only (a) renders engine state, (b) relays OS
//   events, (c) triggers the engine. ZERO job/admission/idempotency/budget
//   logic in Swift. Deleting this app must leave `/gm` inline fully working.
//
// Concretely the app is allowed exactly ONE side effect on the engine: it may
// EXEC the frozen scan command. It must NEVER write the state JSON file and
// NEVER write the sqlite ledger (LedgerReader enforces read-only at the
// driver; this file enforces the exec allow-list at the subprocess boundary).
//
// `ContractGuard` is the SOLE place a subprocess that touches the engine is
// constructed. The test target greps the source to assert no other
// subprocess/write path exists, and asserts this command shape.

enum ContractGuard {

    /// The repo's iga-proactive skill directory (engine lives here, frozen).
    /// Overridable via `IGA_SKILL_DIR` (sandboxed runs/tests); the DEFAULT is
    /// the correct absolute install path — NEVER empty, NEVER env-derived. A
    /// Finder/Spotlight-launched .app inherits no shell env, so the default
    /// must stand on its own.
    static func skillDir() -> String {
        if let env = ProcessInfo.processInfo.environment["IGA_SKILL_DIR"],
           !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/skills/iga-proactive"
    }

    /// The EXACT, only engine command this app may run. Mirrors the command
    /// documented in the brief & SKILL.md. Any change here is a contract
    /// change and must be reviewed against the frozen decision.
    ///
    ///   cd ~/Iga/skills/iga-proactive \
    ///     && PYTHONPATH=engine uv run python -m engine scan --json
    static let engineScanArgv: [String] = [
        "-c",
        "cd \"$IGA_SKILL_DIR\" && PYTHONPATH=engine uv run "
            + "python -m engine scan --json"
    ]

    /// Result of the one sanctioned engine invocation.
    struct ScanOutcome {
        let ok: Bool
        let exitCode: Int32
        let stdout: String
        let stderr: String
        let startedAt: Date
        let finishedAt: Date
    }

    /// Build the (and only the) sanctioned engine subprocess. Read-trigger
    /// only: it runs the engine's own scan; it performs no writes itself. The
    /// engine owns every admission decision and every state/ledger mutation.
    /// This is the SINGLE constructor of an engine subprocess in the codebase.
    static func engineScanProcess() -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = engineScanArgv
        var env = ProcessInfo.processInfo.environment
        env["IGA_SKILL_DIR"] = skillDir()
        p.environment = env
        return p
    }

    /// Execute the one sanctioned scan. Blocking; callers dispatch off-main.
    /// Centralized here so NO other file references a subprocess primitive —
    /// the contract entry point is exactly one symbol.
    static func runScan(timeout: TimeInterval = 90) -> ScanOutcome {
        let started = Date()
        let proc = engineScanProcess()
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe

        do {
            try proc.run()
        } catch {
            return ScanOutcome(
                ok: false, exitCode: -1, stdout: "",
                stderr: "failed to launch engine: \(error)",
                startedAt: started, finishedAt: Date())
        }

        // Drain pipes off the wait to avoid the 64KB pipe-buffer deadlock.
        let outData = drain(outPipe.fileHandleForReading)
        let errData = drain(errPipe.fileHandleForReading)

        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if proc.isRunning {
            proc.terminate()
            return ScanOutcome(
                ok: false, exitCode: -2, stdout: "",
                stderr: "engine scan timed out after \(Int(timeout))s",
                startedAt: started, finishedAt: Date())
        }

        let code = proc.terminationStatus
        return ScanOutcome(
            ok: code == 0,
            exitCode: code,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? "",
            startedAt: started,
            finishedAt: Date())
    }

    private static func drain(_ fh: FileHandle) -> Data {
        var data = Data()
        while true {
            let chunk = fh.availableData
            if chunk.isEmpty { break }
            data.append(chunk)
        }
        return data
    }

    /// Human-readable single line for the README / about box / tests.
    static let documentedCommand =
        "cd ~/Iga/skills/iga-proactive && "
        + "PYTHONPATH=engine uv run python -m engine scan --json"

    // MARK: - The ONLY sanctioned MUTATION entry point (Wave B habit record)
    //
    // The hard contract extended to the habit widget: clicking a square is a
    // MUTATION the app must NOT perform itself (no JSON write, no streak/goal
    // math). It relays to exactly ONE engine entry point — the habit-tracker
    // `record` CLI — analogous to the read-only scan entry point above. That CLI
    // mutates the substrate via frozen Wave-A code and re-emits the derived
    // widget JSON; the app only reads the refreshed file afterward.
    //
    // This is the SECOND sanctioned subprocess constructor; a THIRD — the
    // NON-MUTATING reproject (`habitReprojectProcess`/`runReproject`) — is
    // added below. All three live here so the contract entry point stays a small,
    // explicitly-named set and the litmus can grep-prove nothing else execs.
    // The reproject is contract-safe: it is to the record CLI exactly what
    // the read-only `scan` is to the engine — the app only TRIGGERS the
    // engine's own projection; it performs no write and decides nothing
    // (record.py --reproject leaves the substrate byte-identical).

    /// The habit-tracker skill directory (engine lives here, Wave-A frozen).
    /// Overridable via `IGA_HT_SKILL_DIR` (sandboxed runs/tests); the DEFAULT
    /// is the correct ABSOLUTE install path. CRITICAL for the Finder/Spotlight
    /// launch path: a LaunchServices-spawned .app inherits no shell env, so
    /// this default must be self-sufficient and is NEVER allowed to be empty
    /// — an empty value would make the record subprocess `cd ""` (a no-op
    /// that silently leaves the wrong cwd) and the relayed click would not
    /// persist. Resolution is pure (home dir / constant), no env reliance.
    static func habitTrackerSkillDir() -> String {
        if let env = ProcessInfo.processInfo.environment["IGA_HT_SKILL_DIR"],
           !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/skills/habit-tracker"
    }

    /// Absolute path to the frozen habit-tracker `record.py`. Resolved by the
    /// Swift app from the (absolute) skill dir — NOT a bare relative token the
    /// subprocess's cwd has to be correct for. Belt-and-braces with the
    /// guarded `cd`: even if the cwd were wrong, the interpreter still gets an
    /// absolute script path and the op runs (or fails LOUDLY, never silently).
    static func habitRecordScriptPath() -> String {
        "\(habitTrackerSkillDir())/engine/record.py"
    }

    /// The user's LIVE state root. The record CLI deliberately has NO implicit
    /// real-state default (privacy/data-loss guard), so the app passes this
    /// explicitly. Overridable via `IGA_STATE_DIR` for a sandboxed run/tests.
    static func liveStateDir() -> String {
        if let env = ProcessInfo.processInfo.environment["IGA_STATE_DIR"],
           !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/state"
    }

    /// Resolve an ABSOLUTE `uv` interpreter path. CRITICAL for the
    /// Finder/Spotlight/Launchpad launch path: a `.app` opened by
    /// LaunchServices inherits a minimal environment — `/bin/zsh -c` does
    /// NOT source `.zprofile`/`.zshrc`, so a bare `uv` is not on PATH and
    /// `record.py` silently fails to launch. We probe the well-known
    /// install locations, then fall back to a login shell's `which uv`,
    /// and finally to a bare `uv` (dev/terminal launches still work).
    /// Pure path resolution — no engine logic.
    static func resolvedUvPath() -> String {
        if let env = ProcessInfo.processInfo.environment["IGA_UV_PATH"],
           !env.isEmpty,
           FileManager.default.isExecutableFile(atPath: env) {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = [
            "\(home)/.local/bin/uv",
            "/opt/homebrew/bin/uv",
            "/usr/local/bin/uv",
            "/usr/bin/uv",
        ]
        for c in candidates
        where FileManager.default.isExecutableFile(atPath: c) {
            return c
        }
        // Last resort: ask a LOGIN+INTERACTIVE shell (sources the user's
        // profile, so PATH is populated) where uv is.
        let probe = Process()
        probe.executableURL = URL(fileURLWithPath: "/bin/zsh")
        probe.arguments = ["-lic", "command -v uv"]
        let pipe = Pipe()
        probe.standardOutput = pipe
        probe.standardError = Pipe()
        do {
            try probe.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            probe.waitUntilExit()
            let path = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !path.isEmpty,
               FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        } catch {
            // fall through to the bare-name fallback
        }
        return "uv"   // dev/terminal launch still has uv on PATH
    }

    /// A record operation the widget can relay. The app NEVER decides the
    /// resulting amount/streak — it only names the gesture; the engine
    /// computes everything.
    enum RecordOp: Equatable {
        case add                 // log a completion (engine: amount->max(1,+1))
        case remove              // clear the day (engine: amount->0, deletes)
        case setAmount(Int)      // set exactly N (engine enforces N>=0)
    }

    /// Build the (and only the) sanctioned habit-record subprocess. It runs
    /// the frozen `record` CLI with a MANDATORY explicit `--state-dir`. The
    /// app performs no write itself; the engine owns the mutation + the
    /// re-projection. SINGLE mutation-subprocess constructor in the codebase.
    static func habitRecordProcess(
        habitId: String,
        date: String,
        op: RecordOp,
        windowDays: Int
    ) -> Process {
        var opArgs: String
        switch op {
        case .add:               opArgs = "--add"
        case .remove:            opArgs = "--remove"
        case .setAmount(let n):  opArgs = "--set-amount \(n)"
        }
        // Single-quote the dynamic values so the shell can't reinterpret
        // them; ids/dates are engine-validated server-side regardless.
        //
        // Every input is resolved to an ABSOLUTE value by the Swift app and
        // passed via the explicit $IGA_HT_* env (set below) — the subprocess
        // assumes ZERO inherited shell env (a Finder/Spotlight-launched .app
        // gets none: no PATH, no exports). Specifically:
        //   * $IGA_HT_UV         — absolute uv (resolvedUvPath, never bare)
        //   * $IGA_HT_SKILL_DIR  — absolute skill dir (never empty)
        //   * $IGA_HT_STATE_DIR  — absolute live state root
        //   * $IGA_HT_RECORD_PY  — absolute record.py (not a cwd-relative
        //                          token); used as the script arg so a wrong
        //                          cwd cannot make `python engine/record.py`
        //                          a "can't open file" silent failure.
        // The `cd` is GUARDED: a bad/empty skill dir is a LOUD `exit 90`,
        // never a silent default that drops the click.
        let cmd =
            "cd \"$IGA_HT_SKILL_DIR\" || exit 90\n"
            + "\"$IGA_HT_UV\" run python "
            + "\"$IGA_HT_RECORD_PY\" --state-dir \"$IGA_HT_STATE_DIR\" "
            + "--habit '\(shellSafe(habitId))' "
            + "--date '\(shellSafe(date))' \(opArgs) "
            + "--days \(max(1, windowDays))"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-c", cmd]
        var env = ProcessInfo.processInfo.environment
        env["IGA_HT_SKILL_DIR"] = habitTrackerSkillDir()
        env["IGA_HT_STATE_DIR"] = liveStateDir()
        env["IGA_HT_UV"] = resolvedUvPath()
        env["IGA_HT_RECORD_PY"] = habitRecordScriptPath()
        p.environment = env
        return p
    }

    /// Reject anything but the safe id/date character set so a crafted value
    /// can never break out of the single-quoted shell argument. Engine-side
    /// validation is the real guarantee; this is defense in depth.
    private static func shellSafe(_ s: String) -> String {
        s.filter {
            $0.isLetter || $0.isNumber
                || $0 == "-" || $0 == "_" || $0 == ":"
        }
    }

    /// Execute the one sanctioned record op. Blocking; callers dispatch
    /// off-main. Returns the same outcome shape as `runScan` so the relay
    /// layer is uniform. The engine re-emits the widget JSON; the poller
    /// picks it up on its next tick.
    static func runRecord(
        habitId: String,
        date: String,
        op: RecordOp,
        windowDays: Int,
        timeout: TimeInterval = 60
    ) -> ScanOutcome {
        let started = Date()
        let proc = habitRecordProcess(
            habitId: habitId, date: date, op: op, windowDays: windowDays)
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        do {
            try proc.run()
        } catch {
            return ScanOutcome(
                ok: false, exitCode: -1, stdout: "",
                stderr: "failed to launch record entry point: \(error)",
                startedAt: started, finishedAt: Date())
        }
        let outData = drain(outPipe.fileHandleForReading)
        let errData = drain(errPipe.fileHandleForReading)
        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if proc.isRunning {
            proc.terminate()
            return ScanOutcome(
                ok: false, exitCode: -2, stdout: "",
                stderr: "record entry point timed out after \(Int(timeout))s",
                startedAt: started, finishedAt: Date())
        }
        let code = proc.terminationStatus
        return ScanOutcome(
            ok: code == 0,
            exitCode: code,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? "",
            startedAt: started,
            finishedAt: Date())
    }

    /// Human-readable single line documenting the mutation entry point shape.
    static let documentedRecordCommand =
        "cd <abs-skill-dir> || exit 90 ; <abs-uv> run python "
        + "<abs-record.py> --state-dir <abs-live-state> --habit <id> "
        + "--date <YYYY-MM-DD> (--add | --remove | --set-amount N) --days N"

    // MARK: - The NON-MUTATING refresh entry point (cold-launch staleness fix)
    //
    // WHY: a Finder/Spotlight-launched .app opened after a Mac restart (no
    // scan/record since yesterday) would otherwise render a DAY-STALE window
    // — the engine's last-emitted `today` is behind the real date and there
    // is no cell for today until some mutation incidentally re-projects. The
    // app triggers this on launch (and whenever it notices the polled JSON's
    // `today` is behind the system date) so streak/goal/coach/`today` are
    // current WITHOUT a fake write. It is the projection analogue of the
    // read-only `scan` entry point: the engine owns the projection, the app only
    // triggers it. `record.py --reproject` performs NO substrate mutation
    // (the substrate file is byte-identical before/after — proven).

    /// Build the (and only the) sanctioned NON-MUTATING reproject subprocess.
    /// Same env-independent contract as the record entry point: every input is an
    /// absolute literal resolved by Swift and passed via the explicit
    /// $IGA_HT_* env (a Finder/Spotlight .app inherits none), the `cd` is a
    /// LOUD `exit 90`, and the script is referenced by its absolute path so a
    /// wrong cwd cannot make it a silent failure. No --habit/--date/op: this
    /// cannot mutate by construction.
    static func habitReprojectProcess(windowDays: Int) -> Process {
        let cmd =
            "cd \"$IGA_HT_SKILL_DIR\" || exit 90\n"
            + "\"$IGA_HT_UV\" run python "
            + "\"$IGA_HT_RECORD_PY\" --state-dir \"$IGA_HT_STATE_DIR\" "
            + "--reproject --days \(max(1, windowDays))"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-c", cmd]
        var env = ProcessInfo.processInfo.environment
        env["IGA_HT_SKILL_DIR"] = habitTrackerSkillDir()
        env["IGA_HT_STATE_DIR"] = liveStateDir()
        env["IGA_HT_UV"] = resolvedUvPath()
        env["IGA_HT_RECORD_PY"] = habitRecordScriptPath()
        p.environment = env
        return p
    }

    /// Execute the one sanctioned reproject. Blocking; callers dispatch
    /// off-main. Same outcome shape as `runScan`/`runRecord` so the relay
    /// layer is uniform. Non-mutating by construction.
    static func runReproject(
        windowDays: Int,
        timeout: TimeInterval = 60
    ) -> ScanOutcome {
        let started = Date()
        let proc = habitReprojectProcess(windowDays: windowDays)
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        do {
            try proc.run()
        } catch {
            return ScanOutcome(
                ok: false, exitCode: -1, stdout: "",
                stderr: "failed to launch reproject entry point: \(error)",
                startedAt: started, finishedAt: Date())
        }
        let outData = drain(outPipe.fileHandleForReading)
        let errData = drain(errPipe.fileHandleForReading)
        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if proc.isRunning {
            proc.terminate()
            return ScanOutcome(
                ok: false, exitCode: -2, stdout: "",
                stderr: "reproject entry point timed out after \(Int(timeout))s",
                startedAt: started, finishedAt: Date())
        }
        let code = proc.terminationStatus
        return ScanOutcome(
            ok: code == 0,
            exitCode: code,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? "",
            startedAt: started,
            finishedAt: Date())
    }

    /// Human-readable single line documenting the reproject entry point shape.
    static let documentedReprojectCommand =
        "cd <abs-skill-dir> || exit 90 ; <abs-uv> run python "
        + "<abs-record.py> --state-dir <abs-live-state> --reproject --days N"

    // MARK: - The habit-MANAGEMENT mutation entry point (Wave D: ⋯ menu)
    //
    // Renaming / deleting / goal-editing a habit and importing / exporting
    // the tracker are MUTATIONS (export is a read of intimate data). Same
    // hard contract as the record entry point: the app issues NO write and decides
    // NOTHING — it relays a NAMED intent to exactly one engine entry point
    // (engine/manage.py), which mutates the substrate via frozen Wave-A code
    // and re-emits the widget JSON. This is the FOURTH (and last) sanctioned
    // subprocess constructor; all four live here so the litmus can
    // grep-prove nothing else execs.

    /// Absolute path to the frozen habit-tracker `manage.py` (resolved from
    /// the absolute skill dir — never a cwd-relative token, same reasoning
    /// as `habitRecordScriptPath`).
    static func habitManageScriptPath() -> String {
        "\(habitTrackerSkillDir())/engine/manage.py"
    }

    /// A management intent the ⋯ menu can relay. The app NEVER performs the
    /// mutation — it only names it; the engine does everything.
    enum ManageOp: Equatable {
        case rename(name: String)
        case delete
        case setGoal(
            period: String, target: Int?,
            perDayTarget: Int?, allowExceed: Bool)
        case exportTo(path: String)
        case importFrom(path: String)
        case setOrder(position: Int)
        case setArchived(Bool)
        case setColor(hex: String)
    }

    /// Single-quote a value safely for `/bin/zsh -c` (a name or a file path
    /// may contain spaces / metacharacters; the charset `shellSafe` is for
    /// engine-validated ids only and would corrupt these). Closes the quote,
    /// inserts an escaped literal quote, reopens — the classic POSIX idiom.
    private static func singleQuoted(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// The manage-specific flag string for one intent. ids go through the
    /// charset filter (engine-validated regardless); names/paths through
    /// proper single-quote escaping; period is from a fixed set.
    private static func manageArgs(
        habitId: String?, op: ManageOp
    ) -> String {
        let habitFlag = habitId.map {
            " --habit '\(shellSafe($0))'"
        } ?? ""
        switch op {
        case .rename(let name):
            return "--rename \(singleQuoted(name))\(habitFlag)"
        case .delete:
            return "--delete\(habitFlag)"
        case let .setGoal(period, target, perDay, allowExceed):
            var a = "--set-goal\(habitFlag) "
                + "--period \(singleQuoted(period))"
            if let t = target { a += " --target \(max(1, t))" }
            if let p = perDay { a += " --per-day-target \(max(1, p))" }
            a += allowExceed ? " --allow-exceed" : " --no-allow-exceed"
            return a
        case .exportTo(let path):
            return "--export \(singleQuoted(path))"
        case .importFrom(let path):
            return "--import \(singleQuoted(path))"
        case .setOrder(let position):
            return "--set-order \(max(1, position))\(habitFlag)"
        case .setArchived(let on):
            return (on ? "--archive" : "--unarchive") + habitFlag
        case .setColor(let hex):
            return "--set-color \(singleQuoted(hex))\(habitFlag)"
        }
    }

    /// Build the (and only the) sanctioned habit-management subprocess.
    /// Identical env-independent contract as the record entry point: every input
    /// absolute via $IGA_HT_* (a Finder/Spotlight .app inherits none), the
    /// `cd` a LOUD `exit 90`, the script referenced by its absolute path.
    static func habitManageProcess(
        habitId: String?, op: ManageOp, windowDays: Int
    ) -> Process {
        let cmd =
            "cd \"$IGA_HT_SKILL_DIR\" || exit 90\n"
            + "\"$IGA_HT_UV\" run python "
            + "\"$IGA_HT_MANAGE_PY\" --state-dir \"$IGA_HT_STATE_DIR\" "
            + manageArgs(habitId: habitId, op: op)
            + " --days \(max(1, windowDays))"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-c", cmd]
        var env = ProcessInfo.processInfo.environment
        env["IGA_HT_SKILL_DIR"] = habitTrackerSkillDir()
        env["IGA_HT_STATE_DIR"] = liveStateDir()
        env["IGA_HT_UV"] = resolvedUvPath()
        env["IGA_HT_MANAGE_PY"] = habitManageScriptPath()
        p.environment = env
        return p
    }

    /// Execute the one sanctioned management op. Blocking; callers dispatch
    /// off-main. Same outcome shape as the other entry points.
    static func runManage(
        habitId: String?, op: ManageOp,
        windowDays: Int, timeout: TimeInterval = 90
    ) -> ScanOutcome {
        let started = Date()
        let proc = habitManageProcess(
            habitId: habitId, op: op, windowDays: windowDays)
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        do {
            try proc.run()
        } catch {
            return ScanOutcome(
                ok: false, exitCode: -1, stdout: "",
                stderr: "failed to launch manage entry point: \(error)",
                startedAt: started, finishedAt: Date())
        }
        let outData = drain(outPipe.fileHandleForReading)
        let errData = drain(errPipe.fileHandleForReading)
        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if proc.isRunning {
            proc.terminate()
            return ScanOutcome(
                ok: false, exitCode: -2, stdout: "",
                stderr: "manage entry point timed out after \(Int(timeout))s",
                startedAt: started, finishedAt: Date())
        }
        let code = proc.terminationStatus
        return ScanOutcome(
            ok: code == 0,
            exitCode: code,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? "",
            startedAt: started,
            finishedAt: Date())
    }

    /// Human-readable single line documenting the manage entry point shape.
    static let documentedManageCommand =
        "cd <abs-skill-dir> || exit 90 ; <abs-uv> run python "
        + "<abs-manage.py> --state-dir <abs-live-state> "
        + "(--rename N | --delete | --set-goal … | --export P | "
        + "--import P) [--habit <id>] --days N"

    // MARK: - The mood-tracker INGEST trigger entry point (semi-automatic backfill)
    //
    // WHY: The source mood app has no API and no silent sync; the user drops a CSV
    // export into a configurable iCloud-Drive folder (manual "Save to
    // Files", or a scheduled iOS Shortcut copying the mood app's local Backup.csv).
    // The ALWAYS-ON menu-bar app — the chosen reliable host on the Mac
    // mini, deliberately NOT a LaunchAgent — TRIGGERS the mood-tracker's
    // own ingest. That CLI imports the export iff it changed (FROZEN
    // importer) and re-emits the Mood widget. This is the projection
    // analogue of the read-only `scan`/`reproject` entry points: the engine
    // decides every write; the app only triggers. ingest.py is idempotent
    // — stable per-row ids (`m-sha1(Date|MoodKey|Notes)`) merged in
    // place + a file-content sha1 marker — so re-running with no new (or
    // an overlapping) export NEVER duplicates and is a cheap no-op. This
    // is the FIFTH (and last) sanctioned subprocess constructor; all live
    // here so the litmus can grep-prove the entry point set is closed.

    /// The mood-tracker skill dir (engine frozen). Overridable via
    /// `IGA_MT_SKILL_DIR` (sandboxed runs/tests); the DEFAULT is the
    /// correct absolute install path — never empty, never env-derived (a
    /// Finder/Spotlight-launched .app inherits no shell env).
    static func moodTrackerSkillDir() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_MT_SKILL_DIR"], !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/skills/mood-tracker"
    }

    /// Absolute path to the frozen mood-tracker `ingest.py` (resolved from
    /// the absolute skill dir — never a cwd-relative token).
    static func moodIngestScriptPath() -> String {
        "\(moodTrackerSkillDir())/engine/ingest.py"
    }

    /// The configurable folder the app watches and ingest scans. Default =
    /// the iCloud-Drive `Iga/` inbox (universal drop + automation target —
    /// manual export AND the scheduled Shortcut land here). Override via
    /// `IGA_MOOD_WATCH_DIR` (OSS users point it anywhere; tests sandbox
    /// it). Pure path resolution — no engine logic.
    static func moodWatchDir() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_MOOD_WATCH_DIR"], !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        // iCloud Drive's user-visible "Documents" maps to the
        // `…/CloudDocs/Documents` subdir (verified on the Mac mini); the
        // `Iga/` inbox lives there. `IGA_MOOD_WATCH_DIR` overrides for
        // OSS users / a different layout.
        return "\(home)/Library/Mobile Documents/"
            + "com~apple~CloudDocs/Documents/Iga"
    }

    /// Filename glob the ingest matches inside `moodWatchDir()`. Default =
    /// the actual How We Feel export filename, so non-mood files dropped in
    /// the same inbox (e.g. bank-statement CSVs) are never picked up. Mirror
    /// of `moodWatchDir()`: override via `IGA_MOOD_EXPORT_GLOB` (OSS users
    /// whose export is named differently point it elsewhere). Pure literal —
    /// no engine logic. Replaces an earlier launchctl/LaunchAgent workaround
    /// (prohibited — the whole point of this app is to avoid those).
    static func moodExportGlob() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_MOOD_EXPORT_GLOB"], !env.isEmpty {
            return env
        }
        return "HowWeFeelEmotions.csv"
    }

    /// Build the (and only the) sanctioned mood-ingest subprocess. Same
    /// env-independent contract as the habit entry points: every input is an
    /// absolute literal resolved by Swift and passed via the explicit
    /// $IGA_MT_* env (a Finder/Spotlight .app inherits none), the `cd` is
    /// a LOUD `exit 90`, the script is referenced by its absolute path so
    /// a wrong cwd cannot make it a silent failure. The app decides
    /// nothing — ingest.py owns the import + re-projection.
    static func moodIngestProcess() -> Process {
        let cmd =
            "cd \"$IGA_MT_SKILL_DIR\" || exit 90\n"
            + "\"$IGA_MT_UV\" run python "
            + "\"$IGA_MT_INGEST_PY\" --state-dir \"$IGA_MT_STATE_DIR\" "
            + "--watch-dir \"$IGA_MOOD_WATCH_DIR\" "
            + "--glob \"$IGA_MOOD_EXPORT_GLOB\""
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-c", cmd]
        var env = ProcessInfo.processInfo.environment
        env["IGA_MT_SKILL_DIR"] = moodTrackerSkillDir()
        env["IGA_MT_STATE_DIR"] = liveStateDir()
        env["IGA_MT_UV"] = resolvedUvPath()
        env["IGA_MT_INGEST_PY"] = moodIngestScriptPath()
        env["IGA_MOOD_WATCH_DIR"] = moodWatchDir()
        env["IGA_MOOD_EXPORT_GLOB"] = moodExportGlob()
        p.environment = env
        return p
    }

    /// Execute the one sanctioned mood ingest. Blocking; callers dispatch
    /// off-main. Same outcome shape as the other entry points. Idempotent +
    /// non-destructive by construction (the engine never duplicates and
    /// never deletes the user's file).
    static func runMoodIngest(
        timeout: TimeInterval = 90
    ) -> ScanOutcome {
        let started = Date()
        let proc = moodIngestProcess()
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        do {
            try proc.run()
        } catch {
            return ScanOutcome(
                ok: false, exitCode: -1, stdout: "",
                stderr: "failed to launch mood-ingest entry point: \(error)",
                startedAt: started, finishedAt: Date())
        }
        let outData = drain(outPipe.fileHandleForReading)
        let errData = drain(errPipe.fileHandleForReading)
        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if proc.isRunning {
            proc.terminate()
            return ScanOutcome(
                ok: false, exitCode: -2, stdout: "",
                stderr: "mood-ingest entry point timed out after "
                    + "\(Int(timeout))s",
                startedAt: started, finishedAt: Date())
        }
        let code = proc.terminationStatus
        return ScanOutcome(
            ok: code == 0,
            exitCode: code,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? "",
            startedAt: started,
            finishedAt: Date())
    }

    /// Human-readable single line documenting the mood-ingest entry point shape.
    static let documentedMoodIngestCommand =
        "cd <abs-mood-skill-dir> || exit 90 ; <abs-uv> run python "
        + "<abs-ingest.py> --state-dir <abs-live-state> "
        + "--watch-dir <abs-watch-folder>"

    // MARK: - The email-triage trigger entry point (6th — last LaunchAgent retired)
    //
    // email-triage was the FINAL pre-menu-bar LaunchAgent. It is a
    // DETERMINISTIC daily Node CLI (the `email` skill's triage --apply),
    // NOT an LLM worker — so it is deliberately NOT a proactive.yaml
    // spawn_worker job (that engine only dispatches ledger-governed
    // workers). It is the SAME shape as the mood-ingest trigger: the
    // always-on app fires the skill's OWN proven wrapper on a daily
    // cadence (the wrapper self-sets PATH/cwd and writes its dated JSON
    // log, byte-for-byte the launchd-era behaviour). The app only
    // TRIGGERS; the skill decides everything. SIXTH (and last) sanctioned
    // subprocess constructor — the entry point set stays closed + grep-provable.

    /// Absolute path to the email skill's self-contained triage wrapper.
    /// Overridable via `IGA_EMAIL_TRIAGE_SCRIPT` (sandboxed runs/tests);
    /// the default is the correct absolute install path (a
    /// Finder/Spotlight-launched .app inherits no shell env).
    static func emailTriageScript() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_EMAIL_TRIAGE_SCRIPT"], !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/skills/email/engine/launchd/iga-email-triage"
    }

    /// Build the (and only the) sanctioned email-triage subprocess. The
    /// wrapper is self-contained (its own PATH/cwd/logging); we invoke it
    /// by ABSOLUTE path through /bin/zsh with a guarded executable check
    /// so a missing/renamed wrapper is a LOUD `exit 90`, never a silent
    /// no-op. No args/flags are added — behaviour stays identical to the
    /// retired LaunchAgent.
    static func emailTriageProcess() -> Process {
        let cmd =
            "[ -x \"$IGA_EMAIL_TRIAGE\" ] || exit 90\n"
            + "exec \"$IGA_EMAIL_TRIAGE\""
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-c", cmd]
        var env = ProcessInfo.processInfo.environment
        env["IGA_EMAIL_TRIAGE"] = emailTriageScript()
        p.environment = env
        return p
    }

    /// Execute the one sanctioned email triage. Blocking; callers
    /// dispatch off-main. Same outcome shape as the other entry points. Triage
    /// hits Gmail + applies labels, so the timeout is generous.
    static func runEmailTriage(
        timeout: TimeInterval = 300
    ) -> ScanOutcome {
        let started = Date()
        let proc = emailTriageProcess()
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        do {
            try proc.run()
        } catch {
            return ScanOutcome(
                ok: false, exitCode: -1, stdout: "",
                stderr: "failed to launch email-triage entry point: \(error)",
                startedAt: started, finishedAt: Date())
        }
        let outData = drain(outPipe.fileHandleForReading)
        let errData = drain(errPipe.fileHandleForReading)
        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if proc.isRunning {
            proc.terminate()
            return ScanOutcome(
                ok: false, exitCode: -2, stdout: "",
                stderr: "email-triage entry point timed out after "
                    + "\(Int(timeout))s",
                startedAt: started, finishedAt: Date())
        }
        let code = proc.terminationStatus
        return ScanOutcome(
            ok: code == 0,
            exitCode: code,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? "",
            startedAt: started,
            finishedAt: Date())
    }

    /// Human-readable single line documenting the email-triage entry point.
    static let documentedEmailTriageCommand =
        "[ -x <abs-iga-email-triage-wrapper> ] || exit 90 ; "
        + "exec <abs-iga-email-triage-wrapper>"

    // MARK: - Notify entry point (osascript)
    //
    // The app is ad-hoc signed → macOS drops its UNUserNotifications. The
    // reliable $0 path is `osascript display notification`, delivered by
    // Apple's signed automation host (no Developer ID needed). title/body
    // are passed as `argv` items — NOT interpolated into the script — so
    // there is zero AppleScript injection surface. This is the ONLY
    // sanctioned constructor for the notify subprocess.

    static func notifyProcess(title: String, body: String) -> Process {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = [
            "-e", "on run argv",
            "-e", "display notification (item 1 of argv) "
                + "with title (item 2 of argv)",
            "-e", "end run",
            "--", body, title,
        ]
        return p
    }

    /// Post one banner. Blocking but fast; callers dispatch off-main.
    /// Returns whether osascript exited 0 + a short detail for the
    /// Settings test (this is how we know delivery actually worked).
    @discardableResult
    static func runNotify(
        title: String, body: String, timeout: TimeInterval = 8
    ) -> (ok: Bool, detail: String) {
        let proc = notifyProcess(title: title, body: body)
        let errPipe = Pipe()
        proc.standardError = errPipe
        do { try proc.run() } catch {
            return (false, "failed to launch osascript: \(error)")
        }
        let errData = drain(errPipe.fileHandleForReading)
        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline { usleep(50_000) }
        if proc.isRunning {
            proc.terminate()
            return (false, "osascript timed out after \(Int(timeout))s")
        }
        let code = proc.terminationStatus
        let err = String(data: errData, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return code == 0
            ? (true, "delivered via osascript")
            : (false, "osascript exit \(code)"
                + (err.isEmpty ? "" : ": \(err.prefix(160))"))
    }

    // MARK: - The research-dispatch trigger entry point (7th)
    //
    // Proactive-research dispatch — same shape as email-triage: the always-on
    // app fires the skill's OWN self-contained wrapper
    // (skills/iga-proactive-research/engine/iga-research-dispatch) on a daily
    // cadence. The wrapper runs the engine scan (atomic ledger claim + governor
    // gate + dedup) then dispatches each governor-approved WORKER_REQUEST via
    // headless `claude -p` (MAX subscription), which files the research drawer
    // via the IgaMemory MCP. ZERO research logic in Swift — the app only
    // TRIGGERS; the engine + wrapper decide everything. This keeps proactive
    // research OUT of the interactive `/gm` session.

    /// Absolute path to the research-dispatch wrapper. Overridable via
    /// `IGA_RESEARCH_DISPATCH_SCRIPT` (sandboxed runs/tests); the default is
    /// the correct absolute install path (a Finder/Spotlight-launched .app
    /// inherits no shell env).
    static func researchDispatchScript() -> String {
        if let env = ProcessInfo.processInfo
            .environment["IGA_RESEARCH_DISPATCH_SCRIPT"], !env.isEmpty {
            return env
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/Iga/skills/iga-proactive-research/engine/iga-research-dispatch"
    }

    /// Build the (and only the) sanctioned research-dispatch subprocess. The
    /// wrapper is self-contained (its own PATH/cwd/logging); invoked by
    /// ABSOLUTE path through /bin/zsh with a guarded executable check so a
    /// missing/renamed wrapper is a LOUD `exit 90`, never a silent no-op.
    static func researchDispatchProcess() -> Process {
        let cmd =
            "[ -x \"$IGA_RESEARCH_DISPATCH\" ] || exit 90\n"
            + "exec \"$IGA_RESEARCH_DISPATCH\""
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-c", cmd]
        var env = ProcessInfo.processInfo.environment
        env["IGA_RESEARCH_DISPATCH"] = researchDispatchScript()
        p.environment = env
        return p
    }

    /// Execute one sanctioned research dispatch. Blocking; callers dispatch
    /// off-main. Generous timeout: the wrapper may run up to 2 topics × ~40 min
    /// of headless `claude -p` deep research.
    static func runResearchDispatch(
        timeout: TimeInterval = 5400
    ) -> ScanOutcome {
        let started = Date()
        let proc = researchDispatchProcess()
        let outPipe = Pipe()
        let errPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        do {
            try proc.run()
        } catch {
            return ScanOutcome(
                ok: false, exitCode: -1, stdout: "",
                stderr: "failed to launch research-dispatch entry point: \(error)",
                startedAt: started, finishedAt: Date())
        }
        let outData = drain(outPipe.fileHandleForReading)
        let errData = drain(errPipe.fileHandleForReading)
        let deadline = Date().addingTimeInterval(timeout)
        while proc.isRunning && Date() < deadline {
            usleep(50_000)
        }
        if proc.isRunning {
            proc.terminate()
            return ScanOutcome(
                ok: false, exitCode: -2, stdout: "",
                stderr: "research-dispatch entry point timed out after "
                    + "\(Int(timeout))s",
                startedAt: started, finishedAt: Date())
        }
        let code = proc.terminationStatus
        return ScanOutcome(
            ok: code == 0,
            exitCode: code,
            stdout: String(data: outData, encoding: .utf8) ?? "",
            stderr: String(data: errData, encoding: .utf8) ?? "",
            startedAt: started,
            finishedAt: Date())
    }
}
