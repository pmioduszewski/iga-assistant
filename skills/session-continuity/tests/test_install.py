import importlib.util
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).parents[1] / "engine/install.py"
spec = importlib.util.spec_from_file_location("session_continuity_install", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_preserves_other_handlers_and_is_idempotent(tmp_path):
    data = {"permissions": {"allow": ["Read"]}, "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-hook"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "stop-hook"}]}]}}
    installed = installer.merge(data, tmp_path, "claude")
    assert installer.merge(installed, tmp_path, "claude") == installed
    assert installer.merge(installed, tmp_path, "claude", uninstall=True) == data
    assert len(installed["hooks"]["UserPromptSubmit"]) == 2
    assert installed["hooks"]["Stop"] == data["hooks"]["Stop"]


def test_fresh_install_and_uninstall_round_trip(tmp_path):
    installed = installer.merge({}, tmp_path, "codex")
    (group,) = installed["hooks"]["UserPromptSubmit"]
    command = group["hooks"][0]["command"]
    assert "session-continuity/engine/hook.py" in command and "--host codex" in command
    assert installer.merge(installed, tmp_path, "codex", uninstall=True) == {}


def test_writes_both_hosts_and_dry_run_writes_nothing(tmp_path):
    dry = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--dry-run"], capture_output=True)
    assert dry.returncode == 0
    assert not (tmp_path / ".claude").exists() and not (tmp_path / ".codex").exists()
    real = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path)], capture_output=True)
    assert real.returncode == 0
    for target in (".claude/settings.local.json", ".codex/hooks.json"):
        assert "UserPromptSubmit" in json.loads((tmp_path / target).read_text())["hooks"]


def test_invalid_second_target_preserves_first(tmp_path):
    first = tmp_path / ".claude/settings.local.json"
    second = tmp_path / ".codex/hooks.json"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text(json.dumps({"permissions": {"allow": ["Read"]}}))
    original = first.read_bytes()
    second.write_text("invalid")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path)], capture_output=True)
    assert proc.returncode == 1
    assert first.read_bytes() == original
    assert second.read_text() == "invalid"
