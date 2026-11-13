#!/usr/bin/env python3
"""Read the conversations of this Iga workspace across sessions and harnesses.

The user talks to one Iga, however many sessions they open. Each harness keeps
its own transcript store, so a fact said in one session is invisible to the
next unless something reads it. This engine reads those stores (never writes)
and prints the readable part: user and assistant text with local times. Tool
calls and tool results are dropped, because that is where the bulk and most
credentials live, and anything that still looks like a credential is masked.

Every harness is reported as `ok`, `not installed`, or a `⚠️ ... unavailable`
line. A store that cannot be read is never skipped silently.

Usage:
  sessions.py --list  [--day YYYY-MM-DD | --since ISO] [--exclude-current] [--json]
  sessions.py --day YYYY-MM-DD [--exclude-current] [--out-dir DIR] [--grep WORD]
  sessions.py --session ID [--grep WORD]

Stdlib only. The workspace is $IGA_HOME, or the clone this file lives in.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Iterable, Iterator

HARNESSES = ("claude", "codex")
# Read by default. Codex transcripts are opt-in: a user may keep Codex for
# unrelated work and not want it reviewed. IGA_SESSIONS_HARNESSES="claude codex" adds it.
DEFAULT_HARNESSES = ("claude",)
# Where a harness tells a command which session it runs in. IGA_SESSION_ID lets
# a harness without such a variable (or a wrapper script) supply it.
CURRENT_SESSION_ENV = ("IGA_SESSION_ID", "CLAUDE_CODE_SESSION_ID")
SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}"
    r"|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+)?"
    r"|[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s@/]+@[^\s]+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/-]{20,}=*"
    r"|(?i:\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[=:]\s*)[^\s,;]{8,}",
    re.DOTALL,
)
_COMMAND_RE = re.compile(
    r"<command-name>\s*/?([^<\s]+)\s*</command-name>(?:.*?<command-args>(.*?)</command-args>)?",
    re.DOTALL,
)
# Blocks a harness puts in the user's turn instead of something the user typed,
# as seen in real transcripts. The list is a denylist on purpose: an unknown
# wrapper is kept, so a harness change shows up as noise, never as lost words.
_HARNESS_PREFIXES = (
    "<system-reminder>", "<local-command-", "<task-notification>",
    "Stop hook feedback", "[Request interrupted",
    "<environment_context>", "<user_instructions>", "<codex_internal_context>",
    "<guardian_tool_descriptions>", "<heartbeat>", "<in-app-browser-context>",
    "<external_browser_tabs>", "<external_codex_apps_", "<turn_aborted>", "<image", "<skill>",
    "# AGENTS.md instructions",
)
_LEADING_COMMENTS_RE = re.compile(r"\A(?:\s*<!--.*?-->)+\s*", re.DOTALL)


def mask(text: str) -> str:
    return SECRET_RE.sub("[secret]", text)


@dataclass
class Turn:
    when: datetime
    who: str  # "User" or "Iga"
    text: str


@dataclass
class Session:
    harness: str
    id: str
    path: Path
    title: str = ""
    turns: list[Turn] = field(default_factory=list)

    @property
    def user_turns(self) -> int:
        return sum(1 for t in self.turns if t.who == "User")

    def label(self, limit: int = 100) -> str:
        """Session title, else the first thing the user typed that is not a command."""
        text = self.title
        if not text:
            users = [t.text for t in self.turns if t.who == "User"]
            text = next((u for u in users if not u.startswith("/")), users[0] if users else "")
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


@dataclass
class Source:
    harness: str
    status: str  # "ok", "absent" (harness not installed), "unavailable"
    detail: str = ""
    sessions: list[Session] = field(default_factory=list)
    unreadable: int = 0

    def line(self) -> str:
        if self.status == "absent":
            return f"{self.harness}: not installed ({self.detail})"
        if self.status == "unavailable":
            return f"⚠️ {self.harness} sessions unavailable ({self.detail})"
        tail = f"; ⚠️ {self.unreadable} {self.harness} file(s) unreadable, not reviewed" if self.unreadable else ""
        return f"{self.harness}: ok, {len(self.sessions)} session(s){tail}"


def workspace_root() -> Path:
    env = os.environ.get("IGA_HOME")
    return Path(env).expanduser() if env else Path(__file__).resolve().parents[3]


def enabled_harnesses() -> list[str]:
    """The harnesses named in $IGA_SESSIONS_HARNESSES, else DEFAULT_HARNESSES."""
    names = os.environ.get("IGA_SESSIONS_HARNESSES", "").replace(",", " ").split()
    return [h for h in names if h in HARNESSES] or list(DEFAULT_HARNESSES)


def claude_slug(path: Path) -> str:
    """Claude Code names a project's transcript folder after its path."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _same_dir(a: str | os.PathLike, b: str | os.PathLike) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _parse_ts(value: object, tz: tzinfo | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return None


def _records(path: Path, seen: list[int]) -> Iterator[dict]:
    """Yield the JSON objects of a transcript; seen[0] counts its non-empty lines."""
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.strip():
                continue
            seen[0] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _text_parts(content: object, kinds: tuple[str, ...]) -> list[str]:
    if isinstance(content, str):
        return [content]
    parts = []
    for item in content if isinstance(content, list) else []:
        if isinstance(item, dict) and item.get("type") in kinds and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return parts


def _user_text(parts: Iterable[str]) -> str:
    """Join what the user typed, dropping blocks the harness wrapped around it."""
    kept = []
    for part in parts:
        part = part.strip()
        if part.startswith("<command-"):
            match = _COMMAND_RE.search(part)
            if match:
                kept.append(f"/{match.group(1)} {(match.group(2) or '').strip()}".strip())
            continue
        part = _LEADING_COMMENTS_RE.sub("", part)  # reply markers some clients prepend
        if part and not part.startswith(_HARNESS_PREFIXES):
            kept.append(part)
    return "\n".join(kept)


def read_claude(path: Path, tz: tzinfo | None = None) -> Session | None:
    """A Claude Code transcript: one JSON record per line, `type` user or assistant.

    Returns None when the file has content but none of it looks like a transcript.
    """
    session = Session("claude", path.stem, path)
    lines, known = [0], False
    for record in _records(path, lines):
        kind = record.get("type")
        known = known or isinstance(kind, str)
        if kind == "custom-title" and isinstance(record.get("customTitle"), str):
            session.title = mask(record["customTitle"])
        if kind not in ("user", "assistant") or record.get("isMeta") or record.get("isSidechain"):
            continue
        when = _parse_ts(record.get("timestamp"), tz)
        message = record.get("message")
        if when is None or not isinstance(message, dict):
            continue
        parts = _text_parts(message.get("content"), ("text",))
        text = _user_text(parts) if kind == "user" else "\n".join(p.strip() for p in parts if p.strip())
        if text:
            session.turns.append(Turn(when, "User" if kind == "user" else "Iga", mask(text)))
    return session if known or not lines[0] else None


def read_codex(path: Path, root: Path, tz: tzinfo | None = None) -> Session | None | bool:
    """A Codex rollout: `session_meta` first, then `response_item` messages.

    Returns False for a session of another folder, a subagent thread or an
    empty file (not ours, not an error) and None when the file has content
    that is not a rollout we can read.
    """
    session: Session | None = None
    lines = [0]
    for record in _records(path, lines):
        payload = record.get("payload")
        if not isinstance(payload, dict):
            if session is None:
                return None
            continue
        if record.get("type") == "session_meta":
            cwd = payload.get("cwd")
            if isinstance(payload.get("source"), dict) or not isinstance(cwd, str) or not _same_dir(cwd, root):
                return False
            session = Session("codex", str(payload.get("id") or path.stem), path)
            continue
        if session is None:
            return None
        if record.get("type") != "response_item" or payload.get("type") != "message":
            continue
        role = payload.get("role")
        when = _parse_ts(record.get("timestamp"), tz)
        if when is None or role not in ("user", "assistant"):
            continue
        if role == "user":
            text = _user_text(_text_parts(payload.get("content"), ("input_text",)))
        elif payload.get("phase") in (None, "final_answer"):  # skip progress commentary
            text = "\n".join(p.strip() for p in _text_parts(payload.get("content"), ("output_text",)) if p.strip())
        else:
            text = ""
        if text:
            session.turns.append(Turn(when, "User" if role == "user" else "Iga", mask(text)))
    if session is None:
        return None if lines[0] else False
    return session


def _claude_source(root: Path, home: Path, floor: float, tz: tzinfo | None, match: str = "") -> Source:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude")
    if not base.is_dir():
        return Source("claude", "absent", "no Claude Code data directory")
    store = base / "projects" / claude_slug(root)
    if not store.is_dir():
        if (base / "projects").is_dir():
            return Source("claude", "ok")  # installed, never used in this workspace
        return Source("claude", "unavailable", "no projects folder in the Claude Code data directory")
    source = Source("claude", "ok")
    for path in sorted(store.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
        if path.stat().st_mtime < floor or match not in path.stem:
            continue
        session = read_claude(path, tz)
        if session is None:
            source.unreadable += 1
        else:
            source.sessions.append(session)
    return source


def _codex_source(root: Path, home: Path, floor: float, tz: tzinfo | None, match: str = "") -> Source:
    base = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    if not base.is_dir():
        return Source("codex", "absent", "no Codex data directory")
    stores = [d for d in (base / "sessions", base / "archived_sessions") if d.is_dir()]
    if not stores:
        return Source("codex", "unavailable", "no sessions folder in the Codex data directory")
    source = Source("codex", "ok")
    paths = [p for store in stores for p in store.rglob("*.jsonl")]
    for path in sorted(paths, key=lambda p: p.stat().st_mtime):
        if path.stat().st_mtime < floor or match not in path.stem:
            continue
        session = read_codex(path, root, tz)
        if session is None:
            source.unreadable += 1
        elif session is not False:
            source.sessions.append(session)
    return source


_READERS = {"claude": _claude_source, "codex": _codex_source}


def collect(
    root: Path,
    *,
    day: str | None = None,
    since: datetime | None = None,
    exclude: Iterable[str] = (),
    harnesses: Iterable[str] | None = None,
    match: str = "",
    home: Path | None = None,
    tz: tzinfo | None = None,
) -> list[Source]:
    """Sessions of `root` with at least one turn on `day` (local) or after `since`.

    `match` limits the read to transcripts whose file name contains it (a
    session id or its prefix), so one session can be opened without a scan.
    """
    home = home or Path.home()
    if day:
        floor = datetime.fromisoformat(day).replace(tzinfo=tz).astimezone(tz).timestamp()
    elif since:
        floor = since.timestamp()
    else:
        floor = 0.0
    skip = {e for e in exclude if e}
    sources = []
    for harness in harnesses or enabled_harnesses():
        try:
            source = _READERS[harness](root, home, floor, tz, match)
        except OSError as exc:
            source = Source(harness, "unavailable", f"{type(exc).__name__} reading its transcript store")
        kept, copied = [], set()
        for session in source.sessions:
            # A resumed or forked session starts as a copy of its parent. Keep each
            # turn once (in the older file), also when the current session is the copy.
            fresh = []
            for turn in session.turns:
                key = (turn.when, turn.who, turn.text)
                if key not in copied:
                    copied.add(key)
                    fresh.append(turn)
            if session.id in skip or str(session.path) in skip:
                continue
            session.turns = [
                t for t in fresh
                if (not day or t.when.strftime("%Y-%m-%d") == day) and (not since or t.when >= since)
            ]
            if session.turns:
                kept.append(session)
        source.sessions = sorted(kept, key=lambda s: s.turns[0].when)
        sources.append(source)
    return sources


def index_line(session: Session) -> str:
    first, last = session.turns[0].when, session.turns[-1].when
    return (f"{first:%H:%M}-{last:%H:%M} {session.harness} {session.id[:8]}, "
            f"{session.user_turns} user turn(s): {session.label()}")


def render(session: Session, max_chars: int = 1500, grep: str | None = None) -> list[str]:
    rows = []
    for turn in session.turns:
        if grep and grep.lower() not in turn.text.lower():
            continue
        text = turn.text if len(turn.text) <= max_chars else turn.text[:max_chars] + " [...]"
        rows.append(f"{turn.when:%Y-%m-%d %H:%M} {turn.who}: {text}")
    return rows


def reviewable(sources: list[Source]) -> bool:
    """False when no harness store could be read at all."""
    return any(s.status == "ok" for s in sources)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", help="local date YYYY-MM-DD")
    ap.add_argument("--since", help="ISO time; sessions with turns after it (for catching up since the last diary entry)")
    ap.add_argument("--session", help="one session id, or the 8-character prefix the index shows")
    ap.add_argument("--exclude", action="append", default=[], help="session id or transcript path to leave out (the current one)")
    ap.add_argument("--exclude-current", action="store_true",
                    help="leave out the session running this command, when the harness exposes its id")
    ap.add_argument("--list", action="store_true", help="one line per session instead of the conversation")
    ap.add_argument("--json", action="store_true", help="with --list: machine-readable output")
    ap.add_argument("--grep", help="only turns containing this text (case-insensitive)")
    ap.add_argument("--max-chars", type=int, default=1500, help="cut longer turns (default 1500)")
    ap.add_argument("--out-dir", type=Path, help="write one <harness>-<id>.txt per session here")
    ap.add_argument("--harness", action="append", choices=HARNESSES, help="limit to these (default: claude, or $IGA_SESSIONS_HARNESSES)")
    ap.add_argument("--root", type=Path, help="workspace folder (default: $IGA_HOME or this clone)")
    a = ap.parse_args(argv)

    if not (a.day or a.since or a.session):
        ap.error("one of --day, --since or --session is required")
    try:
        since = datetime.fromisoformat(a.since.replace("Z", "+00:00")).astimezone() if a.since else None
    except ValueError:
        ap.error("--since must be an ISO date or time")
    root = (a.root or workspace_root()).expanduser()
    current_unknown = False
    if a.exclude_current:
        current = next((os.environ[k] for k in CURRENT_SESSION_ENV if os.environ.get(k)), "")
        a.exclude.append(current)
        current_unknown = not current

    sources = collect(root, day=a.day, since=since, exclude=a.exclude, harnesses=a.harness,
                      match=a.session or "")
    sessions = [s for src in sources for s in src.sessions]
    if a.session:
        sessions = [s for s in sessions if s.id == a.session or s.id.startswith(a.session)]

    if a.list and a.json:
        json.dump({
            "sources": [{"harness": s.harness, "status": s.status, "detail": s.detail,
                         "unreadable": s.unreadable} for s in sources],
            "sessions": [{"harness": s.harness, "id": s.id, "path": str(s.path),
                          "first": s.turns[0].when.isoformat(), "last": s.turns[-1].when.isoformat(),
                          "user_turns": s.user_turns, "label": s.label()} for s in sessions],
        }, sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
        return 0

    for source in sources:
        print(source.line())
    if current_unknown:
        print("note: this harness does not expose the current session's id, so this session is included below")
    if not reviewable(sources):
        print("⚠️ other sessions unavailable (no transcript store could be read)")
    if a.out_dir:
        a.out_dir.mkdir(parents=True, exist_ok=True)
    for session in sessions:
        if a.list:
            print(index_line(session))
            continue
        rows = render(session, a.max_chars, a.grep)
        if not rows:
            continue
        header = f"=== {session.harness} session {session.id} ({len(rows)} turns)"
        if a.out_dir:
            out = a.out_dir / f"{session.harness}-{session.id}.txt"
            out.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
            os.chmod(out, 0o600)
            print(f"{out}\t{len(rows)}")
        else:
            print(header)
            print("\n".join(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
