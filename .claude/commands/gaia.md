---
description: Gaia — personal AI assistant command router
argument-hint: <status|rules|install|uninstall|check-updates|diff|update|help|command>
---

Route the Gaia command based on $ARGUMENTS.

## Priority 1 — Built-in admin commands

Match $ARGUMENTS against these first (case-insensitive):

- **(empty) or `help`** — Print available admin commands and any user-defined commands found in `rules/commands.md`. Format as a clean list with short descriptions.

- **`status`** — System health check. Thin generic layer; specifics live in each rule pack / skill via frontmatter. Run in this order:
  1. **MemPalace status** — `mempalace_status` (drawer count, palace_path, wing breakdown)
  2. **Connected MCPs** — `claude mcp list` (show ✓/✗ per server). Note: cloud-injected claude.ai MCPs may not appear in this listing even when active; cross-reference with the deferred-tool list if needed.
  3. **Installed rules** — files in `rules/*.md` (skip `.gaia.yml` and dotfiles). For each, show provenance frontmatter if present (`source`, `source_commit`, `installed_at`); otherwise mark "local-only".
  4. **Installed skills** — directories under `skills/*/` with `SKILL.md`. Show `name` + `status` from frontmatter.
  5. **Available community packs** — files in `community_rules/` AND directories in `community_skills/` that are NOT yet installed in `rules/` or `skills/`. Filter by name match.
  6. **Prerequisite scan** — read frontmatter of every `rules/*.md` AND every `skills/*/SKILL.md`. For each `prerequisites:` entry, evaluate its `check:` clause (see CLAUDE.md "Check clause DSL"). Surface unsatisfied prereqs, one line per item: `⚠️ <pack-name>: <prereq-name> — <description> — guide: <guide-path-if-any>`. Group by severity: `error` (block) → `warning` → `info`. After listing, if any unsatisfied prereqs have a `guide:` field, use `AskUserQuestion` to ask: *"Want me to walk you through fixing the missing prerequisite(s) now?"* — on yes, step through each guide interactively (read the guide file, do safe file writes / commands automatically, confirm any sudo step before running).
  7. **Time-awareness hook** — verify `~/.claude/settings.json` AND `.claude/settings.local.json` for a `UserPromptSubmit` hook whose command starts with `date`. If neither has it, flag as a missing prerequisite — Gaia needs accurate wall-clock time for calendar/scheduling work.
  8. **Update check** — for each installed pack with `source_commit` frontmatter, do `gh api repos/<source>/commits?path=<source_path>&per_page=1` to fetch upstream HEAD. If HEAD `sha[:7]` ≠ `source_commit`, report `N packs have updates available (run /gaia check-updates for details)`. If `gh` is not on PATH, fall back to `git ls-remote` or `WebFetch` against `https://raw.githubusercontent.com/<source>/<branch>/<source_path>`. Do NOT skip this step unless an actual error occurs — name the error if so.
  9. **Other flags** — broken MCP, empty palace, missing hooks, anything else the scan surfaces.

- **`rules`** — List all installed rules (`rules/` dir, excluding `.gaia.yml` and dotfiles) and available community packs (`community_rules/` dir). Show filename and first-line summary of each.

- **`install <pack>`** — Install a community rule pack:
  1. Resolve upstream from `rules/.gaia.yml` (key: `upstream`, default `pmioduszewski/iga-assistant`, branch from `upstream_branch` defaulting to `main`)
  2. Look for `community_rules/<pack>.md` locally first
  3. If not found locally, fetch from `https://raw.githubusercontent.com/<upstream>/<branch>/community_rules/<pack>.md`
  4. If found: show the user what it contains, ask for confirmation
  5. On confirmation: copy to `rules/<pack>.md` AND **stamp frontmatter** with provenance:
     ```yaml
     ---
     source: <upstream>
     source_path: community_rules/<pack>.md
     source_commit: <short-sha-of-current-HEAD-for-that-file>
     installed_at: <today YYYY-MM-DD>
     ---
     ```
     Get the commit SHA via `gh api repos/<upstream>/commits?path=community_rules/<pack>.md&per_page=1` (extract `sha[:7]`). If `gh` unavailable, fall back to `git ls-remote https://github.com/<upstream> <branch>` for branch HEAD (less precise, file-level not exact, but sufficient).
     If the source pack already has frontmatter, prepend the provenance block above the existing one. If the pack is local-only (no upstream), set `source: local` and skip commit lookup.
  6. If not found anywhere: tell the user

- **`uninstall <pack>`** — Remove an installed rule pack:
  1. Check if `rules/<pack>.md` exists
  2. Ask for confirmation
  3. Delete on confirmation

- **`check-updates`** — Detect which installed packs have upstream updates available:
  1. Read all files in `rules/` (skip `.gaia.yml`, `commands.md`, anything without a frontmatter `source_commit`)
  2. For each pack: extract `source`, `source_path`, `source_commit` from frontmatter
  3. Fetch upstream HEAD commit for that path: `gh api repos/<source>/commits?path=<source_path>&per_page=1`
  4. Compare: if HEAD `sha[:7]` ≠ `source_commit`, count commits between via `gh api repos/<source>/compare/<source_commit>...<head_sha>` and report
  5. Print a clean table:
     ```
     pack            installed   upstream    status
     notion.md       5799294     abc1234     3 commits behind
     daily_commands  62a9dd9     62a9dd9     up to date
     ```
  6. If any have updates, suggest `/gaia diff <pack>` and `/gaia update <pack>`
  7. Read-only — no file mutation

- **`diff <pack>`** — Three-way diff for an installed pack:
  > ⚠️ Before running, print this warning verbatim: "Note: For `/gaia update`, you should be running Opus 4.6 (medium effort) or stronger. Three-way merges of behavioral rules are non-trivial and weaker models may produce subtly wrong merges that silently break Gaia's behavior. `/gaia diff` is read-only and safe on any model, but the merge step is not."
  1. Read `rules/<pack>.md` → call this **LOCAL** (user's current version, possibly customized)
  2. Read its frontmatter `source_commit` → fetch upstream content **at that commit**: `https://raw.githubusercontent.com/<source>/<source_commit>/<source_path>` → call this **BASE**
  3. Fetch upstream content at HEAD: `https://raw.githubusercontent.com/<source>/<branch>/<source_path>` → call this **UPSTREAM**
  4. Show two diffs side-by-side or sequentially:
     - **Your customizations** (BASE → LOCAL): what you changed since installing
     - **Upstream changes** (BASE → UPSTREAM): what maintainers added
     - **Potential conflicts**: lines where BOTH sides changed the same region
  5. End with: "Run `/gaia update <pack>` to merge upstream changes while preserving your customizations."

- **`update <pack>`** — Interactive LLM-assisted merge:
  > ⚠️ **Model warning — print this verbatim before doing any merge work, on every invocation:**
  > "This command performs an LLM-assisted three-way merge of behavioral rules. **Strongly recommended:** Opus 4.6 (medium effort) or stronger. Weaker models (Sonnet without thinking, Haiku) can produce subtly wrong merges that silently break Gaia's behavior — for example, dropping a behavioral hook, mis-resolving a conflict, or merging in upstream wording that contradicts a user customization. If you're not sure what model you're on, run `/model` to check, or skip the update and run `/gaia diff <pack>` to review changes manually instead."
  > After printing the warning, ask the user to confirm they want to proceed before fetching anything.
  1. Run the same three-way fetch as `diff` (BASE, LOCAL, UPSTREAM)
  2. Generate a **proposed merged version** that:
     - Keeps user's customizations from LOCAL (lines/sections changed in BASE→LOCAL but not BASE→UPSTREAM)
     - Applies upstream improvements from UPSTREAM (changed in BASE→UPSTREAM but not BASE→LOCAL)
     - Flags conflicts inline using `<<<<<<< local` / `======= upstream` markers when both sides changed the same region — let the user resolve those manually
  3. Display the proposed merge to the user with a summary:
     - "Preserved N user customizations: [list]"
     - "Applied M upstream improvements: [list]"
     - "K conflicts requiring manual resolution: [list]"
  4. Ask for confirmation. Do NOT write the file until user approves.
  5. On approval:
     - Write the merged content to `rules/<pack>.md`
     - Update frontmatter `source_commit` to new HEAD sha and bump `installed_at`
     - Confirm the path written
  6. On rejection: discard, leave file untouched.

## Priority 2 — User-defined commands (fallback)

If $ARGUMENTS does not match any admin command above:

1. Read `rules/commands.md` — look for a section matching `## /$ARGUMENTS` (e.g., `## /gm`)
2. If found: follow the steps defined there
3. If not found: check CLAUDE.md for a default definition of that command under "Gaia Commands"
4. If still not found: tell the user this command is not defined and suggest `gaia help`

When executing a user-defined command, also check for any `rules/<tool>.md` files that might apply (e.g., `rules/todoist.md` for task-related commands).

## Configuration: `rules/.gaia.yml`

Optional config file. If present, controls where `install` / `check-updates` fetch from. Defaults are used when missing.

```yaml
# rules/.gaia.yml
upstream: pmioduszewski/iga-assistant   # default
upstream_branch: main                     # default

# Per-pack overrides (optional) — useful when mixing packs from different sources
overrides:
  notion: yourname/your-notion-extras
  jira: company-internal/gaia-rules
```

Per-pack `source` in frontmatter takes precedence over `overrides`, which takes precedence over the global `upstream`.
