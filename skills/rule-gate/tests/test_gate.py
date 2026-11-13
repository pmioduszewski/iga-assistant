from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[1] / "engine"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"rule_gate_{name}", ENGINE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("gate")
installer = load("install")
TOOL = "mcp__IgaMemory__mempalace_add_drawer"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("IGA_RULE_GATE", raising=False)
    monkeypatch.delenv("IGA_RULE_ROOMS", raising=False)


def pre(wing, room, content="CORRECTION: never ask before checking", tool=TOOL):
    return gate.process_event({"hook_event_name": "PreToolUse", "session_id": "t", "tool_name": tool,
                               "tool_input": {"wing": wing, "room": room, "content": content}})


@pytest.mark.parametrize("wing,room", [
    ("iga", "rules"), ("iga/rules", "rules"), ("iga_rules", "behavior"), ("iga", "corrections"),
    ("iga/rules/corrections", "anything"), ("IGA", "Rules"),
])
def test_new_drawers_in_rule_rooms_are_denied(wing, room):
    out = pre(wing, room)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
    assert "state/corrections.md" in out["permissionDecisionReason"]
    assert "USER ASKED:" in out["permissionDecisionReason"]


@pytest.mark.parametrize("wing,room", [
    ("user", "finance"), ("projects", "website"), ("people", "family"), ("decisions", "business"),
    ("iga", "tooling"), ("iga_rules", "tooling"), ("iga", "patterns"), ("wing_iga", "diary"),
])
def test_fact_rooms_pass(wing, room):
    assert pre(wing, room) == {}


def test_a_rule_the_user_asked_for_passes():
    assert pre("iga", "rules", 'USER ASKED: "from now on remind me about bills two days ahead"') == {}
    assert pre("iga", "rules", "  user asked: lowercase works too") == {}


def test_other_tools_and_shapes_pass():
    assert pre("iga", "rules", tool="mcp__IgaMemory__mempalace_update_drawer") == {}
    assert gate.process_event({"tool_name": TOOL, "tool_input": "not an object"}) == {}
    assert gate.process_event(["not", "a", "payload"]) == {}


def test_kill_switch_and_custom_rooms(monkeypatch):
    monkeypatch.setenv("IGA_RULE_ROOMS", "rules, lessons")
    assert pre("iga", "lessons") != {} and pre("iga", "corrections") == {}
    monkeypatch.setenv("IGA_RULE_GATE", "0")
    assert pre("iga", "rules") == {}


def run(stdin: str):
    return subprocess.run([sys.executable, str(ENGINE / "gate.py")], input=stdin, capture_output=True, text=True)


def test_command_line_emits_deny_json_and_fails_open():
    payload = {"hook_event_name": "PreToolUse", "tool_name": TOOL,
               "tool_input": {"wing": "iga", "room": "rules", "content": "x"}}
    done = run(json.dumps(payload))
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    for broken in ("not json", "", "[]"):
        result = run(broken)
        assert result.returncode == 0 and result.stdout == ""


def test_install_is_idempotent_and_reversible(tmp_path):
    data = {"permissions": {"allow": ["Read"]}, "hooks": {
        "PreToolUse": [{"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "other-hook"}]}]}}
    installed = installer.merge(data, tmp_path)
    assert installer.merge(installed, tmp_path) == installed
    assert installer.merge(installed, tmp_path, uninstall=True) == data
    ours = installed["hooks"]["PreToolUse"][-1]
    assert ours["matcher"] == installer.MATCHER and "rule-gate/engine/gate.py" in ours["hooks"][0]["command"]
    assert installer.merge(installer.merge({}, tmp_path), tmp_path, uninstall=True) == {}


def test_install_command_dry_run_then_write(tmp_path):
    script = str(ENGINE / "install.py")
    dry = subprocess.run([sys.executable, script, "--root", str(tmp_path), "--dry-run"], capture_output=True)
    assert dry.returncode == 0 and not (tmp_path / ".claude").exists()
    real = subprocess.run([sys.executable, script, "--root", str(tmp_path)], capture_output=True)
    assert real.returncode == 0
    settings = json.loads((tmp_path / ".claude/settings.local.json").read_text())
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == installer.MATCHER
    assert not (tmp_path / ".codex").exists()
