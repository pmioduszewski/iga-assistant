"""Phase 4 — import a golden-seed.json into a (clean) MemPalace palace.

Files each seed fact as its own drawer (granular → each can be superseded
independently later) via the real add path (embeddings + indexing). Run with
the mempalace venv python:

    MEMPALACE_PALACE_PATH=<palace> <mempalace-venv>/bin/python \
        -m engine.import_seed <palace> <seed.final.json>

Generic: no user-specific data. Category→wing/room mapping is the day-0
onboarding default; override via CATEGORY_MAP if a deployment wants different
wings.
"""
import json
import os
import sys

# Day-0 onboarding placement. Semantic search is content-based, so these are
# sensible homes, not hard constraints.
CATEGORY_MAP = {
    "identity": ("user", "identity"),
    "family": ("people", "family"),
    "work_projects": ("projects", "general"),
    "tools_stack": ("user", "tooling"),
    "preferences": ("iga", "rules"),
    "health": ("user", "health"),
    "finance": ("user", "finance"),
    "schedule": ("user", "schedule"),
    "commitments": ("projects", "planning"),
    "abandoned": ("user", "general"),
}


def import_seed(palace_path: str, seed_path: str, marker: str = "golden-seed"):
    os.environ["MEMPALACE_PALACE_PATH"] = palace_path
    import mempalace.mcp_server as m  # imported after env is set so _config binds the palace

    seed = json.load(open(seed_path))
    added, dup, err = 0, 0, []
    for cat, entries in seed.get("categories", {}).items():
        wing, room = CATEGORY_MAP.get(cat, ("reference", "general"))
        for e in entries:
            fact = (e.get("fact") or "").strip()
            if not fact:
                continue
            # abandoned/superseded facts keep an explicit retirement marker in
            # the text so they never read as current.
            if e.get("status") == "abandoned" and "abandon" not in fact.lower():
                fact = "[ABANDONED/retired] " + fact
            res = m.tool_add_drawer(
                wing, room, fact,
                source_file=f"{marker}:{cat}",
                added_by="seed-import",
            )
            if res.get("success") is False:
                err.append((cat, fact[:60], res.get("error")))
            elif res.get("duplicate"):
                dup += 1
            else:
                added += 1
    return {"added": added, "duplicates": dup, "errors": err}


if __name__ == "__main__":
    pp = sys.argv[1]
    sp = sys.argv[2]
    out = import_seed(pp, sp)
    print(f"imported: {out['added']} added, {out['duplicates']} duplicate, "
          f"{len(out['errors'])} errors")
    for c, f, msg in out["errors"][:20]:
        print(f"  ERROR [{c}] {f!r}: {msg}")
