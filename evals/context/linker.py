"""Save-time supersede linker.

When a note is saved, find the older notes closest to it and ask the provider's
cheap model which of them the new note makes outdated. Those get a supersede
link, so later searches never return them as current. The slow model call is
paid once at save time (or in a background pass), never while answering.
"""

from __future__ import annotations

import json

LINK_SYSTEM = "You maintain a personal notes store. Reply with JSON only."

LINK_PROMPT = """A NEW note was just saved. Below are OLDER notes that look related.

Which older notes does the new note make outdated? An older note is outdated only when BOTH notes state
the SAME specific fact, status, rule or decision and the new note gives a newer, changed or corrected
version of it (for example planned -> done, broken -> fixed, pending -> approved, "correction: X is
wrong"), or restates the same rule, so reading the older note as current would mislead.

Never mark an older note outdated when:
- it is a rule, preference or instruction and the new note does not explicitly change or cancel THAT rule
  (fixing the reason behind a rule does not cancel the rule)
- it is a periodic or journal-style note (weekly notes, diary, log, meeting notes): those record what was
  true then and are never replaced by a later one
- it is about a related topic, a different person, client, year or event with a similar name
- it still holds other facts that are true and are not covered by the new note

For each outdated note, quote the old fact from the OLDER note and the new fact from the NEW note,
copied word for word (short quotes, 3 to 20 words).

NEW ({new_date}):
{new_text}

OLDER:
{older}

Reply exactly: {{"outdated": [{{"n": <number>, "old_quote": "...", "new_quote": "..."}}]}}
Reply {{"outdated": []}} when none is outdated."""


def _norm(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


def quote_in(quote: str, text: str) -> bool:
    """True when the quote really appears in the text (case/punctuation-insensitive)."""
    q = _norm(quote)
    return len(q.split()) >= 3 and q in _norm(text)


def build_prompt(new_date: str, new_text: str, older: list[tuple[str, str]], max_chars: int = 900) -> str:
    """older: [(date, text)] in candidate order."""
    lines = [
        f"[{i}] ({date}) {' '.join(text.split())[:max_chars]}" for i, (date, text) in enumerate(older, 1)
    ]
    return LINK_PROMPT.format(
        new_date=new_date, new_text=" ".join(new_text.split())[:1500], older="\n".join(lines)
    )


def ask(complete, new_date, new_text, older) -> list[dict]:
    """One model call. Returns the model's claimed links whose quotes really appear
    in the notes: [{"i": <0-based index>, "old_quote": ..., "new_quote": ...}]."""
    if not older:
        return []
    raw = complete(build_prompt(new_date, new_text, older), LINK_SYSTEM)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        return []
    try:
        items = json.loads(raw[start : end + 1]).get("outdated", [])
    except (ValueError, TypeError, AttributeError):
        return []
    out = []
    for it in items:
        try:
            i = int(it["n"]) - 1
        except (KeyError, TypeError, ValueError):
            continue
        oq, nq = str(it.get("old_quote", "")), str(it.get("new_quote", ""))
        if 0 <= i < len(older) and quote_in(oq, older[i][1]) and quote_in(nq, new_text):
            out.append({"i": i, "old_quote": oq, "new_quote": nq})
    return out


SHORT_NOTE_WORDS = 60
MIN_COVERAGE = 0.5  # a long note is retired only when the changed facts are most of it


def decide(items, older) -> list[int]:
    """Keep a link only when the changed facts are what the older note is about.

    A link retires the WHOLE older note. When a long, multi-topic note has one
    resolved item, the rest of it is still true, so it must stay current.
    """
    keep = []
    for i in sorted({it["i"] for it in items}):
        words = len(_norm(older[i][1]).split())
        quoted = sum(len(_norm(it["old_quote"]).split()) for it in items if it["i"] == i)
        if words <= SHORT_NOTE_WORDS or quoted / max(words, 1) >= MIN_COVERAGE:
            keep.append(i)
    return keep


def judge_outdated(complete, new_date, new_text, older) -> list[int]:
    """complete: callable(prompt, system) -> str. Returns 0-based indexes into `older`."""
    return decide(ask(complete, new_date, new_text, older), older)
