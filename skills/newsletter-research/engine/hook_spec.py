#!/usr/bin/env python3
"""Hook spec parser/validator — stdlib only.

Parses a hook spec Markdown file (YAML frontmatter + optional body) and
returns a typed dict. Bad or missing required fields raise HookSpecError with
a clear, actionable message; the engine never crashes silently.

Schema documented in ``skills/newsletter-research/docs/hook-spec.md``.

Design mirrors ``extract.py``: pure, side-effect-free, no I/O beyond what
the caller provides, fully unit-testable. The engine calls this once at
job-dispatch time to load the user's hook before handing it to the worker.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class HookSpecError(ValueError):
    """Raised when a hook spec file is missing required fields or is invalid.

    The message is human-readable and actionable (references the field name
    and what's wrong). The engine surfaces it as a named job-load error.
    """


# ---------------------------------------------------------------------------
# Frontmatter extraction
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body_text).

    Supports ``--- ... ---`` fences only (GitHub-flavour Markdown).
    If no valid frontmatter block is found, frontmatter_text is ``""``
    and body_text is the entire input.
    """
    m = _FM_RE.match(text or "")
    if not m:
        return "", text
    return m.group(1), text[m.end():]


# ---------------------------------------------------------------------------
# Minimal YAML parser (stdlib: no PyYAML dependency)
# ---------------------------------------------------------------------------
# Covers the subset used by hook specs:
#   - scalar key: value  (string, int, or multi-line | block)
#   - list items under a key:
#       key:
#         - item1
#         - item2
#   - nested dict for trigger:
#       trigger:
#         gmail_label: "..."
# Does NOT need to handle arbitrary YAML — hook specs are intentionally simple.


def _parse_simple_yaml(text: str) -> dict[str, Any]:  # noqa: C901
    """Parse hook spec frontmatter.

    Limitations (by design):
    - Block scalars (``|`` / ``>``) are supported for direct values only.
    - Nested dicts are supported one level deep (for ``trigger:``).
    - Does not handle anchors, aliases, flow sequences/mappings, or multi-document.
    Returns a plain dict with str/int/list/dict values.
    """
    result: dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    n = len(lines)

    def _strip_quotes(s: str) -> str:
        s = s.strip()
        if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
            return s[1:-1]
        return s

    while i < n:
        line = lines[i]
        # Skip blank lines and comments.
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue

        # Top-level key:value  (indent == 0)
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)', line)
        if not m:
            i += 1
            continue
        key = m.group(1)
        rest = m.group(2).strip()

        # Block scalar (|)
        if rest == "|":
            i += 1
            block_lines: list[str] = []
            # Detect indent of the block from first non-empty line.
            indent = None
            while i < n:
                bl = lines[i]
                if not bl.strip():
                    block_lines.append("")
                    i += 1
                    continue
                detected = len(bl) - len(bl.lstrip())
                if indent is None:
                    indent = detected
                if detected < (indent or 0):
                    break
                block_lines.append(bl[(indent or 0):])
                i += 1
            # Strip trailing blank lines.
            while block_lines and not block_lines[-1]:
                block_lines.pop()
            result[key] = "\n".join(block_lines)
            continue

        # Empty value → look ahead for list or nested dict.
        if rest == "":
            i += 1
            items: list[Any] = []
            nested: dict[str, str] = {}
            while i < n:
                sub = lines[i]
                if not sub.strip():
                    i += 1
                    continue
                indent_sub = len(sub) - len(sub.lstrip())
                if indent_sub == 0:
                    break
                stripped = sub.strip()
                # List item.
                if stripped.startswith("- "):
                    items.append(_strip_quotes(stripped[2:]))
                    i += 1
                # Nested key: value.
                elif re.match(r'^[A-Za-z_][A-Za-z0-9_]*:\s', stripped):
                    nm = re.match(r'^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)', stripped)
                    if nm:
                        nested[nm.group(1)] = _strip_quotes(nm.group(2))
                    i += 1
                else:
                    i += 1
            if items:
                result[key] = items
            elif nested:
                result[key] = nested
            # (empty sub-block → key absent)
            continue

        # Inline value.
        result[key] = _strip_quotes(rest)
        i += 1

    return result


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*$')


def _require_str(d: dict, field: str, *, nonempty: bool = True) -> str:
    val = d.get(field)
    if val is None:
        raise HookSpecError(f"hook spec missing required field: '{field}'")
    if not isinstance(val, str):
        raise HookSpecError(
            f"hook spec field '{field}' must be a string, got {type(val).__name__}"
        )
    if nonempty and not val.strip():
        raise HookSpecError(f"hook spec field '{field}' must not be empty")
    return val


def _require_list(d: dict, field: str) -> list[str]:
    val = d.get(field)
    if val is None:
        raise HookSpecError(f"hook spec missing required field: '{field}'")
    if not isinstance(val, list) or not val:
        raise HookSpecError(
            f"hook spec field '{field}' must be a non-empty list of strings"
        )
    return [str(v) for v in val]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_hook_spec(text: str) -> dict[str, Any]:
    """Parse and validate a hook spec file.

    ``text`` is the full file content (frontmatter + optional body).

    Returns a typed dict with keys:
      name, description, trigger, interest_profile, scoring_context,
      fit_threshold, output_wing, cadence, status, body

    Raises ``HookSpecError`` on any validation failure.
    """
    fm_text, body = _split_frontmatter(text)
    if not fm_text.strip():
        raise HookSpecError(
            "hook spec has no YAML frontmatter block (expected --- ... ---)"
        )

    raw = _parse_simple_yaml(fm_text)

    # --- name ---
    name = _require_str(raw, "name")
    if not _SLUG_RE.match(name):
        raise HookSpecError(
            f"hook spec 'name' must be a lowercase slug ([a-z0-9_-]+), got: {name!r}"
        )

    # --- description ---
    description = _require_str(raw, "description")

    # --- trigger ---
    trigger_raw = raw.get("trigger")
    if not isinstance(trigger_raw, dict):
        raise HookSpecError(
            "hook spec 'trigger' must be a mapping with 'gmail_label' or 'gmail_query'"
        )
    has_label = "gmail_label" in trigger_raw
    has_query = "gmail_query" in trigger_raw
    if has_label and has_query:
        raise HookSpecError(
            "hook spec 'trigger' must have EITHER 'gmail_label' OR 'gmail_query', not both"
        )
    if not has_label and not has_query:
        raise HookSpecError(
            "hook spec 'trigger' must have 'gmail_label' or 'gmail_query'"
        )
    trigger = dict(trigger_raw)

    # --- interest_profile ---
    interest_profile = _require_str(raw, "interest_profile")

    # --- scoring_context ---
    scoring_context = _require_list(raw, "scoring_context")

    # --- fit_threshold ---
    ft_raw = raw.get("fit_threshold", 2)
    try:
        fit_threshold = int(ft_raw)
    except (TypeError, ValueError):
        raise HookSpecError(
            f"hook spec 'fit_threshold' must be an integer 0–3, got: {ft_raw!r}"
        )
    if not (0 <= fit_threshold <= 3):
        raise HookSpecError(
            f"hook spec 'fit_threshold' must be 0–3, got: {fit_threshold}"
        )

    # --- output_wing ---
    output_wing = _require_str(raw, "output_wing")

    # --- cadence ---
    cadence_raw = raw.get("cadence", "on-demand")
    cadence = str(cadence_raw).strip()
    if cadence not in ("on-demand", "auto"):
        raise HookSpecError(
            f"hook spec 'cadence' must be 'on-demand' or 'auto', got: {cadence!r}"
        )

    # --- status ---
    status_raw = raw.get("status", "active")
    status = str(status_raw).strip()
    if status not in ("active", "paused"):
        raise HookSpecError(
            f"hook spec 'status' must be 'active' or 'paused', got: {status!r}"
        )

    return {
        "name": name,
        "description": description,
        "trigger": trigger,
        "interest_profile": interest_profile,
        "scoring_context": scoring_context,
        "fit_threshold": fit_threshold,
        "output_wing": output_wing,
        "cadence": cadence,
        "status": status,
        "body": body.strip(),
    }


def load_hook_spec(path: str) -> dict[str, Any]:
    """Read a hook spec file from *path* and return the parsed spec dict.

    Raises ``HookSpecError`` on parse/validation failure.
    Raises ``OSError`` if the file is unreadable (caller decides how to handle).
    """
    with open(path, encoding="utf-8") as fh:
        return parse_hook_spec(fh.read())
