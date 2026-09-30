import importlib.util
import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).parents[1] / "engine/install.py"
spec = importlib.util.spec_from_file_location("response_guard_install", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_preserves_other_handlers_and_idempotent(tmp_path):
    data = {"permissions": {"allow": ["Read"]}, "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "other-hook"}]}]}}
    installed = installer.merge(data, tmp_path, "claude", 120)
    assert installer.merge(installed, tmp_path, "claude", 120) == installed
    assert installer.merge(installed, tmp_path, "claude", 120, uninstall=True) == data
    assert len(installed["hooks"]["Stop"]) == 2


def test_changed_limit_replaces_owned_hook(tmp_path):
    changed = installer.merge(installer.merge({}, tmp_path, "codex", 120), tmp_path, "codex", 90)
    for event in installer.EVENTS:
        assert len(changed["hooks"][event]) == 1
        assert "--max-words 90" in changed["hooks"][event][0]["hooks"][0]["command"]


def test_dry_run_does_not_write(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path), "--dry-run"], capture_output=True)
    assert proc.returncode == 0
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()


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
