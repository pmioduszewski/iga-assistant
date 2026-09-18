"""Provider + tier resolution, and the two public calls."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Sequence

TIERS = ("cheap", "smart")

# The one tier-to-model map. ``None`` means "let the backend use its own
# configured default" (codex reads ~/.codex/config.toml; ollama has no sane
# universal default, so it must be set via IGA_MODEL_*).
# CLI aliases (sonnet/opus) are used for claude-cli so the map does not rot
# when model versions rotate.
DEFAULT_MODELS: dict[str, dict[str, str | None]] = {
    "claude-cli": {"cheap": "sonnet", "smart": "opus"},
    "codex-cli": {"cheap": None, "smart": None},
    "anthropic": {"cheap": "claude-sonnet-5", "smart": "claude-opus-5"},
    "openai": {"cheap": "gpt-5-mini", "smart": "gpt-5"},
    "ollama": {"cheap": None, "smart": None},
}

PROVIDERS = tuple(DEFAULT_MODELS)
DEFAULT_PROVIDER = "claude-cli"
AGENT_PROVIDERS = ("claude-cli", "codex-cli")


class LLMError(RuntimeError):
    """Any backend failure: bad config, non-zero exit, HTTP error, refusal."""


@dataclass(frozen=True)
class AgentResult:
    """Outcome of :func:`run_agent`. Mirrors what dispatchers already log."""

    provider: str
    model: str | None
    exit: int
    stdout: str
    stderr: str


def resolve(
    tier: str = "cheap",
    *,
    model: str | None = None,
    provider: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    """Return ``(provider, model)`` for a call.

    Precedence for the model: explicit ``model`` arg, then
    ``IGA_MODEL_<TIER>``, then the default map.
    """
    env = os.environ if env is None else env
    if tier not in TIERS:
        raise LLMError(f"unknown tier {tier!r}; expected one of {TIERS}")
    name = (provider or "").strip() or (env.get("IGA_PROVIDER") or "").strip() or DEFAULT_PROVIDER
    if name not in DEFAULT_MODELS:
        raise LLMError(f"unknown IGA_PROVIDER {name!r}; expected one of {PROVIDERS}")
    chosen = model or env.get(f"IGA_MODEL_{tier.upper()}") or DEFAULT_MODELS[name][tier]
    return name, (chosen.strip() if chosen else None)


def complete(
    prompt: str,
    *,
    tier: str = "cheap",
    model: str | None = None,
    system: str | None = None,
    timeout: float = 120.0,
    provider: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Text in, text out, on whichever backend is configured."""
    from . import backends

    env = os.environ if env is None else env
    name, chosen = resolve(tier, model=model, provider=provider, env=env)
    fn = backends.COMPLETE[name]
    return fn(prompt, model=chosen, system=system, timeout=timeout, env=env)


def run_agent(
    prompt: str,
    *,
    tier: str = "cheap",
    model: str | None = None,
    cwd: str | None = None,
    add_dirs: Sequence[str] = (),
    allowed_tools: str | None = None,
    max_turns: int | None = None,
    read_only: bool = False,
    timeout: float = 1800.0,
    provider: str | None = None,
    env: Mapping[str, str] | None = None,
) -> AgentResult:
    """Headless agent run with tools. CLI backends only.

    ``read_only=True`` means the run must not mutate the filesystem. Claude
    Code enforces that through ``allowed_tools`` (pass a list without
    Bash/Write/Edit); codex enforces it through a read-only sandbox. MCP tools
    keep working either way.

    ``allowed_tools`` and ``max_turns`` are Claude Code concepts; backends
    without an equivalent ignore them (codex gates tools via its sandbox and
    its own config instead).
    """
    from . import backends

    env = os.environ if env is None else env
    name, chosen = resolve(tier, model=model, provider=provider, env=env)
    if name not in AGENT_PROVIDERS:
        raise LLMError(
            f"provider {name!r} cannot run agents (no tools); "
            f"use one of {AGENT_PROVIDERS} for agent jobs"
        )
    fn = backends.AGENT[name]
    return fn(
        prompt,
        model=chosen,
        cwd=cwd,
        add_dirs=tuple(add_dirs),
        allowed_tools=allowed_tools,
        max_turns=max_turns,
        read_only=read_only,
        timeout=timeout,
        env=env,
    )
