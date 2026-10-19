#!/usr/bin/env python3
"""Shared Claude Code and Codex response-length hook.

Input and output follow the JSON hook protocol. All failures are deliberately
silent so a broken guard can never prevent the host from stopping.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_MAX_WORDS = 150
REWRITE_MARKER = "[Iga response guard: rewrite once]"
DETAIL_OFFER_TTL_SECONDS = 30 * 60

_DETAIL_RE = re.compile(
    r"(?:\b(?:explain|describe|analyze|analyse|review|answer|write|give|provide|show|"
    r"want|need|do)\b.{0,48}\b(?:in\s+detail|detailed|thorough|in[ -]depth|"
    r"deep[ -]dive|step[ -]by[ -]step|more\s+details?)\b)|"
    r"(?:\b(?:detailed|thorough|in[ -]depth|step[ -]by[ -]step)\b.{0,32}"
    r"\b(?:explanation|analysis|review|answer|response|breakdown|instructions?|steps?)\b)|"
    r"(?:\b(?:please\s+)?(?:elaborate|tell\s+me\s+more)\b)|"
    r"(?:^\s*more\s+details?\s*(?:,\s*please)?[.!]?\s*$)|"
    r"(?:^\s*(?:a\s+)?(?:deep[ -]dive|step[ -]by[ -]step)"
    r"\s*(?:,\s*please)?[.!]?\s*$)",
    re.IGNORECASE,
)
_WORD_COUNT_RE = re.compile(
    r"\b(?:at\s+least\s+|about\s+|around\s+|approximately\s+|up\s+to\s+|"
    r"maximum\s+(?:of\s+)?|minimum\s+(?:of\s+)?|in\s+)?"
    r"(\d[\d,]*)\s+words?\b",
    re.IGNORECASE,
)
_NEGATED_DETAIL_RE = re.compile(
    r"\b(?:do\s+not|don[’']t|dont)\s+(?:\w+\s+){0,4}(?:in\s+detail|detailed|"
    r"thorough|in[ -]depth|deep[ -]dive|step[ -]by[ -]step|more\s+details?|"
    r"elaborate|elaboration)\b|"
    r"\b(?:no|without)\s+(?:need\s+for\s+)?(?:a\s+)?(?:detailed|thorough|"
    r"in[ -]depth|deep[ -]dive|step[ -]by[ -]step|elaboration)\b|"
    r"\bnot\s+(?:in\s+detail|in[ -]depth)\b",
    re.IGNORECASE,
)
_SHORT_APPROVAL_RE = re.compile(
    r"^\s*(?:yes|yes(?:,\s*|\s+)please|please|go\s+ahead)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_DETAIL_OFFER_RE = re.compile(
    r"(?:^|[.!?]\s+|\bnext:\s+)(?:(?:do\s+you\s+)?(?:want|need)|"
    r"would\s+you\s+like|"
    r"shall\s+i)\s+(?:the\s+)?(?:more\s+)?(?:detail|details|reasoning|"
    r"explanation|steps)\b[^?]{0,32}\?|"
    r"\bi\s+can\s+(?:also\s+)?(?:explain|elaborate|provide|give|walk\s+through)"
    r".{0,48}\b(?:detail|details|reasoning|steps|more)\b.{0,32}"
    r"\bif\s+(?:useful|you(?:'d|\s+would)?\s+(?:like|want))\b|"
    r"\bask\s+(?:me\s+)?if\s+you\s+want.{0,32}\b(?:detail|details|reasoning|"
    r"explanation|steps|more)\b",
    re.IGNORECASE,
)


_MARKDOWN_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
_HAS_LETTER_OR_DIGIT_RE = re.compile(r"[^\W_]")


def count_words(text: str) -> int:
    """Count words the reader actually reads.

    A markdown link counts as its label, not its URL. Tokens without a letter
    or digit (table pipes, list dashes, rules, bare emphasis markers) are
    markup and do not count. Code and bare URLs still count.
    """
    text = _MARKDOWN_LINK_RE.sub(r" \1 ", text)
    return sum(1 for token in text.split() if _HAS_LETTER_OR_DIGIT_RE.search(token))


def _without_quoted_text(text: str) -> str:
    """Remove code and quoted spans before interpreting user intent."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", " ", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', " ", text)
    text = re.sub(r"(?<!\w)'(?:\\.|[^'\\])*'(?!\w)", " ", text)
    text = re.sub(r"“[^”]*”|‘[^’]*’", " ", text)
    return text


def explicitly_requests_detail(prompt: str, max_words: int) -> bool:
    clean = _without_quoted_text(prompt)
    if _NEGATED_DETAIL_RE.search(clean):
        return False
    if _DETAIL_RE.search(clean):
        return True
    return any(
        int(match.group(1).replace(",", "")) > max_words
        for match in _WORD_COUNT_RE.finditer(clean)
    )


def explicitly_offers_detail(answer: str) -> bool:
    tail = answer[-240:]
    if re.search(
        r"\b(?:do\s+not|don[’']t|dont|cannot|can[’']t|cant|won[’']t|wont)\b"
        r".{0,64}\b(?:detail|details|reasoning|explanation|steps|more)\b",
        tail,
        re.IGNORECASE,
    ):
        return False
    return bool(_DETAIL_OFFER_RE.search(tail))


def _annotation_approves_detail(prompt: str) -> bool:
    match = re.search(
        r"<response-annotations>\s*(.*?)\s*</response-annotations>",
        prompt,
        flags=re.DOTALL,
    )
    if not match or not re.fullmatch(r"\s*(?:## My request:\s*)?", prompt[match.end():]):
        return False
    try:
        annotations = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        return False
    if isinstance(annotations, dict):
        annotations = [annotations]
    if not isinstance(annotations, list) or len(annotations) != 1:
        return False
    annotation = annotations[0]
    if not isinstance(annotation, dict) or annotation.get("annotation", "").strip().lower() != "yes":
        return False
    selected = next(
        (
            annotation[key]
            for key in ("selectedText", "selected_text", "selection", "text")
            if isinstance(annotation.get(key), str)
        ),
        "",
    )
    return explicitly_offers_detail(selected)


def _default_state_dir() -> Path:
    iga_home = os.environ.get("IGA_HOME")
    root = Path(iga_home).expanduser() if iga_home else Path(__file__).resolve().parents[3]
    return root / "state" / "response-guard"


def _connect(state_dir: Path) -> sqlite3.Connection:
    state_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(state_dir / "guard.sqlite3", timeout=0.8)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS session_state (
            host TEXT NOT NULL,
            session_id TEXT NOT NULL,
            detail_allowed INTEGER NOT NULL CHECK (detail_allowed IN (0, 1)),
            detail_offer INTEGER NOT NULL CHECK (detail_offer IN (0, 1)),
            detail_offer_at INTEGER,
            retry_used INTEGER NOT NULL CHECK (retry_used IN (0, 1)),
            PRIMARY KEY (host, session_id)
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(session_state)")}
    if "detail_offer_at" not in columns:
        connection.execute("ALTER TABLE session_state ADD COLUMN detail_offer_at INTEGER")
    return connection


def _guidance(max_words: int, detail_allowed: bool, retry: bool = False) -> str:
    if retry:
        return (
            f"Response guard retry: Rewrite the previous answer once in at most {max_words} "
            "words (markdown symbols and link URLs are not counted). Do not repeat the original answer or redo tools. "
            "Preserve essential facts, code, citations, and any required Next: line."
        )
    limit = (
        "The user explicitly requested detail, so the default word limit is exempt."
        if detail_allowed
        else f"Use no more than {max_words} words (markdown symbols and link URLs are not counted)."
    )
    return (
        f"Response guard: Answer first and use the minimum useful words. {limit} Fully fulfill "
        "the task. Preserve essential facts, code, citations, and any required Next: line. "
        "Offer more detail only when contextually useful, at most once, never as boilerplate."
    )


def _prompt_event(
    connection: sqlite3.Connection,
    host: str,
    session_id: str,
    prompt: str,
    max_words: int,
) -> dict[str, Any]:
    synthetic = prompt.startswith(REWRITE_MARKER)
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT detail_offer, detail_offer_at, retry_used FROM session_state "
        "WHERE host = ? AND session_id = ?",
        (host, session_id),
    ).fetchone()

    if synthetic:
        # Codex re-submits a Stop reason as a user prompt. Never reset the
        # already-used retry or mistake the marker for genuine user intent.
        if row is None or not bool(row[2]):
            connection.rollback()
            return {}
        detail_allowed = False
    else:
        prior_offer = bool(
            row
            and row[0]
            and row[1]
            and time.time() - row[1] <= DETAIL_OFFER_TTL_SECONDS
        )
        detail_allowed = explicitly_requests_detail(prompt, max_words) or (
            prior_offer
            and (
                bool(_SHORT_APPROVAL_RE.fullmatch(prompt))
                or _annotation_approves_detail(prompt)
            )
        )
        connection.execute(
            """
            INSERT INTO session_state(
                host, session_id, detail_allowed, detail_offer, detail_offer_at, retry_used
            )
            VALUES (?, ?, ?, 0, NULL, 0)
            ON CONFLICT(host, session_id) DO UPDATE SET
                detail_allowed = excluded.detail_allowed,
                detail_offer = 0,
                detail_offer_at = NULL,
                retry_used = 0
            """,
            (host, session_id, int(detail_allowed)),
        )
    connection.commit()
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": _guidance(max_words, detail_allowed, retry=synthetic),
        }
    }


def _stop_event(
    connection: sqlite3.Connection,
    host: str,
    session_id: str,
    answer: str,
    max_words: int,
    allow_block: bool = True,
) -> dict[str, str]:
    offer = explicitly_offers_detail(answer)
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT detail_allowed, retry_used FROM session_state WHERE host = ? AND session_id = ?",
        (host, session_id),
    ).fetchone()
    if row is None:
        connection.rollback()
        return {}

    detail_allowed, retry_used = map(bool, row)
    should_block = (
        allow_block
        and count_words(answer) > max_words
        and not detail_allowed
        and not retry_used
    )
    connection.execute(
        "UPDATE session_state SET detail_offer = ?, detail_offer_at = ?, retry_used = ? "
        "WHERE host = ? AND session_id = ?",
        (
            int(offer),
            int(time.time()) if offer else None,
            int(retry_used or should_block),
            host,
            session_id,
        ),
    )
    connection.commit()

    if not should_block:
        return {}
    return {
        "decision": "block",
        "reason": (
            f"{REWRITE_MARKER} Rewrite the previous answer in at most {max_words} "
            "words (markdown symbols and link URLs are not counted). Do not repeat the original answer or redo tools. "
            "Preserve essential facts, code, citations, and any required Next: line."
        ),
    }


def process_event(
    payload: object,
    *,
    host: str,
    state_dir: Path,
    max_words: int = DEFAULT_MAX_WORDS,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or max_words < 1:
        return {}
    event = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {}

    with _connect(state_dir) as connection:
        if event == "UserPromptSubmit":
            prompt = payload.get("prompt")
            if not isinstance(prompt, str):
                return {}
            return _prompt_event(connection, host, session_id, prompt, max_words)
        if event == "Stop":
            answer = payload.get("last_assistant_message")
            if not isinstance(answer, str) or not answer:
                return {}
            return _stop_event(
                connection,
                host,
                session_id,
                answer,
                max_words,
                allow_block=payload.get("stop_hook_active") is not True,
            )
    return {}


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("IGA_RESPONSE_GUARD_OFF") == "1":
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, choices=("claude", "codex"))
    parser.add_argument("--state-dir", type=Path, default=_default_state_dir())
    parser.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS)
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        output = process_event(
            payload, host=args.host, state_dir=args.state_dir, max_words=args.max_words
        )
        if output:
            json.dump(output, sys.stdout, separators=(",", ":"))
            sys.stdout.write("\n")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
