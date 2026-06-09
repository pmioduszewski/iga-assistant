---
description: Iga — personal AI assistant command router
argument-hint: <status|rules|install|uninstall|check-updates|diff|update|help|command>
---

Route the Iga command based on $ARGUMENTS.

## Priority 1 — Built-in admin commands

Match $ARGUMENTS against these first (case-insensitive):

- **(empty) or `help`** — Print available admin commands and any user-defined commands found in `rules/commands.md`. Format as a clean list with short descriptions.

- **`status`** — System health check. Thin generic layer; specifics live in each rule pack / skill via frontmatter. Run in this order:
  1. **MemPalace status** — `mempalace_status` (drawer count, palace_path, wing breakdown)
  2. **Connected MCPs** — `claude mcp list` (show ✓/✗ per server). Note: cloud-injected claude.ai MCPs may not appear in this listing even when active; cross-reference with the deferred-tool list if needed.
  3. **Installed rules** — files in `rules/*.md` (skip `.iga.yml` and dotfiles). For each, show provenance frontmatter if present (`source`, `source_commit`, `installed_at`); otherwise mark "local-only".
  4. **Installed skills** — directories under `skills/*/` with `SKILL.md`. Show `name` + `status` from frontmatter.
  5. **Available community packs** — files in `community_rules/` AND directories in `community_skills/` that are NOT yet installed in `rules/` or `skills/`. Filter by name match.
  6. **Prerequisite scan** — read frontmatter of every `rules/*.md` AND every `skills/*/SKILL.md`. For each `prerequisites:` entry, evaluate its `check:` clause (see CLAUDE.md "Check clause DSL"). Surface unsatisfied prereqs, one line per item: `⚠️ <pack-name>: <prereq-name> — <description> — guide: <guide-path-if-any>`. Group by severity: `error` (block) → `warning` → `info`. After listing, if any unsatisfied prereqs have a `guide:` field, use `AskUserQuestion` to ask: *"Want me to walk you through fixing the missing prerequisite(s) now?"* — on yes, step through each guide interactively (read the guide file, do safe file writes / commands automatically, confirm any sudo step before running).
  7. **Time-awareness hook** — verify `~/.claude/settings.json` AND `.claude/settings.local.json` for a `UserPromptSubmit` hook whose command starts with `date`. If neither has it, flag as a missing prerequisite — Iga needs accurate wall-clock time for calendar/scheduling work.
  8. **Update check** — for each installed pack with `source_commit` frontmatter, do `gh api repos/<source>/commits?path=<source_path>&per_page=1` to fetch upstream HEAD. If HEAD `sha[:7]` ≠ `source_commit`, report `N packs have updates available (run /iga check-updates for details)`. If `gh` is not on PATH, fall back to `git ls-remote` or `WebFetch` against `https://raw.githubusercontent.com/<source>/<branch>/<source_path>`. Do NOT skip this step unless an actual error occurs — name the error if so.
  9. **Other flags** — broken MCP, empty palace, missing hooks, anything else the scan surfaces.

- **`rules`** — List all installed rule packs (`rules/` dir, excluding `.iga.yml` and dotfiles) and skill bundles (`skills/*/` dirs with `SKILL.md`), plus available community packs (`community_rules/`) and skill bundles (`community_skills/*/`). Show name and first-line/`description` summary of each.

  **`<pack>` for the commands below resolves to either a single-file rule pack (`community_rules/<pack>.md` → `rules/<pack>.md`) or a directory skill bundle (`community_skills/<pack>/` → `skills/<pack>/`). Resolution order: rule pack first, then skill bundle. Each command handles both forms.**

- **`install <pack>`** — Install a community rule pack or skill bundle:
  1. Resolve upstream from `rules/.iga.yml` (key: `upstream`, default `pmioduszewski/iga-assistant`, branch from `upstream_branch` defaulting to `main`)
  2. Resolve `<pack>` locally: look for `community_rules/<pack>.md` (**rule pack**) first; else `community_skills/<pack>/` (**skill bundle**, a directory containing `SKILL.md`)
  3. If not found locally, fetch from `https://raw.githubusercontent.com/<upstream>/<branch>/community_rules/<pack>.md`; if that 404s, try the skill bundle at `community_skills/<pack>/SKILL.md` (and fetch the whole directory tree if present)
  4. If found: show the user what it contains, ask for confirmation
  5. On confirmation:
     - **rule pack** → copy to `rules/<pack>.md`
     - **skill bundle** → recursively copy the directory tree to `skills/<pack>/`
     AND **stamp frontmatter** with provenance — into `rules/<pack>.md` for a rule pack, or into the installed `skills/<pack>/SKILL.md` **only** (never other bundle files) for a skill bundle:
     ```yaml
     ---
     source: <upstream>
     source_path: community_rules/<pack>.md   # or community_skills/<pack>/ for a bundle
     source_commit: <short-sha-of-current-HEAD-for-that-path>
     installed_at: <today YYYY-MM-DD>
     ---
     ```
     Get the commit SHA via `gh api repos/<upstream>/commits?path=<source_path>&per_page=1` (extract `sha[:7]`). If `gh` unavailable, fall back to `git ls-remote https://github.com/<upstream> <branch>` for branch HEAD (less precise, file-level not exact, but sufficient).
     If the source already has frontmatter, prepend the provenance block above the existing one. If local-only (no upstream), set `source: local` and skip commit lookup.
     **Never overwrite an existing `skills/<pack>/SKILL.local.md`** — it is the user's private layer.
  6. If not found anywhere: tell the user

- **`uninstall <pack>`** — Remove an installed rule pack or skill bundle:
  1. Resolve: `rules/<pack>.md` (rule pack) or `skills/<pack>/` (skill bundle)
  2. Ask for confirmation
  3. On confirmation:
     - rule pack → delete `rules/<pack>.md`
     - skill bundle → `rm -rf skills/<pack>/`, but **preserve `skills/<pack>/SKILL.local.md`** (move it aside, delete the rest, restore it — or skip it from deletion). Then **warn the user**: any optional companion artifact (e.g. a macOS app, login item, scheduler) is NOT removed by this and must be uninstalled separately per that bundle's own docs (e.g. `skills/<pack>/app/README.md`) — deleting the directory does not unregister OS-level state.

- **`check-updates`** — Detect which installed packs/bundles have upstream updates available:
  1. Read all provenance-bearing items: files in `rules/` (skip `.iga.yml`, `commands.md`) AND `skills/*/SKILL.md`, skipping anything without a frontmatter `source_commit`
  2. For each: extract `source`, `source_path`, `source_commit` from frontmatter
  3. Fetch upstream HEAD commit for that path: `gh api repos/<source>/commits?path=<source_path>&per_page=1`
  4. Compare: if HEAD `sha[:7]` ≠ `source_commit`, count commits between via `gh api repos/<source>/compare/<source_commit>...<head_sha>` and report
  5. Print a clean table:
     ```
     pack             installed   upstream    status
     notion.md        5799294     abc1234     3 commits behind
     daily_commands   62a9dd9     62a9dd9     up to date
     iga-proactive/   0466f15     0466f15     up to date
     ```
  6. If any have updates, suggest `/iga diff <pack>` and `/iga update <pack>`
  7. Read-only — no file mutation

- **`diff <pack>`** — Three-way diff for an installed pack or bundle:
  > ⚠️ Before running, print this warning verbatim: "Note: For `/iga update`, you should be running Opus 4.6 (medium effort) or stronger. Three-way merges of behavioral rules are non-trivial and weaker models may produce subtly wrong merges that silently break Iga's behavior. `/iga diff` is read-only and safe on any model, but the merge step is not."
  1. **LOCAL** = the user's current installed version (`rules/<pack>.md` for a rule pack; the `skills/<pack>/` tree for a skill bundle, possibly customized)
  2. Read provenance `source_commit` → fetch upstream **at that commit**: `https://raw.githubusercontent.com/<source>/<source_commit>/<source_path>` → **BASE** (for a bundle, fetch the directory tree at that commit)
  3. Fetch upstream at HEAD: `https://raw.githubusercontent.com/<source>/<branch>/<source_path>` → **UPSTREAM** (bundle: the tree at HEAD)
  4. Show the diffs (for a skill bundle, **per-file across the directory tree**, including added/removed files):
     - **Your customizations** (BASE → LOCAL): what you changed since installing
     - **Upstream changes** (BASE → UPSTREAM): what maintainers added
     - **Potential conflicts**: regions both sides changed
  5. End with: "Run `/iga update <pack>` to merge upstream changes while preserving your customizations."

- **`update <pack>`** — Interactive LLM-assisted merge:
  > ⚠️ **Model warning — print this verbatim before doing any merge work, on every invocation:**
  > "This command performs an LLM-assisted three-way merge of behavioral rules. **Strongly recommended:** Opus 4.6 (medium effort) or stronger. Weaker models (Sonnet without thinking, Haiku) can produce subtly wrong merges that silently break Iga's behavior — for example, dropping a behavioral hook, mis-resolving a conflict, or merging in upstream wording that contradicts a user customization. If you're not sure what model you're on, run `/model` to check, or skip the update and run `/iga diff <pack>` to review changes manually instead."
  > After printing the warning, ask the user to confirm they want to proceed before fetching anything.
  1. Run the same three-way fetch as `diff` (BASE, LOCAL, UPSTREAM)
  2. Generate a **proposed merged version** that:
     - Keeps user's customizations from LOCAL (lines/sections changed in BASE→LOCAL but not BASE→UPSTREAM)
     - Applies upstream improvements from UPSTREAM (changed in BASE→UPSTREAM but not BASE→LOCAL)
     - Flags conflicts inline using `<<<<<<< local` / `======= upstream` markers when both sides changed the same region — let the user resolve those manually
     - For a **skill bundle**, do this **per-file across the bundle** (not on a single `.md`): merge each file independently, carry over added files from UPSTREAM, and never touch `skills/<pack>/SKILL.local.md`
  3. Display the proposed merge to the user with a summary:
     - "Preserved N user customizations: [list]"
     - "Applied M upstream improvements: [list]"
     - "K conflicts requiring manual resolution: [list]"
  4. Ask for confirmation. Do NOT write until user approves.
  5. On approval:
     - Write the merged content to `rules/<pack>.md` (rule pack) or back across the `skills/<pack>/` tree (skill bundle)
     - Update frontmatter `source_commit` to new HEAD sha and bump `installed_at` (in `skills/<pack>/SKILL.md` for a bundle)
     - Confirm the path(s) written
  6. On rejection: discard, leave files untouched.

## Priority 2 — User-defined commands (fallback)

If $ARGUMENTS does not match any admin command above:

1. Read `rules/commands.md` — look for a section matching `## /$ARGUMENTS` (e.g., `## /gm`)
2. If found: follow the steps defined there
3. If not found: check CLAUDE.md for a default definition of that command under "Iga Commands"
4. If still not found: tell the user this command is not defined and suggest `iga help`

When executing a user-defined command, also check for any `rules/<tool>.md` files that might apply (e.g., `rules/todoist.md` for task-related commands).

## Configuration: `rules/.iga.yml`

Optional config file. If present, controls where `install` / `check-updates` fetch from. Defaults are used when missing.

```yaml
# rules/.iga.yml
upstream: pmioduszewski/iga-assistant   # default
upstream_branch: main                     # default

# Per-pack overrides (optional) — useful when mixing packs from different sources
overrides:
  notion: yourname/your-notion-extras
  jira: company-internal/iga-rules
```

Per-pack `source` in frontmatter takes precedence over `overrides`, which takes precedence over the global `upstream`.
