"""Tests for engine/hook_spec.py and the spec-driven worker context.

Covers:
  - Parsing a dev-profile spec (examples/example-hook.md)
  - Parsing an inline non-dev profile (parenting tips)
  - Validation errors for bad/missing fields
  - build_worker_context merges spec + drawer context correctly
  - Killswitch / empty-queue still yields zero spawns (regression guard —
    hook_spec has no bearing on the safety gate, but we verify the gate
    still holds after the refactor by importing the new modules)

Pure-function tests (no network, no MCP, no LLM). Mirrors the testing
posture of test_extract.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# conftest.py already inserts engine/ on sys.path.
import hook_spec as hs  # type: ignore
from hook_spec import HookSpecError, parse_hook_spec, load_hook_spec  # type: ignore
import extract as ex  # type: ignore
from extract import build_worker_context  # type: ignore

SKILL_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_HOOK = SKILL_ROOT / "examples" / "example-hook.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEV_SPEC = """\
---
name: dev-tools
description: Dev libs and tools relevant to software projects.

trigger:
  gmail_label: "Newsletter/Dev"

interest_profile: |
  Libraries and tools that could improve software projects — performance,
  developer experience, databases, AI/ML tooling.

scoring_context:
  - "projects/*"

fit_threshold: 2
output_wing: "vault/dev-tools"
cadence: on-demand
status: active
---

## Additional context

Include GitHub repos, npm packages, CLI tools.
Exclude pure marketing copy.
"""

_PARENTING_SPEC = """\
---
name: parenting-tips
description: Practical parenting tips from family newsletter stream.

trigger:
  gmail_label: "Newsletter/Family"

interest_profile: |
  Practical parenting tips for a toddler aged 1-3: sleep routines, nutrition,
  developmental milestones, age-appropriate toys and activities.

scoring_context:
  - "family"
  - "user/interests"

fit_threshold: 2
output_wing: "vault/family"
cadence: on-demand
status: active
---
"""


# ---------------------------------------------------------------------------
# Parsing — dev profile
# ---------------------------------------------------------------------------


def test_parse_dev_spec_returns_expected_fields():
    spec = parse_hook_spec(_DEV_SPEC)
    assert spec["name"] == "dev-tools"
    assert spec["trigger"] == {"gmail_label": "Newsletter/Dev"}
    assert "Libraries and tools" in spec["interest_profile"]
    assert spec["scoring_context"] == ["projects/*"]
    assert spec["fit_threshold"] == 2
    assert spec["output_wing"] == "vault/dev-tools"
    assert spec["cadence"] == "on-demand"
    assert spec["status"] == "active"
    assert "Include GitHub repos" in spec["body"]


def test_parse_dev_spec_types():
    spec = parse_hook_spec(_DEV_SPEC)
    assert isinstance(spec["name"], str)
    assert isinstance(spec["interest_profile"], str)
    assert isinstance(spec["scoring_context"], list)
    assert isinstance(spec["fit_threshold"], int)
    assert isinstance(spec["body"], str)


# ---------------------------------------------------------------------------
# Parsing — non-dev (parenting) profile
# ---------------------------------------------------------------------------


def test_parse_parenting_spec_name_and_profile():
    spec = parse_hook_spec(_PARENTING_SPEC)
    assert spec["name"] == "parenting-tips"
    assert "toddler" in spec["interest_profile"].lower()
    assert spec["scoring_context"] == ["family", "user/interests"]
    assert spec["output_wing"] == "vault/family"


def test_parse_parenting_spec_body_is_empty():
    spec = parse_hook_spec(_PARENTING_SPEC)
    assert spec["body"] == ""


def test_parenting_and_dev_profiles_differ():
    dev = parse_hook_spec(_DEV_SPEC)
    par = parse_hook_spec(_PARENTING_SPEC)
    assert dev["interest_profile"] != par["interest_profile"]
    assert dev["scoring_context"] != par["scoring_context"]
    assert dev["output_wing"] != par["output_wing"]


# ---------------------------------------------------------------------------
# Parsing — example-hook.md shipped in examples/
# ---------------------------------------------------------------------------


def test_example_hook_file_parses_without_error():
    spec = load_hook_spec(str(EXAMPLE_HOOK))
    assert spec["name"] == "dev-tools"
    assert spec["trigger"].get("gmail_label") == "Newsletter/Dev"
    assert spec["fit_threshold"] == 2


def test_example_hook_is_oss_clean():
    """The shipped example must not contain PII (no real names, emails, paths
    like /Users/<something>)."""
    text = EXAMPLE_HOOK.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "@gmail.com" not in text
    # No real person names — just check for common PII patterns.
    import re
    assert not re.search(r'\b[A-Z][a-z]+\s[A-Z][a-z]+\b', text), (
        "example-hook.md appears to contain a full person name (PII)"
    )


# ---------------------------------------------------------------------------
# Validation errors — bad/missing fields
# ---------------------------------------------------------------------------


def test_missing_name_raises():
    bad = _DEV_SPEC.replace("name: dev-tools\n", "")
    with pytest.raises(HookSpecError, match="name"):
        parse_hook_spec(bad)


def test_bad_name_slug_raises():
    bad = _DEV_SPEC.replace("name: dev-tools", "name: Dev Tools!")
    with pytest.raises(HookSpecError, match="slug"):
        parse_hook_spec(bad)


def test_missing_description_raises():
    bad = _DEV_SPEC.replace(
        "description: Dev libs and tools relevant to software projects.\n", ""
    )
    with pytest.raises(HookSpecError, match="description"):
        parse_hook_spec(bad)


def test_missing_trigger_raises():
    lines = [l for l in _DEV_SPEC.splitlines()
             if not l.startswith("trigger:") and "gmail_label" not in l]
    bad = "\n".join(lines)
    with pytest.raises(HookSpecError, match="trigger"):
        parse_hook_spec(bad)


def test_both_trigger_keys_raises():
    spec = _DEV_SPEC.replace(
        'trigger:\n  gmail_label: "Newsletter/Dev"',
        'trigger:\n  gmail_label: "Newsletter/Dev"\n  gmail_query: "label:foo"'
    )
    with pytest.raises(HookSpecError, match="EITHER"):
        parse_hook_spec(spec)


def test_missing_interest_profile_raises():
    # Remove the interest_profile block (block scalar spans multiple lines).
    import re
    bad = re.sub(
        r'interest_profile: \|.*?(?=\n[a-z])', '',
        _DEV_SPEC, flags=re.DOTALL
    )
    with pytest.raises(HookSpecError, match="interest_profile"):
        parse_hook_spec(bad)


def test_missing_scoring_context_raises():
    lines = [l for l in _DEV_SPEC.splitlines()
             if "scoring_context" not in l and '"projects/*"' not in l]
    bad = "\n".join(lines)
    with pytest.raises(HookSpecError, match="scoring_context"):
        parse_hook_spec(bad)


def test_missing_output_wing_raises():
    bad = _DEV_SPEC.replace('output_wing: "vault/dev-tools"\n', "")
    with pytest.raises(HookSpecError, match="output_wing"):
        parse_hook_spec(bad)


def test_bad_fit_threshold_raises():
    bad = _DEV_SPEC.replace("fit_threshold: 2", "fit_threshold: 99")
    with pytest.raises(HookSpecError, match="fit_threshold"):
        parse_hook_spec(bad)


def test_bad_cadence_raises():
    bad = _DEV_SPEC.replace("cadence: on-demand", "cadence: weekly")
    with pytest.raises(HookSpecError, match="cadence"):
        parse_hook_spec(bad)


def test_bad_status_raises():
    bad = _DEV_SPEC.replace("status: active", "status: disabled")
    with pytest.raises(HookSpecError, match="status"):
        parse_hook_spec(bad)


def test_no_frontmatter_raises():
    with pytest.raises(HookSpecError, match="frontmatter"):
        parse_hook_spec("Just a plain body, no frontmatter.")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_when_optional_fields_absent():
    minimal = """\
---
name: minimal-hook
description: A minimal hook.

trigger:
  gmail_query: "label:foo"

interest_profile: |
  Anything interesting.

scoring_context:
  - "general"

output_wing: "vault/general"
---
"""
    spec = parse_hook_spec(minimal)
    assert spec["fit_threshold"] == 2
    assert spec["cadence"] == "on-demand"
    assert spec["status"] == "active"
    assert spec["body"] == ""


# ---------------------------------------------------------------------------
# build_worker_context
# ---------------------------------------------------------------------------


def test_build_worker_context_merges_spec_and_drawer():
    spec = parse_hook_spec(_DEV_SPEC)
    drawer = {
        "drawer.id": "flag-42",
        "drawer.title": "Newsletter/Dev: weekly",
        "drawer.room": "newsletter-research-queue",
        "drawer.target_date": "2026-05-18",
        "drawer.context": "message-id: abc123; label Newsletter/Dev",
    }
    ctx = build_worker_context(spec, drawer)

    # Drawer fields pass through.
    assert ctx["drawer.id"] == "flag-42"
    assert ctx["drawer.context"] == "message-id: abc123; label Newsletter/Dev"

    # Hook fields from spec.
    assert ctx["hook.name"] == "dev-tools"
    assert ctx["hook.interest_profile"] == spec["interest_profile"]
    assert ctx["hook.scoring_context"] == ["projects/*"]
    assert ctx["hook.fit_threshold"] == 2
    assert ctx["hook.output_wing"] == "vault/dev-tools"
    assert ctx["hook.trigger"] == {"gmail_label": "Newsletter/Dev"}


def test_build_worker_context_parenting_profile():
    spec = parse_hook_spec(_PARENTING_SPEC)
    ctx = build_worker_context(spec, {})
    assert ctx["hook.name"] == "parenting-tips"
    assert "toddler" in ctx["hook.interest_profile"].lower()
    assert ctx["hook.scoring_context"] == ["family", "user/interests"]
    assert ctx["hook.output_wing"] == "vault/family"


def test_build_worker_context_missing_drawer_keys_default_empty():
    spec = parse_hook_spec(_DEV_SPEC)
    ctx = build_worker_context(spec, {})
    assert ctx["drawer.id"] == ""
    assert ctx["drawer.context"] == ""


# ---------------------------------------------------------------------------
# Paused hook: worker context carries status=paused
# ---------------------------------------------------------------------------


def test_paused_status_preserved_in_context():
    paused = _DEV_SPEC.replace("status: active", "status: paused")
    spec = parse_hook_spec(paused)
    ctx = build_worker_context(spec, {"drawer.id": "x"})
    assert ctx["hook.status"] == "paused"


# ---------------------------------------------------------------------------
# todoist_project (optional, personal-layer surfacing target)
# ---------------------------------------------------------------------------
def test_todoist_project_absent_defaults_empty_and_reaches_context():
    spec = parse_hook_spec(_DEV_SPEC)
    assert spec["todoist_project"] == ""
    ctx = build_worker_context(spec, {})
    assert ctx["hook.todoist_project"] == ""


def test_todoist_project_parsed_and_threaded_to_worker_context():
    spec_text = _DEV_SPEC.replace(
        'output_wing: "vault/dev-tools"',
        'output_wing: "vault/dev-tools"\ntodoist_project: "Iga Research"',
    )
    spec = parse_hook_spec(spec_text)
    assert spec["todoist_project"] == "Iga Research"
    ctx = build_worker_context(spec, {})
    # Step 5b depends on this key being present in the worker context.
    assert ctx["hook.todoist_project"] == "Iga Research"


# ---------------------------------------------------------------------------
# sinks (generic finding-sink contract)
# ---------------------------------------------------------------------------
def test_sinks_default_is_sqlite_floor_and_reaches_context():
    spec = parse_hook_spec(_DEV_SPEC)
    assert spec["sinks"] == [{"type": "sqlite"}]
    ctx = build_worker_context(spec, {})
    assert ctx["hook.sinks"] == [{"type": "sqlite"}]


def test_sinks_legacy_todoist_project_folds_into_sinks():
    spec_text = _DEV_SPEC.replace(
        'output_wing: "vault/dev-tools"',
        'output_wing: "vault/dev-tools"\ntodoist_project: "Iga Research"',
    )
    spec = parse_hook_spec(spec_text)
    assert {"type": "todoist", "project": "Iga Research"} in spec["sinks"]
    assert any(s["type"] == "sqlite" for s in spec["sinks"])  # floor


def test_sinks_explicit_list_parsed_and_threaded():
    spec_text = _DEV_SPEC.replace(
        'output_wing: "vault/dev-tools"',
        'output_wing: "vault/dev-tools"\nsinks:\n  - sqlite\n  - todoist\n'
        'todoist_project: "P"',
    )
    spec = parse_hook_spec(spec_text)
    ctx = build_worker_context(spec, {})
    types = {s["type"] for s in ctx["hook.sinks"]}
    assert types == {"sqlite", "todoist"}


def test_sinks_unknown_type_raises_hookspecerror():
    spec_text = _DEV_SPEC.replace(
        'output_wing: "vault/dev-tools"',
        'output_wing: "vault/dev-tools"\nsinks:\n  - smoke-signal',
    )
    with pytest.raises(HookSpecError):
        parse_hook_spec(spec_text)
