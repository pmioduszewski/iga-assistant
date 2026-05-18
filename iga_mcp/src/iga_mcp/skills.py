"""
Skill-contributed MCP tools layer (IgaMCP v1).

Each skill that wants to expose typed MCP tools adds an entry to SKILL_TOOLS
below (the declarative registry). In v1 the registry is explicit/handwritten.

TODO (v2 auto-discovery): scan skills/<name>/mcp_tools.py across IGA_HOME,
import each, and call register(mcp) automatically — no manual entry needed.
For now every skill just adds its tool callables to this module and registers
them in server.py.

State resolution:
  IGA_STATE_DIR  → used verbatim if set (tests override this)
  else           → <IGA_HOME>/state
Both are expanduser'd so ~ works in env values.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Re-use the same IGA_HOME resolution logic as server.py (no import cycle:
# server imports skills, not the reverse).
IGA_HOME = Path(os.environ.get("IGA_HOME", str(Path.home() / "Gaia"))).expanduser()

# How long (seconds) to wait for an engine subprocess before giving up.
_DEFAULT_TIMEOUT = 30


def state_root() -> str:
    """
    Resolve the substrate state directory.

    Preference order:
      1. IGA_STATE_DIR env var (tests set this to a temp dir)
      2. <IGA_HOME>/state

    Always returns an absolute, expanduser'd string so the engine subprocess
    never sees a naked ~ (some subprocess PATH environments don't expand it).
    """
    explicit = os.environ.get("IGA_STATE_DIR", "").strip()
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    return str((IGA_HOME / "state").expanduser().resolve())


def _run(engine_relpath: str, args: list[str], *, timeout: int = _DEFAULT_TIMEOUT) -> object:
    """
    Run a skill engine script as a subprocess.

    engine_relpath  — path relative to IGA_HOME, e.g.
                      "skills/habit-tracker/engine/record.py"
    args            — extra CLI arguments (after the script path)
    timeout         — seconds before raising TimeoutError

    Returns:
      - If "--json" is in args: parsed JSON object (dict or list).
      - Otherwise: stripped stdout string.

    Raises RuntimeError on nonzero exit or on timeout.
    """
    script = IGA_HOME / engine_relpath
    cmd = [sys.executable, str(script)] + args
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Engine timed out after {timeout}s: {engine_relpath}"
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Engine {engine_relpath} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    output = proc.stdout.strip()
    if "--json" in args:
        return json.loads(output)
    return output


# ---------------------------------------------------------------------------
# Habit-tracker tools
# ---------------------------------------------------------------------------

_HABIT_RECORD = "skills/habit-tracker/engine/record.py"
_HABIT_SUMMARY = "skills/habit-tracker/engine/summary.py"


class HabitNotFound(Exception):
    """No habit entity matched the query. Carries the verbatim list of
    available habit names so the model can immediately retry with a correct
    one. NEVER raised as a fallback that creates/guesses a habit."""

    def __init__(self, query: str, available: list[str]):
        self.query = query
        self.available = available
        names = ", ".join(repr(n) for n in available) if available else "(none)"
        super().__init__(
            f"no habit matched {query!r}. Available habits: {names}"
        )


def _habit_substrate_path() -> Path:
    """`<state_root>/substrates/habit-tracker.json` — the SAME path the
    frozen substrate engine writes (state/substrates/<kind>.json)."""
    return Path(state_root()) / "substrates" / "habit-tracker.json"


def _load_habit_entities() -> list[dict]:
    """Read the habit substrate JSON read-only and return the entity list as
    [{"id","name"}]. Missing/unreadable/malformed file → []. Never mutates
    any engine state — pure parse."""
    path = _habit_substrate_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for e in doc.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        if eid is None:
            continue
        out.append({"id": str(eid), "name": str(e.get("name", ""))})
    return out


def _normalize_habit(s: str) -> str:
    """strip, lower, collapse whitespace, strip surrounding punctuation so
    'push ups', 'Push-Ups', ' push-ups ' all map to the same token."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    # strip surrounding punctuation (anything not alnum/space) from both ends
    s = re.sub(r"^[^0-9a-z ]+|[^0-9a-z ]+$", "", s)
    # treat internal separators (-, _, etc.) as spaces so "push-ups" == "push ups"
    s = re.sub(r"[^0-9a-z]+", " ", s).strip()
    return s


def resolve_habit(query: str) -> str:
    """Resolve a free-text habit query to a substrate entity **id**.

    Precedence (NEVER creates or guesses — ambiguous/none raises HabitNotFound
    whose message lists available habit names verbatim):
      (a) exact id match
      (b) exact case-insensitive name match
      (c) unique case-insensitive *normalized* name match
      (d) exactly one entity whose normalized name *contains* the normalized
          query as a whitespace-delimited token
    """
    entities = _load_habit_entities()
    names = [e["name"] for e in entities]

    q_raw = (query or "").strip()

    # (a) exact id match
    for e in entities:
        if e["id"] == q_raw:
            return e["id"]

    # (b) exact case-insensitive name match
    q_lower = q_raw.lower()
    ci = [e for e in entities if e["name"].lower() == q_lower]
    if len(ci) == 1:
        return ci[0]["id"]
    if len(ci) > 1:
        raise HabitNotFound(query, names)

    # (c) unique normalized name match
    q_norm = _normalize_habit(q_raw)
    if q_norm:
        norm = [e for e in entities if _normalize_habit(e["name"]) == q_norm]
        if len(norm) == 1:
            return norm[0]["id"]
        if len(norm) > 1:
            raise HabitNotFound(query, names)

        # (d) exactly one entity whose normalized name contains q_norm as a token
        q_tokens = q_norm.split()
        contains = [
            e for e in entities
            if _token_subseq(q_tokens, _normalize_habit(e["name"]).split())
        ]
        if len(contains) == 1:
            return contains[0]["id"]

    raise HabitNotFound(query, names)


def _token_subseq(needle: list[str], haystack: list[str]) -> bool:
    """True iff every token of `needle` (in order, contiguous) appears as a
    run inside `haystack`. Used for the (d) 'contains as a token' fallback."""
    if not needle:
        return False
    n = len(needle)
    for i in range(len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            return True
    return False


def habit_log(habit: str, op: str = "add", date: str = "today", amount: int | None = None) -> dict:
    """
    Core implementation for iga_habit_log. Separated so tests can call directly.

    Resolves the free-text `habit` to a substrate entity **id** first
    (record.py requires the id and does NOT auto-create). On an unresolvable
    name returns a structured {"ok": False, "error", "available"} dict instead
    of raising — so the MCP never crashes and the model can immediately retry
    with a valid name (see iga_habit_list).

    op ∈ {"add", "remove", "set"}
    "set" requires amount (int >= 0).
    """
    from datetime import date as _date

    op = op.lower().strip()
    if op not in {"add", "remove", "set"}:
        raise ValueError(f"op must be 'add', 'remove', or 'set'; got {op!r}")
    if op == "set" and amount is None:
        raise ValueError("op='set' requires amount")

    try:
        entity_id = resolve_habit(habit)
    except HabitNotFound as exc:
        return {
            "ok": False,
            "error": str(exc),
            "available": exc.available,
        }

    resolved_date = _date.today().isoformat() if date.strip().lower() == "today" else date.strip()

    args = [
        "--state-dir", state_root(),
        "--habit", entity_id,
        "--date", resolved_date,
    ]
    if op == "add":
        args.append("--add")
    elif op == "remove":
        args.append("--remove")
    elif op == "set":
        args += ["--set-amount", str(amount)]

    output = _run(_HABIT_RECORD, args)
    return {
        "ok": True,
        "habit": habit,
        "habit_id": entity_id,
        "op": op,
        "date": resolved_date,
        "output": output,
    }


def habit_summary() -> dict:
    """Core implementation for iga_habit_summary."""
    return _run(_HABIT_SUMMARY, ["--json"])


def habit_list() -> dict:
    """Core implementation for iga_habit_list.

    Returns {"habits": [{"id","name","done_today"?}]}. ids come from the
    substrate (the source of truth record.py needs); done_today is merged in
    by name from summary.py when that field is exposed there, best-effort."""
    entities = _load_habit_entities()
    done_by_name: dict[str, bool] = {}
    try:
        summary = habit_summary()
        if isinstance(summary, dict):
            for h in summary.get("habits", []) or []:
                if not isinstance(h, dict):
                    continue
                nm = h.get("name")
                if nm is None:
                    continue
                for k in ("done_today", "doneToday", "completed_today"):
                    if k in h:
                        done_by_name[str(nm)] = bool(h[k])
                        break
    except Exception:
        # summary is best-effort enrichment only; never fail the listing.
        done_by_name = {}

    habits: list[dict] = []
    for e in entities:
        item = {"id": e["id"], "name": e["name"]}
        if e["name"] in done_by_name:
            item["done_today"] = done_by_name[e["name"]]
        habits.append(item)
    return {"habits": habits}


# ---------------------------------------------------------------------------
# Mood-tracker tools
# ---------------------------------------------------------------------------

_MOOD_RECORD = "skills/mood-tracker/engine/record.py"
_MOOD_SUMMARY = "skills/mood-tracker/engine/summary.py"
_MOOD_LEXICON = IGA_HOME / "skills" / "mood-tracker" / "engine" / "lexicon.py"


class EmotionNotInLexicon(Exception):
    """The emotion name is not in the canonical RULER lexicon. Carries up to
    ~5 nearest canonical suggestions so the model can retry."""

    def __init__(self, name: str, suggestions: list[str]):
        self.name = name
        self.suggestions = suggestions
        hint = ", ".join(suggestions) if suggestions else "(no close match)"
        super().__init__(
            f"{name!r} is not a known emotion. Did you mean: {hint}?"
        )


_lex_cache = None


def _load_lexicon():
    """Import the frozen mood-tracker lexicon module by path. Cached. We
    consume its own normalize/lookup + EMOTIONS data — never reimplement it."""
    global _lex_cache
    if _lex_cache is not None:
        return _lex_cache
    spec = importlib.util.spec_from_file_location(
        "mt_lexicon", _MOOD_LEXICON
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load lexicon at {_MOOD_LEXICON}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _lex_cache = mod
    return mod


def validate_emotion(name: str) -> str:
    """Return the canonical normalized emotion key if `name` is in the
    lexicon; else raise EmotionNotInLexicon with up to 5 nearest canonical
    emotions. Uses the lexicon's OWN normalize/lookup + EMOTIONS data."""
    lex = _load_lexicon()
    norm = lex.normalize(name)
    if lex.lookup(name) is not None:
        return norm

    import difflib

    candidates = list(lex.EMOTIONS.keys())
    # closest by edit distance, then a substring/prefix pass as backstop
    suggestions = difflib.get_close_matches(norm, candidates, n=5, cutoff=0.5)
    if not suggestions:
        suggestions = [c for c in candidates if norm and norm in c][:5]
    if not suggestions:
        # Last resort: a small spread of common canonical anchors so the
        # model always gets a usable retry vocabulary (never empty).
        suggestions = [
            e for e in ("happy", "calm", "anxious", "sad", "tired")
            if e in lex.EMOTIONS
        ]
    raise EmotionNotInLexicon(name, suggestions)


def mood_log(
    emotion: str,
    note: str = "",
    at: str = "now",
    people: str = "",
    places: str = "",
    events: str = "",
) -> dict:
    """
    Core implementation for iga_mood_log.

    at: "now" maps to today's date in YYYY-MM-DD (the engine accepts YYYY-MM-DD
    and YYYY-MM-DDTHH:MM). Empty optional strings are omitted from argv.
    """
    from datetime import date as _date

    # Validate every emotion (semicolons separate multiple) against the
    # canonical lexicon BEFORE invoking the engine. On any unknown emotion
    # return a structured dict (don't crash the MCP) so the model can retry.
    parts = [p.strip() for p in str(emotion).split(";") if p.strip()]
    if not parts:
        return {
            "ok": False,
            "error": "no emotion provided",
            "suggestions": [],
        }
    for part in parts:
        try:
            validate_emotion(part)
        except EmotionNotInLexicon as exc:
            return {
                "ok": False,
                "error": str(exc),
                "suggestions": exc.suggestions,
            }

    resolved_at = _date.today().isoformat() if at.strip().lower() == "now" else at.strip()

    args = [
        "--state-dir", state_root(),
        "--emotion", emotion,
        "--at", resolved_at,
    ]
    if note.strip():
        args += ["--note", note.strip()]
    if people.strip():
        args += ["--people", people.strip()]
    if places.strip():
        args += ["--places", places.strip()]
    if events.strip():
        args += ["--events", events.strip()]

    output = _run(_MOOD_RECORD, args)
    return {"ok": True, "emotion": emotion, "at": resolved_at, "output": output}


def mood_summary(days: int = 14) -> dict:
    """Core implementation for iga_mood_summary."""
    return _run(_MOOD_SUMMARY, ["--json", "--days", str(days)])


# ---------------------------------------------------------------------------
# Declarative registry
# ---------------------------------------------------------------------------
# v1: explicit map.  v2: auto-discover skills/<name>/mcp_tools.py.
#
# Each entry describes one skill's tools so future tooling can enumerate
# them without importing.  The actual callables live above.
#
# TODO(v2): replace this dict with a loader that walks IGA_HOME/skills/*/
# looking for mcp_tools.py, imports each, and calls register(mcp_instance).

SKILL_TOOLS: dict[str, list[str]] = {
    "habit-tracker": ["habit_log", "habit_summary", "habit_list"],
    "mood-tracker": ["mood_log", "mood_summary"],
}
