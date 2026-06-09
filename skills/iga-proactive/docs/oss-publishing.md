# OSS publishing — current state and the deferred relocation map

This documents how `iga-proactive` fits the repo's three-layer OSS model, what
is *already* OSS-safe, and the exact migration map for the relocation that is
**intentionally deferred to publish time**. This is a decision with a
rationale, not an oversight.

## The repo's three-layer model (recap)

Per `CLAUDE.md` ("Generic vs personalized layer", `community_skills/`):

| Layer | Where | Owned by | `iga update` touches it? |
|---|---|---|---|
| Generic skill (the OSS template) | `community_skills/<pack>/` → copied to `skills/<pack>/` on install | upstream maintainer | yes (three-way merge) |
| User overrides | `skills/<pack>/SKILL.local.md` | the user | **never** (gitignored) |
| Secrets / tokens | `~/.config/...`, env vars | the user | **never** (outside the tree) |

`community_skills/` is currently **empty** (only its README). Skills graduate
there once the engine is extracted from user-specific config, there is no
personal data in `SKILL.md`, and it is generic enough to help other users.

## Current state

`iga-proactive` lives at **`skills/iga-proactive/`** and is fully working:
paths are wired, the engine runs, the research job is ported, the menu-bar app
builds and installs. It is the *install target*, not yet mirrored to the *OSS
template* location. This is correct for now — the live system is wired against
`skills/`.

### Already OSS-safe

- No secrets in the tree. The Todoist token is read from
  `~/.config/todoist/token` / `$TODOIST_API_TOKEN` (outside the repo).
- Personal `/gm` wiring lives in the **gitignored** `rules/commands.local.md`
  — it is not part of this skill and must **not** ship.
- `SKILL.md` carries no personal data; the research `proactive.yaml` is
  generic with an explicit "personalization goes in a `.local` override" note.
- Build artifacts (`app/.build/`, `app/Iga.app/`) and the runtime ledger /
  state file (`*.db`, `scratch/`) are gitignored.
- The repo declares `license = { text = "MIT" }` in `pyproject.toml`.
  **There is no top-level `LICENSE` file** — a maintainer must add one before
  going public (see "Maintainer checklist").

## Why the relocation is deferred (the decision)

To publish, the generic engine should be **mirrored** into
`community_skills/iga-proactive/` as the OSS template while `skills/` stays the
install target (the three-way-merge model needs both: BASE in
`community_skills/` at install commit, LOCAL in `skills/`).

Doing that mirror now would mean either (a) maintaining two copies by hand
until publish, which drifts, or (b) relocating the live system and rewriting
the hardcoded paths below — which risks breaking the working, wired
`/gm`-inline path and the menu-bar app's `ContractGuard` command for no
present benefit. The system is `status: stable` *as wired*. The restructure is
therefore deferred to publish time, when it can be done once, atomically, with
the migration map below. **This is an intentional decision, not an omission.**

## The relocation map (hardcoded paths a move must update)

If/when the skill is mirrored or relocated, exactly these source-level path
assumptions must be reviewed. (Docstrings/help-text mentions are cosmetic;
the load-bearing ones are marked **LOAD-BEARING**.)

### Engine (Python)

| File | Line(s) | Hardcoded assumption | Note |
|---|---|---|---|
| `engine/runtime.py` | `_SKILLS_DIR_DEFAULT = Path(__file__).resolve().parents[3] / "skills"` | **LOAD-BEARING.** Assumes `engine/` is exactly 3 levels under `<repo>/skills/iga-proactive/engine/`. A relocation that changes depth breaks job discovery. | Override at call time via `scan_tick(skills_dir=...)`. |
| `engine/runtime.py` | `skill_md = skills_dir / "iga-proactive" / "SKILL.md"` | **LOAD-BEARING.** `_read_engine_caps` reads *this* skill's own SKILL.md by the literal name `iga-proactive` to get `engine_config:` caps. Rename → caps silently fall back to defaults. | |
| `engine/ledger.py` | `~/Iga/state/proactive.db` default | Default db path. Overridable via `$IGA_PROACTIVE_DB`. | Cosmetic if adopters set the env var; otherwise assumes a `~/Iga` tree. |
| `engine/dispatcher.py` | `_DEFAULT_STATE_PATH = "~/Iga/scratch/proactive-state.json"` | Default state path. Overridable via `$IGA_PROACTIVE_STATE`. Relies on `scratch/` being gitignored to keep `git status` clean. | |
| `engine/__main__.py`, `engine/cli.py` | an absolute `cd <home>/Iga/skills/iga-proactive ...` example in the `-m` docstring / argparse epilog | Help-text example only. Not executed. | Cosmetic; genericize the absolute home path to `~/Iga/...` on publish. |

### App (Swift)

| File | Line(s) | Hardcoded assumption | Note |
|---|---|---|---|
| `app/Sources/IgaMenuBar/ContractGuard.swift` | `skillDir()` → `"\(home)/Iga/skills/iga-proactive"` and `documentedCommand`/`engineScanArgv` `cd ~/Iga/skills/iga-proactive` | **LOAD-BEARING.** The single sanctioned engine-exec command path. Relocation must update this *and* the matching string in `ContractLitmusTests` (the test asserts the exact command). | |
| `app/Sources/IgaMenuBar/LedgerReader.swift` | `"\(home)/Iga/state/proactive.db"` default | Ledger path; overridable via `$IGA_PROACTIVE_DB`. | |
| `app/Sources/IgaMenuBar/StateStore.swift` | `"\(home)/Iga/scratch/proactive-state.json"` default | State path; overridable via `$IGA_PROACTIVE_STATE`. | |
| `app/build.sh` | installs to `~/Applications/Iga.app` | Per-user install location; not repo-relative, so a skill relocation does not affect it. | |
| `app/Tests/IgaMenuBarTests/ContractLitmusTests.swift` | asserts the exact `documentedCommand` string | Must change in lockstep with `ContractGuard`. | |

Summary: the relocation surface is small and well isolated — two
**LOAD-BEARING** spots in `runtime.py`, one in `ContractGuard.swift` (with its
mirrored test assertion), and four env-overridable default paths. Everything
runtime-critical is already env-overridable; only job *discovery* depth and
the app's exec command are structurally pinned.

## Maintainer checklist before going public

- [ ] Add a top-level `LICENSE` file. `pyproject.toml` says MIT; **no
      `LICENSE` file exists at the repo root today** — this must be created.
- [ ] Mirror the generic engine + app + docs into
      `community_skills/iga-proactive/` (no personal data; this skill's
      `SKILL.md` and `proactive.yaml` are already generic — verify again).
- [ ] Apply the relocation map above if the OSS layout changes the depth or
      the skill directory name; update `ContractGuard.swift` **and** its test
      assertion in lockstep.
- [ ] Confirm `SKILL.local.md` / `rules/*.local.md` / `commands.local.md`
      are excluded (they are gitignored) and that the personal `/gm` wiring is
      not referenced by anything shipped.
- [ ] Decide whether a downstream packager signs/notarizes the app. The
      shipped `build.sh` stays signing-free by frozen decision.
- [ ] Re-run both test suites (engine: `uv run python -m pytest
      skills/iga-proactive/tests/ -q`; app: `swift test --enable-xctest`).
