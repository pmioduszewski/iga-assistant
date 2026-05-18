"""Mood-meter quadrant model (RULER / Yale framework the source app uses).

Every emotion sits in one of four quadrants by VALENCE (pleasant ↔
unpleasant) × ENERGY (high ↔ low):

  yellow = high energy + pleasant      green = low energy + pleasant
  red    = high energy + unpleasant    blue  = low energy + unpleasant

This curated, deterministic map (stdlib, no LLM, no network) is what lets
Iga reason about the user's psychology — valence/energy trends, dominant
quadrant — for coaching. An emotion we don't know maps to ``unknown``
(valence 0 / energy 0) so every aggregate degrades gracefully instead of
guessing. Keys are matched case-insensitively on the canonical emotion key.
"""

from __future__ import annotations

import importlib.util
import sys
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


# The canonical RULER / Mood-Meter vocabulary (~140 emotions, correct
# quadrant placement + a concise own-words description). This REPLACES the
# old hand-curated word sets — the lexicon is the single source of truth,
# so real logs resolve instead of falling back to "unknown".
_lex = _load("mt_lexicon", "lexicon.py")

# valence: +1 pleasant / -1 unpleasant ; energy: +1 high / -1 low.
# Per-emotion v/e equals its quadrant corner (the model's granularity).
_VE = {
    "yellow": (1, 1),
    "green": (1, -1),
    "red": (-1, 1),
    "blue": (-1, -1),
    "unknown": (0, 0),
}


def quadrant_of(emotion_key: str) -> str:
    """Mood-meter quadrant for an emotion (canonical lexicon), or
    'unknown' if the word isn't in the RULER vocabulary."""
    hit = _lex.lookup(emotion_key)
    return hit[0] if hit else "unknown"


def describe(emotion_key: str) -> str | None:
    """Concise framework-faithful one-line description, or None if the
    emotion isn't in the lexicon. Iga can surface this when reasoning
    about the user's state; the app may show it on hover later."""
    hit = _lex.lookup(emotion_key)
    return hit[1] if hit else None


def valence_energy(emotion_key: str) -> tuple[int, int]:
    """(valence, energy) ∈ {-1,0,1}² for an emotion key. 0/0 if unknown."""
    return _VE[quadrant_of(emotion_key)]


# the mood-meter palette (the four quadrant colours the original
# app uses), as #rrggbb. The renderer paints the day's DOMINANT-emotion
# quadrant with these so the Mood grid reads like the source app.
PALETTE = {
    "yellow": "#f5c518",   # high energy · pleasant
    "green": "#3fb568",    # low energy · pleasant
    "red": "#e5564e",      # high energy · unpleasant
    "blue": "#4c8dd6",     # low energy · unpleasant
    "unknown": "#8a8d98",  # logged but unmapped
    "none": "#3a3a3c",     # no log that day (dim tile)
}


def color_of(quadrant: str) -> str:
    return PALETTE.get(quadrant, PALETTE["unknown"])


# Coarse 0..4 level for a day's mean valence, for the contribution-grid
# widget (0 = no log; 1 very unpleasant … 4 very pleasant). Keeps the
# Board renderer unchanged (it already paints a 0..4 grid).
def valence_level(mean_valence: float | None, *, logged: bool) -> int:
    if not logged or mean_valence is None:
        return 0
    if mean_valence <= -0.5:
        return 1
    if mean_valence < 0.0:
        return 2
    if mean_valence < 0.5:
        return 3
    return 4
