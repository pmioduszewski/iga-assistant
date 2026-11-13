#!/usr/bin/env python3
"""Rule gate: a PreToolUse hook that keeps corrections from piling up as rules.

When the user corrects the assistant, the reflex is to file a new "rule" in
memory. A few hundred of those later the rules that matter are buried, every
search returns three near-copies, and behaviour is worse, not better. A
correction is repaired by doing the skipped thing, not by a new rule.

So a new drawer in a rule room is denied, with where the content belongs
instead. A rule the user asked for in their own words passes: start the
content with "USER ASKED:" followed by their words.

No model call, no network. Fails open: any error prints nothing and exits 0.
Off: IGA_RULE_GATE=0.
"""
from __future__ import annotations

import json
import os
import sys

BYPASS = "USER ASKED:"
CORRECTIONS_LOG = "state/corrections.md"
DEFAULT_ROOMS = "rules,behavior,behaviour,corrections"
OPEN_ROOMS = {"tooling"}  # how a tool works is a fact, not a behaviour rule


def _off() -> bool:
    return os.environ.get("IGA_RULE_GATE", "1").strip().lower() in ("0", "false", "off", "no")


def rule_rooms() -> set[str]:
    raw = os.environ.get("IGA_RULE_ROOMS") or DEFAULT_ROOMS
    return {room.strip().lower() for room in raw.split(",") if room.strip()}


def is_rule_room(wing: str, room: str) -> bool:
    """`iga/rules` (any room but tooling), or a rule room of the `iga` wing."""
    wing = wing.strip().lower().replace("/", "_")
    room = room.strip().lower()
    if room in OPEN_ROOMS:
        return False
    return wing.startswith("iga_rules") or (wing == "iga" and room in rule_rooms())


def process_event(payload: object) -> dict:
    if _off() or not isinstance(payload, dict):
        return {}
    if not str(payload.get("tool_name") or "").endswith("mempalace_add_drawer"):
        return {}
    args = payload.get("tool_input")
    if not isinstance(args, dict):
        return {}
    content = str(args.get("content") or "").strip()
    if not is_rule_room(str(args.get("wing") or ""), str(args.get("room") or "")):
        return {}
    if content.upper().startswith(BYPASS):
        return {}
    reason = (
        "RULE GATE: new rule drawers are closed by default, because a pile of corrections buries the "
        "rules that matter. Put the content where it belongs: "
        "(1) a fact about the user's world (a person, a bill, a project, a tool) goes in that topic's "
        "wing, updating the existing drawer when there is one; "
        "(2) a refinement of an existing rule: mempalace_update_drawer on that drawer; "
        f"(3) a lapse in your own behaviour: do the skipped check now, append one line to {CORRECTIONS_LOG} "
        "(date, what was missed, what you did instead) and file nothing. "
        f"Only when the user asked for a new rule in their own words: retry with the content starting "
        f"'{BYPASS}' followed by their words. Do not tell the user a rule was saved."
    )
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def main() -> int:
    try:
        output = process_event(json.load(sys.stdin))
        if output:
            json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
            sys.stdout.write("\n")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
