"""iga_llm: resolution, backend argv/payload shape, error paths. No network,
no real binaries: subprocess.run and urlopen are faked."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import types
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import iga_llm  # noqa: E402
from iga_llm import LLMError, backends, complete, resolve, run_agent  # noqa: E402
from iga_llm.__main__ import main  # noqa: E402


class FakeRun:
    """Records argv/input/cwd; optionally writes codex's -o file."""

    def __init__(self, returncode=0, stdout="OUT", stderr="", last_message=None):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        self.last_message = last_message
        self.calls = []

    def __call__(self, argv, input=None, capture_output=None, text=None, timeout=None, cwd=None):
        self.calls.append({"argv": argv, "input": input, "cwd": cwd, "timeout": timeout})
        if self.last_message is not None and "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_text(self.last_message, encoding="utf-8")
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


# ---------------------------------------------------------------- resolve

def test_default_is_claude_cli_with_alias_models():
    assert resolve("cheap", env={}) == ("claude-cli", "sonnet")
    assert resolve("smart", env={}) == ("claude-cli", "opus")


def test_env_selects_provider_and_tier_model():
    env = {"IGA_PROVIDER": "openai", "IGA_MODEL_CHEAP": "my-mini"}
    assert resolve("cheap", env=env) == ("openai", "my-mini")
    assert resolve("smart", env=env) == ("openai", "gpt-5")


def test_explicit_model_beats_env():
    env = {"IGA_MODEL_CHEAP": "from-env"}
    assert resolve("cheap", model="explicit", env=env) == ("claude-cli", "explicit")


def test_codex_defaults_to_its_own_config_model():
    assert resolve("smart", env={"IGA_PROVIDER": "codex-cli"}) == ("codex-cli", None)


@pytest.mark.parametrize("kwargs", [{"tier": "huge"}, {"provider": "nope"}])
def test_bad_tier_or_provider_raises(kwargs):
    with pytest.raises(LLMError):
        resolve(**{"tier": "cheap", **kwargs}, env={})


# ---------------------------------------------------------------- claude-cli

def test_claude_complete_argv_and_neutral_cwd(monkeypatch):
    fake = FakeRun(stdout="hello")
    monkeypatch.setattr(subprocess, "run", fake)
    out = complete("PROMPT", system="SYS", env={"IGA_CLAUDE_BIN": "/x/claude"})
    call = fake.calls[0]
    assert out == "hello"
    assert call["argv"][:4] == ["/x/claude", "-p", "--output-format", "text"]
    assert "--strict-mcp-config" in call["argv"]
    assert call["argv"][call["argv"].index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert call["argv"][call["argv"].index("--model") + 1] == "sonnet"
    assert call["argv"][call["argv"].index("--append-system-prompt") + 1] == "SYS"
    assert call["input"] == "PROMPT"
    assert "iga-llm-" in call["cwd"]  # never the project dir


def test_claude_complete_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", FakeRun(returncode=2, stdout="", stderr="boom"))
    with pytest.raises(LLMError, match="exited 2: boom"):
        complete("p", env={})


def test_missing_binary_raises_llmerror(monkeypatch):
    def raiser(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(subprocess, "run", raiser)
    with pytest.raises(LLMError, match="binary not found"):
        complete("p", env={})


def test_claude_agent_matches_legacy_dispatch_argv(monkeypatch):
    fake = FakeRun(stdout="done")
    monkeypatch.setattr(subprocess, "run", fake)
    res = run_agent(
        "P", model="claude-sonnet-4-6", cwd="/home/iga", add_dirs=["/home/iga"],
        allowed_tools="Read,Write", max_turns=80, timeout=5, env={"IGA_CLAUDE_BIN": "claude"},
    )
    assert fake.calls[0]["argv"] == [
        "claude", "-p", "--permission-mode", "acceptEdits",
        "--model", "claude-sonnet-4-6",
        "--max-turns", "80",
        "--allowedTools", "Read,Write",
        "--add-dir", "/home/iga",
    ]
    assert fake.calls[0]["cwd"] == "/home/iga"
    assert (res.provider, res.exit, res.stdout) == ("claude-cli", 0, "done")


# ---------------------------------------------------------------- codex-cli

def test_codex_complete_reads_last_message_file(monkeypatch):
    fake = FakeRun(stdout="event noise", last_message="final answer")
    monkeypatch.setattr(subprocess, "run", fake)
    out = complete("PROMPT", system="SYS", env={"IGA_PROVIDER": "codex-cli"})
    argv = fake.calls[0]["argv"]
    assert out == "final answer"
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "-m" not in argv  # tier default = codex's own config
    assert argv[-1] == "-"
    assert fake.calls[0]["input"] == "SYS\n\nPROMPT"


def test_codex_complete_passes_model_when_set(monkeypatch):
    fake = FakeRun(last_message="x")
    monkeypatch.setattr(subprocess, "run", fake)
    complete("p", env={"IGA_PROVIDER": "codex-cli", "IGA_MODEL_CHEAP": "gpt-x"})
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("-m") + 1] == "gpt-x"


def test_codex_agent_is_workspace_write_in_cwd(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    res = run_agent("P", cwd="/w", add_dirs=["/extra"], allowed_tools="ignored",
                    env={"IGA_PROVIDER": "codex-cli"})
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("-C") + 1] == "/w"
    assert argv[argv.index("--add-dir") + 1] == "/extra"
    assert "ignored" not in argv
    assert res.provider == "codex-cli"


def test_codex_agent_read_only_drops_write_access(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)
    run_agent("P", cwd="/w", add_dirs=["/w"], read_only=True, env={"IGA_PROVIDER": "codex-cli"})
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--add-dir" not in argv


def test_blank_provider_env_falls_back_to_default():
    assert resolve("cheap", env={"IGA_PROVIDER": "  "}) == ("claude-cli", "sonnet")


def test_api_providers_cannot_run_agents():
    with pytest.raises(LLMError, match="cannot run agents"):
        run_agent("p", env={"IGA_PROVIDER": "openai"})


# ---------------------------------------------------------------- openai / ollama

class FakeHTTP:
    def __init__(self, body):
        self.body, self.requests = body, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        return io.BytesIO(json.dumps(self.body).encode())


def _ok(text):
    return {"choices": [{"message": {"content": text}}]}


def test_openai_payload_and_auth(monkeypatch):
    fake = FakeHTTP(_ok("hi"))
    monkeypatch.setattr(backends.urllib.request, "urlopen", fake)
    out = complete("PROMPT", system="SYS", env={"IGA_PROVIDER": "openai", "OPENAI_API_KEY": "k"})
    req = fake.requests[0]
    body = json.loads(req.data)
    assert out == "hi"
    assert req.full_url == "https://api.openai.com/v1/chat/completions"
    assert req.get_header("Authorization") == "Bearer k"
    assert body == {"model": "gpt-5-mini", "messages": [
        {"role": "system", "content": "SYS"}, {"role": "user", "content": "PROMPT"}]}


def test_openai_requires_key_for_default_base():
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        complete("p", env={"IGA_PROVIDER": "openai"})


def test_openai_compatible_base_url_needs_no_key(monkeypatch):
    fake = FakeHTTP(_ok("local"))
    monkeypatch.setattr(backends.urllib.request, "urlopen", fake)
    env = {"IGA_PROVIDER": "openai", "IGA_OPENAI_BASE_URL": "http://localhost:1234/v1/"}
    assert complete("p", env=env) == "local"
    assert fake.requests[0].full_url == "http://localhost:1234/v1/chat/completions"
    assert fake.requests[0].get_header("Authorization") is None


def test_ollama_needs_a_model_then_hits_localhost(monkeypatch):
    with pytest.raises(LLMError, match="no model configured"):
        complete("p", env={"IGA_PROVIDER": "ollama"})
    fake = FakeHTTP(_ok("ol"))
    monkeypatch.setattr(backends.urllib.request, "urlopen", fake)
    assert complete("p", env={"IGA_PROVIDER": "ollama", "IGA_MODEL_CHEAP": "llama3"}) == "ol"
    assert fake.requests[0].full_url.startswith("http://localhost:11434/v1/")


def test_http_error_never_leaks_the_key(monkeypatch):
    def raiser(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))
    monkeypatch.setattr(backends.urllib.request, "urlopen", raiser)
    with pytest.raises(LLMError) as ei:
        complete("p", env={"IGA_PROVIDER": "openai", "OPENAI_API_KEY": "sk-SECRET"})
    assert "HTTP 401" in str(ei.value) and "sk-SECRET" not in str(ei.value)


# ---------------------------------------------------------------- anthropic (SDK faked)

def _fake_anthropic(monkeypatch, *, stop_reason="end_turn", text="claude says"):
    mod = types.ModuleType("anthropic")
    seen = {}

    class _Err(Exception):
        pass

    mod.RateLimitError = type("RateLimitError", (_Err,), {})
    mod.APIStatusError = type("APIStatusError", (_Err,), {})
    mod.APIConnectionError = type("APIConnectionError", (_Err,), {})

    class Messages:
        def create(self, **kw):
            seen.update(kw)
            blocks = [types.SimpleNamespace(type="thinking", thinking=""),
                      types.SimpleNamespace(type="text", text=text)]
            return types.SimpleNamespace(stop_reason=stop_reason, content=blocks)

    class Anthropic:
        def __init__(self, timeout=None):
            self.messages = Messages()

    mod.Anthropic = Anthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return seen


def test_anthropic_uses_sdk_and_tier_models(monkeypatch):
    seen = _fake_anthropic(monkeypatch)
    out = complete("PROMPT", tier="smart", system="SYS", env={"IGA_PROVIDER": "anthropic"})
    assert out == "claude says"  # thinking blocks skipped
    assert seen["model"] == "claude-opus-5" and seen["system"] == "SYS"
    assert seen["messages"] == [{"role": "user", "content": "PROMPT"}]


def test_anthropic_refusal_raises(monkeypatch):
    _fake_anthropic(monkeypatch, stop_reason="refusal")
    with pytest.raises(LLMError, match="refused"):
        complete("p", env={"IGA_PROVIDER": "anthropic"})


def test_anthropic_without_sdk_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(LLMError, match="pip install anthropic"):
        complete("p", env={"IGA_PROVIDER": "anthropic"})


# ---------------------------------------------------------------- CLI

def test_cli_resolve_prints_json(monkeypatch, capsys):
    monkeypatch.delenv("IGA_PROVIDER", raising=False)
    monkeypatch.delenv("IGA_MODEL_SMART", raising=False)
    assert main(["--resolve", "--tier", "smart"]) == 0
    assert json.loads(capsys.readouterr().out) == {"provider": "claude-cli", "model": "opus", "tier": "smart"}


def test_cli_pipes_stdin_to_stdout(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", FakeRun(stdout="answer"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("question"))
    monkeypatch.delenv("IGA_PROVIDER", raising=False)
    assert main(["--tier", "cheap"]) == 0
    assert capsys.readouterr().out == "answer"


def test_cli_error_goes_to_stderr_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("q"))
    monkeypatch.setenv("IGA_PROVIDER", "ollama")
    monkeypatch.delenv("IGA_MODEL_CHEAP", raising=False)
    assert main([]) == 1
    assert "no model configured" in capsys.readouterr().err


def test_public_surface():
    assert set(iga_llm.PROVIDERS) == {"claude-cli", "codex-cli", "anthropic", "openai", "ollama"}
    assert os.path.basename(iga_llm.__file__) == "__init__.py"
