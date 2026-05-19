"""Job-schema dataclasses + a minimal ``proactive:`` frontmatter parser.

A skill opts into the proactive engine by declaring a ``proactive:`` list in
its SKILL.md YAML frontmatter. Wave 1 only *parses and validates* these job
definitions — it does NOT execute triggers, conditions, or actions. Trigger
strings are stored raw plus a lightweight parsed ``kind``/args split so later
waves can dispatch without re-parsing.

No third-party YAML dependency: ``pyproject.toml`` declares zero runtime deps
and pyyaml is not installed, so this module ships a minimal extractor scoped
to the shapes the ``proactive:`` block actually uses (a list of mappings whose
values are scalars or one level of nested mapping). It is intentionally NOT a
general YAML parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class SchemaError(ValueError):
    """Raised when a proactive job block is missing/invalid."""


# --------------------------------------------------------------------------- #
# Duration parsing
# --------------------------------------------------------------------------- #
_DURATION_RE = re.compile(
    r"""^\s*
        (?:(?P<weeks>\d+)\s*w)?\s*
        (?:(?P<days>\d+)\s*d)?\s*
        (?:(?P<hours>\d+)\s*h)?\s*
        (?:(?P<minutes>\d+)\s*m(?!s))?\s*
        (?:(?P<seconds>\d+)\s*s)?\s*
        $""",
    re.VERBOSE,
)

_DURATION_UNIT_SECONDS = {
    "weeks": 7 * 24 * 3600,
    "days": 24 * 3600,
    "hours": 3600,
    "minutes": 60,
    "seconds": 1,
}


def parse_duration_to_seconds(value: str | int) -> int:
    """Parse a duration string like ``48h``, ``7d``, ``1h30m`` → seconds.

    Accepts a bare int (already seconds) or an int-as-string. Raises
    ``SchemaError`` on anything unparseable. Zero / negative is rejected.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise SchemaError(f"duration must be a string or int, got bool {value!r}")
    if isinstance(value, int):
        seconds = value
    else:
        s = str(value).strip()
        if not s:
            raise SchemaError("empty duration string")
        if s.isdigit():
            seconds = int(s)
        else:
            m = _DURATION_RE.match(s)
            if not m or not any(m.groupdict().values()):
                raise SchemaError(f"unparseable duration: {value!r}")
            seconds = 0
            for unit, raw in m.groupdict().items():
                if raw is not None:
                    seconds += int(raw) * _DURATION_UNIT_SECONDS[unit]
    if seconds <= 0:
        raise SchemaError(f"duration must be positive, got {value!r}")
    return seconds


# --------------------------------------------------------------------------- #
# Trigger / action parsing (raw string + light structural split)
# --------------------------------------------------------------------------- #
_KNOWN_TRIGGER_KINDS = {
    "todoist",
    "schedule",
    "mempalace",
    "calendar",
    "watch",
    "manual",
}

_CALL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\((.*)\))?\s*$", re.DOTALL)


def _parse_call(expr: str) -> tuple[str, str]:
    """Split ``name(raw args)`` → ``(name, raw_args_string)``.

    ``manual`` (no parens) → ``("manual", "")``. Args are kept verbatim;
    we deliberately do NOT evaluate or tokenize them in Wave 1.
    """
    m = _CALL_RE.match(expr or "")
    if not m:
        raise SchemaError(f"cannot parse expression: {expr!r}")
    name = m.group(1)
    args = (m.group(2) or "").strip()
    return name, args


@dataclass
class Trigger:
    raw: str
    kind: str
    args: str = ""

    @classmethod
    def parse(cls, raw: str) -> "Trigger":
        if not isinstance(raw, str) or not raw.strip():
            raise SchemaError("trigger must be a non-empty string")
        name, args = _parse_call(raw.strip())
        if name not in _KNOWN_TRIGGER_KINDS:
            raise SchemaError(
                f"unknown trigger kind {name!r}; "
                f"expected one of {sorted(_KNOWN_TRIGGER_KINDS)}"
            )
        return cls(raw=raw.strip(), kind=name, args=args)


@dataclass
class Action:
    raw: str
    name: str
    args: str = ""

    @classmethod
    def parse(cls, raw: str) -> "Action":
        if not isinstance(raw, str) or not raw.strip():
            raise SchemaError("action must be a non-empty string")
        name, args = _parse_call(raw.strip())
        return cls(raw=raw.strip(), name=name, args=args)


# --------------------------------------------------------------------------- #
# Job
# --------------------------------------------------------------------------- #
_VALID_DELIVER = {
    "surface_next_brief",
    "slack_dm",
    "todoist_comment",
    "push",
    "interrupt",
}
_DEFAULT_DELIVER = "surface_next_brief"


@dataclass
class Job:
    id: str
    trigger: Trigger
    action: Action
    idempotency_key: str  # template, kept verbatim ({{...}} placeholders intact)
    cooldown: str  # raw duration string as authored
    cooldown_seconds: int
    condition: str | None = None  # raw, not evaluated in Wave 1
    budget: dict[str, Any] = field(default_factory=dict)
    deliver: str = _DEFAULT_DELIVER
    # Filesystem path of the source file this job was parsed from
    # (``skills/<pack>/proactive.yaml`` or ``SKILL.md``). Set by
    # ``runtime.load_jobs`` after parsing. Used to resolve a job's
    # relative ``prompt:`` against its own skill directory.
    source_path: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise SchemaError("job.id is required and must be a non-empty string")


# --------------------------------------------------------------------------- #
# Minimal frontmatter / proactive-block extractor
# --------------------------------------------------------------------------- #
def _coerce_scalar(token: str) -> Any:
    """Best-effort scalar coercion for the small value space we accept.

    Strings stay strings; we only coerce ints and bare booleans because
    ``budget.wall_min`` is an int and we want predictable types. Anything
    quoted is returned without quotes, verbatim (placeholders preserved).
    """
    t = token.strip()
    if not t:
        return ""
    if (t[0] == t[-1]) and t[0] in ("'", '"') and len(t) >= 2:
        return t[1:-1]
    low = t.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~", "none"):
        return None
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    return t


def _strip_comment(line: str) -> str:
    """Remove an unquoted trailing ``# comment``. Conservative: only strips
    when ``#`` is preceded by whitespace and not inside quotes."""
    in_s = in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            if i == 0 or line[i - 1] in (" ", "\t"):
                return line[:i]
    return line


def extract_frontmatter_block(text: str) -> str:
    """Return the YAML frontmatter (between the leading ``---`` fences)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not m:
        raise SchemaError("no YAML frontmatter block found (--- ... --- fence)")
    return m.group(1)


def _parse_proactive_list(lines: list[str], base_indent: int) -> list[dict]:
    """Parse a YAML-ish list of mappings under ``proactive:``.

    Supported shape (sufficient for the proactive block):
      - one list item per ``- key: value`` then continuation ``key: value``
      - one level of nested mapping (``budget:`` then indented ``k: v``)
    Not a general YAML parser by design.
    """
    jobs: list[dict] = []
    current: dict | None = None
    nested_key: str | None = None
    nested_indent: int | None = None

    for raw_line in lines:
        line = _strip_comment(raw_line).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if indent <= base_indent and not stripped.startswith("- "):
            # Dedented out of the proactive block.
            break

        if stripped.startswith("- "):
            # New list item.
            if current is not None:
                jobs.append(current)
            current = {}
            nested_key = None
            nested_indent = None
            stripped = stripped[2:].strip()
            if not stripped:
                continue
            # falls through to key: value handling below

        if current is None:
            raise SchemaError(
                "malformed proactive block: mapping before any '-' list item"
            )

        if ":" not in stripped:
            raise SchemaError(f"malformed proactive line (no ':'): {raw_line!r}")

        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()

        if nested_key is not None and nested_indent is not None and indent >= nested_indent:
            current.setdefault(nested_key, {})
            current[nested_key][key] = _coerce_scalar(val)
            continue

        if val == "":
            # Opens a nested mapping (e.g. budget:).
            nested_key = key
            nested_indent = indent + 1
            current[key] = {}
        else:
            nested_key = None
            nested_indent = None
            current[key] = _coerce_scalar(val)

    if current is not None:
        jobs.append(current)
    return jobs


def parse_proactive_from_text(text: str) -> list[dict]:
    """Extract the raw ``proactive:`` list-of-dicts from SKILL.md text."""
    fm = extract_frontmatter_block(text)
    fm_lines = fm.split("\n")
    out_idx = None
    base_indent = 0
    for i, line in enumerate(fm_lines):
        s = _strip_comment(line)
        if re.match(r"^(\s*)proactive\s*:\s*$", s):
            base_indent = len(line) - len(line.lstrip())
            out_idx = i + 1
            break
    if out_idx is None:
        return []
    return _parse_proactive_list(fm_lines[out_idx:], base_indent)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def validate(raw: dict) -> None:
    """Validate a single raw job dict, raising SchemaError on any problem."""
    if not isinstance(raw, dict):
        raise SchemaError(f"job entry must be a mapping, got {type(raw).__name__}")

    for required in ("id", "trigger", "action", "idempotency_key", "cooldown"):
        if required not in raw or raw[required] in (None, ""):
            raise SchemaError(f"job missing required field: {required!r}")

    if not isinstance(raw["id"], str) or not raw["id"].strip():
        raise SchemaError("job.id must be a non-empty string")

    if not isinstance(raw["idempotency_key"], str) or not raw["idempotency_key"].strip():
        raise SchemaError("job.idempotency_key must be a non-empty string")

    deliver = raw.get("deliver", _DEFAULT_DELIVER)
    if deliver not in _VALID_DELIVER:
        raise SchemaError(
            f"job.deliver {deliver!r} invalid; expected one of {sorted(_VALID_DELIVER)}"
        )

    budget = raw.get("budget", {})
    if budget and not isinstance(budget, dict):
        raise SchemaError("job.budget must be a mapping if present")

    cond = raw.get("condition")
    if cond is not None and not isinstance(cond, str):
        raise SchemaError("job.condition must be a string if present")

    # Sub-parsers raise SchemaError on malformed values.
    Trigger.parse(raw["trigger"])
    Action.parse(raw["action"])
    parse_duration_to_seconds(raw["cooldown"])


def _build_job(raw: dict) -> Job:
    validate(raw)
    cooldown_raw = str(raw["cooldown"])
    return Job(
        id=raw["id"].strip(),
        trigger=Trigger.parse(raw["trigger"]),
        action=Action.parse(raw["action"]),
        idempotency_key=raw["idempotency_key"],
        cooldown=cooldown_raw,
        cooldown_seconds=parse_duration_to_seconds(raw["cooldown"]),
        condition=raw.get("condition"),
        budget=dict(raw.get("budget") or {}),
        deliver=raw.get("deliver", _DEFAULT_DELIVER),
    )


def parse_jobs(frontmatter: dict | str) -> list[Job]:
    """Parse + validate the ``proactive:`` jobs.

    Accepts either a dict that already has a ``proactive`` key (the list of
    job dicts), or the raw SKILL.md text (string) — in which case the minimal
    extractor pulls the block out of the YAML frontmatter.

    Raises ``SchemaError`` on the first invalid job and on duplicate ids.
    """
    if isinstance(frontmatter, str):
        raw_jobs = parse_proactive_from_text(frontmatter)
    elif isinstance(frontmatter, dict):
        raw_jobs = frontmatter.get("proactive", []) or []
    else:
        raise SchemaError(
            f"parse_jobs expects str (SKILL.md text) or dict, got "
            f"{type(frontmatter).__name__}"
        )

    if not isinstance(raw_jobs, list):
        raise SchemaError("'proactive' must be a list of job mappings")

    jobs: list[Job] = []
    seen_ids: set[str] = set()
    for entry in raw_jobs:
        job = _build_job(entry)
        if job.id in seen_ids:
            raise SchemaError(f"duplicate job id: {job.id!r}")
        seen_ids.add(job.id)
        jobs.append(job)
    return jobs
