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
        return "\(home)/Gaia/skills/iga-proactive"
    }

    /// The EXACT, only engine command this app may run. Mirrors the command
    /// documented in the brief & SKILL.md. Any change here is a contract
    /// change and must be reviewed against the frozen decision.
    ///
    ///   cd ~/Gaia/skills/iga-proactive \
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
        "cd ~/Gaia/skills/iga-proactive && "
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
    // This is the SECOND (and last) sanctioned subprocess constructor. Both
    // live here so the contract entry point stays exactly two named symbols and the
    // litmus can grep-prove nothing else execs.

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
        return "\(home)/Gaia/skills/habit-tracker"
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
        return "\(home)/Gaia/state"
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
}
