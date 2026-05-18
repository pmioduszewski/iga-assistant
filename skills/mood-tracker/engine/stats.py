"""Deterministic mood aggregates — pure, no I/O, no clock (pass ``today``).

The psychology layer Iga reasons from: valence/energy means, dominant
quadrant, recent trend, top emotions, logging consistency, and the
contextual correlation (which people/places/events co-occur with the
most-unpleasant logs). Same determinism contract as the habit engine —
same substrate + same ``today`` → byte-identical output. Stdlib only.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(modname: str, filename: str):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_q = _load("mt_quadrant", "quadrant.py")


def _d(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _in_window(entries, today: date, days: int):
    start = date.fromordinal(today.toordinal() - (days - 1))
    out = []
    for e in entries:
        d = _d(e.date)
        if d is not None and start <= d <= today:
            out.append(e)
    return out


def _mean(xs) -> float | None:
    xs = [x for x in xs]
    return round(sum(xs) / len(xs), 3) if xs else None


def day_valence_levels(entries, *, today: date, window_days: int) -> list[dict]:
    """One ``{date, level}`` per day in the window (oldest→newest). level
    0 = no log; 1 very-unpleasant … 4 very-pleasant (the contribution-grid
    encoding — keeps the Board renderer unchanged)."""
    by_day: dict[str, list[int]] = defaultdict(list)
    for e in entries:
        if e.date:
            by_day[e.date].append(int(e.valence))
    cells = []
    start = today.toordinal() - (window_days - 1)
    for o in range(start, today.toordinal() + 1):
        dstr = date.fromordinal(o).isoformat()
        vs = by_day.get(dstr, [])
        cells.append({
            "date": dstr,
            "level": _q.valence_level(
                _mean(vs), logged=bool(vs)),
        })
    return cells


# Deterministic tiebreak when a day has equal counts across quadrants.
# Rough states (red/blue) win ties so the grid never visually hides a
# hard day behind a co-logged pleasant one. Fixed order ⇒ pure output.
_QORDER = ["red", "blue", "yellow", "green", "unknown"]


def day_quadrant_cells(entries, *, today: date,
                        window_days: int) -> list[dict]:
    """One ``{date, quadrant, color, count}`` per day in the window
    (oldest→newest). ``quadrant`` is that day's DOMINANT (most-logged)
    mood-meter quadrant — the mood-meter colouring. No-log days
    get quadrant ``none`` (dim tile). Pure; deterministic tiebreak."""
    by_day: dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        if e.date:
            by_day[e.date][e.quadrant or "unknown"] += 1
    cells = []
    start = today.toordinal() - (window_days - 1)
    for o in range(start, today.toordinal() + 1):
        dstr = date.fromordinal(o).isoformat()
        c = by_day.get(dstr)
        if not c:
            cells.append({"date": dstr, "quadrant": "none",
                          "color": _q.color_of("none"), "count": 0})
            continue
        top = max(
            c.items(),
            key=lambda kv: (kv[1], -_QORDER.index(kv[0])
                            if kv[0] in _QORDER else -len(_QORDER)))
        q = top[0]
        cells.append({"date": dstr, "quadrant": q,
                      "color": _q.color_of(q),
                      "count": sum(c.values())})
    return cells


def logging_streak(entries, *, today: date) -> int:
    """Consecutive days with ≥1 log ending today OR yesterday (so the
    streak isn't 'broken' just because today isn't logged yet)."""
    days = {e.date for e in entries if e.date}
    if not days:
        return 0
    anchor = today
    if today.isoformat() not in days:
        y = date.fromordinal(today.toordinal() - 1)
        if y.isoformat() not in days:
            return 0
        anchor = y
    n, cur = 0, anchor
    while cur.isoformat() in days:
        n += 1
        cur = date.fromordinal(cur.toordinal() - 1)
    return n


def _part_of_day(ts: str) -> str:
    try:
        h = int(ts[11:13])
    except (ValueError, IndexError):
        return "unknown"
    if 5 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def recent(entries, *, n: int = 2) -> list[dict]:
    """The ``n`` most recent logs (newest first) as small public dicts —
    ``{date, ts, emotion, quadrant}``. Deterministic: ordered by ts then
    id so ties are stable. NEVER includes the free-text note (privacy);
    the emotion display name + quadrant are safe to surface."""
    have = [e for e in entries if e.ts]
    have.sort(key=lambda e: (e.ts, e.id), reverse=True)
    out = []
    for e in have[:max(0, n)]:
        disp = e.emotion or e.emotion_key
        # A log may carry several ';'-joined feelings (primary +
        # secondary, like the source app). Each gets its OWN quadrant so
        # the card can dot each one in its own colour.
        parts = [
            {"emotion": tok.strip(),
             "quadrant": _q.quadrant_of(tok)}
            for tok in disp.split(";") if tok.strip()
        ]
        out.append({
            "date": e.date, "ts": e.ts,
            "emotion": disp, "quadrant": e.quadrant,
            "parts": parts,
        })
    return out


def summarize(s, *, today: date, window_days: int = 30) -> dict:
    """The aggregate Iga + the widget read from. Pure."""
    win = _in_window(s.entries, today, window_days)
    prior = _in_window(
        s.entries,
        date.fromordinal(today.toordinal() - window_days),
        window_days)

    quad = Counter(e.quadrant for e in win)
    emo = Counter(e.emotion or e.emotion_key for e in win if e.emotion
                  or e.emotion_key)
    val_mean = _mean([e.valence for e in win])
    eng_mean = _mean([e.energy for e in win])
    prior_val = _mean([e.valence for e in prior])

    # context correlation: which tag co-occurs most with unpleasant logs
    neg_tags: Counter = Counter()
    for e in win:
        if e.valence < 0:
            for t in (e.people + e.places + e.events):
                neg_tags[t] += 1

    last = max(
        (e for e in s.entries if e.ts),
        key=lambda e: e.ts, default=None)

    trend = "flat"
    if val_mean is not None and prior_val is not None:
        d = val_mean - prior_val
        trend = ("improving" if d >= 0.25
                 else "declining" if d <= -0.25 else "flat")

    dom = quad.most_common(1)[0][0] if quad else "unknown"
    return {
        "date": today.isoformat(),
        "window_days": window_days,
        "logs": len(win),
        "days_logged": len({e.date for e in win if e.date}),
        "logging_streak": logging_streak(s.entries, today=today),
        "valence_mean": val_mean,
        "energy_mean": eng_mean,
        "trend": trend,
        "dominant_quadrant": dom,
        "by_quadrant": dict(quad),
        "top_emotions": emo.most_common(5),
        "by_part_of_day": dict(Counter(
            _part_of_day(e.ts) for e in win)),
        "stress_context": neg_tags.most_common(3),
        "last": None if last is None else {
            "emotion": last.emotion, "quadrant": last.quadrant,
            "ts": last.ts, "date": last.date,
        },
    }
