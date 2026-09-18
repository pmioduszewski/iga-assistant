"""iga_llm: the single provider entry point for Iga's headless LLM calls.

WHY THIS EXISTS
---------------
Every headless path used to shell out to ``claude -p`` with a hardcoded model
id, which made Iga hostage to one harness and one vendor. Engines now ask for
a *tier* (``cheap`` | ``smart``) and this module decides which backend and
which concrete model serve it.

Two capabilities, deliberately separate:

  * :func:`complete`  : text in, text out. Works on every backend.
  * :func:`run_agent` : a headless agent run with tools (file edits, MCP, web).
                        Only CLI backends can do this; API backends raise.

Configuration is env-only (see README.md): ``IGA_PROVIDER``,
``IGA_MODEL_CHEAP``, ``IGA_MODEL_SMART``.

Stdlib only. The ``anthropic`` backend lazily imports the official SDK, so
the zero-dependency contract holds for everyone who does not select it.
"""

from .core import (
    AGENT_PROVIDERS,
    AgentResult,
    LLMError,
    PROVIDERS,
    TIERS,
    complete,
    resolve,
    run_agent,
)

__all__ = [
    "AGENT_PROVIDERS",
    "AgentResult",
    "LLMError",
    "PROVIDERS",
    "TIERS",
    "complete",
    "resolve",
    "run_agent",
]
