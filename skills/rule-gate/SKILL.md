---
name: rule-gate
description: Keeps corrections from piling up as memory rules. A PreToolUse hook denies a new drawer in a rule room and says where the content belongs instead.
triggers:
  - kind: hook
    spec: PreToolUse on mempalace_add_drawer in Claude Code
status: shipped
---

# Rule gate

When the user corrects Iga, the reflex is to file a rule so it "never happens
again". It does not work. The rules pile up into the hundreds, the same
lesson gets filed again and again and is still broken the same day, and each
new drawer makes every search noisier and the few rules that matter harder
to find.

A correction is repaired by doing the skipped thing in the same reply and
reporting what it showed (`CLAUDE.md`, "How Iga works"). This hook is the
mechanical backstop for that: `engine/gate.py` denies `mempalace_add_drawer`
into a rule room (`iga/rules`, or the `rules`, `behavior` and `corrections`
rooms of the `iga` wing) and names the right home:

- a fact about the user's world goes in its topic wing, updating the existing
  drawer when there is one;
- a refinement of an existing rule updates that drawer;
- a lapse gets one line in `state/corrections.md` (date, what was missed, what
  was done instead). That file is not loaded into context. Three lines on the
  same lapse mean it needs a mechanical guard, which is worth telling the
  user once.

A rule the user asked for in their own words still gets filed: start the
drawer content with `USER ASKED:` followed by their words.

## Setup

```sh
python3 skills/rule-gate/engine/install.py --dry-run
python3 skills/rule-gate/engine/install.py        # --uninstall removes it
```

The installer adds one `PreToolUse` handler (matcher
`mcp__.*__mempalace_add_drawer`) to this clone's `.claude/settings.local.json`
and preserves everything else. Restart Claude Code afterwards. Claude Code
only: the deny contract has not been verified for other hosts, so they rely
on the `CLAUDE.md` guidance alone.

| Variable | Default | Meaning |
|---|---|---|
| `IGA_RULE_GATE` | `1` | `0` turns the gate off |
| `IGA_RULE_ROOMS` | `rules,behavior,behaviour,corrections` | rooms of the `iga` wing treated as rule rooms |

No model call and no network. Any error fails open. Existing drawers are not
touched: cleaning up an existing pile is a separate, deliberate job.
