# community_skills/

Upstream source for installable Iga **skills** (capabilities Iga performs).
Mirror of `community_rules/`, which holds installable **rules** (tool/behavior preferences).

See the "Skills vs Rules — the architecture" section of `CLAUDE.md` for the conceptual split. Short version:

- A **skill** is something Iga *does* (an engine, a workflow, an automation). It lives as a directory.
- A **rule** is a preference for *how* Iga uses a tool or behaves in a context. It lives as a single `.md` file.

## Layout

Each installable skill is a subdirectory:

```
community_skills/<name>/
  SKILL.md              ← LLM instructions + frontmatter (mandatory)
  engine/               ← scripts/binaries (optional)
  tests/                ← unit tests (optional)
  docs/                 ← setup guides (optional)
  README.md             ← top-level human pointer (optional)
```

`SKILL.local.md` is **never** shipped here — that file is user-private by definition (gitignored, lives in the installed copy at `skills/<name>/SKILL.local.md`).

## Installing

```bash
/gaia install <skill-name>
```

The installer copies `community_skills/<name>/` to `skills/<name>/` and stamps provenance frontmatter (`source`, `source_commit`, `installed_at`) so `/gaia update <skill-name>` can three-way-merge upstream improvements while preserving the user's `SKILL.local.md` customizations.

## Status

This directory is **empty for now**. Skills graduate here as they become OSS-ready (engine extracted from user-specific config, no personal data in `SKILL.md`, generic enough to be useful to other users).

For the meta-process of authoring a new skill — including the OSS publication path — see [`skills/create-iga-skill/SKILL.md`](../skills/create-iga-skill/SKILL.md).
