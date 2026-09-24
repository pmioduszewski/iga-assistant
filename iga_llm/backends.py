"""Backends. Each one is a plain function; the registries at the bottom are
the only thing ``core`` knows about.

Secrets are read from the environment inside the call and never appear in an
exception message or a log line.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Mapping

from .core import AgentResult, LLMError

_TAIL = 500


def _tail(text: str | None) -> str:
    return (text or "").strip()[-_TAIL:]


def _run(argv: list[str], prompt: str, *, timeout: float, cwd: str | None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except FileNotFoundError as ex:
        raise LLMError(f"binary not found: {argv[0]}") from ex
    except subprocess.TimeoutExpired as ex:
        raise LLMError(f"{os.path.basename(argv[0])} timed out after {timeout:.0f}s") from ex


# --------------------------------------------------------------------------
# claude-cli: headless Claude Code. Default backend, so default behavior of
# every caller is unchanged.
# --------------------------------------------------------------------------

def _claude_bin(env: Mapping[str, str]) -> str:
    return env.get("IGA_CLAUDE_BIN") or "claude"


def claude_cli_complete(prompt, *, model, system, timeout, env) -> str:
    # A pure completion needs no tools: without --strict-mcp-config every call
    # would start every user-scope MCP server (slower, and OAuth servers can
    # open browser tabs).
    argv = [
        _claude_bin(env), "-p", "--output-format", "text",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]
    if model:
        argv += ["--model", model]
    if system:
        argv += ["--append-system-prompt", system]
    # Neutral cwd: a pure completion must not inherit a project's CLAUDE.md.
    with tempfile.TemporaryDirectory(prefix="iga-llm-") as tmp:
        proc = _run(argv, prompt, timeout=timeout, cwd=tmp)
    if proc.returncode != 0:
        raise LLMError(f"claude -p exited {proc.returncode}: {_tail(proc.stderr) or _tail(proc.stdout)}")
    return proc.stdout


def claude_cli_agent(prompt, *, model, cwd, add_dirs, allowed_tools, max_turns, read_only, timeout, env) -> AgentResult:
    # read_only is enforced by the caller's allowed_tools list on this backend.
    # Prompt goes via STDIN: --allowedTools is variadic and swallows a
    # trailing positional prompt.
    argv = [_claude_bin(env), "-p", "--permission-mode", "acceptEdits"]
    if model:
        argv += ["--model", model]
    if max_turns:
        argv += ["--max-turns", str(max_turns)]
    if allowed_tools:
        argv += ["--allowedTools", allowed_tools]
    for d in add_dirs:
        argv += ["--add-dir", d]
    proc = _run(argv, prompt, timeout=timeout, cwd=cwd)
    return AgentResult("claude-cli", model, proc.returncode, proc.stdout, proc.stderr)


# --------------------------------------------------------------------------
# codex-cli: OpenAI Codex CLI, non-interactive `codex exec`.
# --------------------------------------------------------------------------

def _codex_bin(env: Mapping[str, str]) -> str:
    return env.get("IGA_CODEX_BIN") or "codex"


def codex_cli_complete(prompt, *, model, system, timeout, env) -> str:
    full = f"{system}\n\n{prompt}" if system else prompt
    with tempfile.TemporaryDirectory(prefix="iga-llm-") as tmp:
        out_path = os.path.join(tmp, "last-message.txt")
        argv = [
            _codex_bin(env), "exec",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
            "-C", tmp,
            "-o", out_path,
        ]
        if model:
            argv += ["-m", model]
        argv.append("-")  # read the prompt from stdin
        proc = _run(argv, full, timeout=timeout, cwd=tmp)
        if proc.returncode != 0:
            raise LLMError(f"codex exec exited {proc.returncode}: {_tail(proc.stderr) or _tail(proc.stdout)}")
        try:
            with open(out_path, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError as ex:
            raise LLMError("codex exec produced no final message") from ex


def codex_cli_agent(prompt, *, model, cwd, add_dirs, allowed_tools, max_turns, read_only, timeout, env) -> AgentResult:
    argv = [
        _codex_bin(env), "exec",
        "--sandbox", "read-only" if read_only else "workspace-write",
        "--skip-git-repo-check",
        "--color", "never",
    ]
    if cwd:
        argv += ["-C", cwd]
    if not read_only:  # codex --add-dir grants WRITE access
        for d in add_dirs:
            argv += ["--add-dir", d]
    if model:
        argv += ["-m", model]
    argv.append("-")
    proc = _run(argv, prompt, timeout=timeout, cwd=cwd)
    return AgentResult("codex-cli", model, proc.returncode, proc.stdout, proc.stderr)


# --------------------------------------------------------------------------
# anthropic: Claude API through the official SDK (lazy import, optional dep).
# Credentials resolve the SDK's way: ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
# or an `ant auth login` profile.
# --------------------------------------------------------------------------

def anthropic_complete(prompt, *, model, system, timeout, env) -> str:
    try:
        import anthropic
    except ImportError as ex:
        raise LLMError("IGA_PROVIDER=anthropic needs the SDK: pip install anthropic") from ex

    client = anthropic.Anthropic(timeout=timeout)
    kwargs = {
        "model": model,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    try:
        resp = client.messages.create(**kwargs)
    except anthropic.RateLimitError as ex:
        raise LLMError("anthropic: rate limited (429)") from ex
    except anthropic.APIStatusError as ex:
        raise LLMError(f"anthropic: HTTP {ex.status_code}: {ex.message}") from ex
    except anthropic.APIConnectionError as ex:
        raise LLMError("anthropic: connection error") from ex
    if resp.stop_reason == "refusal":
        raise LLMError("anthropic: request refused")
    return "".join(b.text for b in resp.content if b.type == "text")


# --------------------------------------------------------------------------
# openai: any OpenAI-compatible Chat Completions endpoint over plain urllib.
# Covers OpenAI, Ollama, LM Studio, OpenRouter and similar by base URL.
# --------------------------------------------------------------------------

_OPENAI_BASE = "https://api.openai.com/v1"
_OLLAMA_BASE = "http://localhost:11434/v1"


def _chat_completions(prompt, *, model, system, timeout, base_url, api_key, label) -> str:
    if not model:
        raise LLMError(f"{label}: no model configured; set IGA_MODEL_CHEAP / IGA_MODEL_SMART")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps({"model": model, "messages": messages}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        detail = _tail(ex.read().decode("utf-8", "replace"))
        raise LLMError(f"{label}: HTTP {ex.code}: {detail}") from ex
    except urllib.error.URLError as ex:
        raise LLMError(f"{label}: connection error: {ex.reason}") from ex
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as ex:
        raise LLMError(f"{label}: unexpected response shape") from ex


def openai_complete(prompt, *, model, system, timeout, env) -> str:
    key = env.get("OPENAI_API_KEY")
    base = env.get("IGA_OPENAI_BASE_URL") or _OPENAI_BASE
    if not key and base == _OPENAI_BASE:
        raise LLMError("openai: OPENAI_API_KEY is not set")
    return _chat_completions(
        prompt, model=model, system=system, timeout=timeout,
        base_url=base, api_key=key, label="openai",
    )


def ollama_complete(prompt, *, model, system, timeout, env) -> str:
    return _chat_completions(
        prompt, model=model, system=system, timeout=timeout,
        base_url=env.get("IGA_OLLAMA_BASE_URL") or _OLLAMA_BASE, api_key=None, label="ollama",
    )


COMPLETE = {
    "claude-cli": claude_cli_complete,
    "codex-cli": codex_cli_complete,
    "anthropic": anthropic_complete,
    "openai": openai_complete,
    "ollama": ollama_complete,
}

AGENT = {
    "claude-cli": claude_cli_agent,
    "codex-cli": codex_cli_agent,
}
