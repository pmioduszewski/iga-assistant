#!/usr/bin/env python3
"""Newsletter Research — deterministic helpers (stdlib only).

WHAT THIS IS (and is NOT)
-------------------------
This module is the *deterministic scaffolding* for the newsletter-research
capability, mirroring the role of ``scanner.py`` in
``skills/iga-proactive-research``: pure, side-effect-free, fully unit-testable
functions. The actual newsletter *reading and judgement* (semantic artifact
extraction, the 0-3 project-fit score, the "why it fits" rationale) happens in
the WORKER (``engine/worker.prompt.md``), exactly like the research port's
worker does the actual research. **This engine never calls an LLM.** It only:

  * provides cheap regex *scaffolding* the worker can lean on (URL / GitHub
    repo / package-name candidates) — the worker still does the real semantic
    judgement; these are hints, not verdicts;
  * scores a candidate artifact against a project list with a transparent,
    deterministic keyword overlap heuristic (generic default; an optional
    ``SKILL.local.md`` override with NO personal data committed upstream);
  * computes the stable dedup key the worker/engine use to avoid filing the
    same finding twice;
  * defines the findings-JSON + MemPalace-vault drawer output contract shape.

Mirrors the research port's "engine emits WORKER_REQUEST only; the worker does
the cognition" split exactly.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import unicodedata
from typing import Iterable

# --- Artifact type vocabulary (mirrors SKILL.md "Extract artifacts") -------

ARTIFACT_TYPES = (
    "lib",
    "repo",
    "tool",
    "technique",
    "blog-post",
    "talk",
    "paper",
    "service",
)

# --- Regex scaffolding ------------------------------------------------------
# Deliberately conservative. These are HINTS the worker may use, never the
# final extraction — semantic judgement stays in the worker (same contract as
# iga-proactive-research: deterministic helpers + LLM worker).

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# github.com/<owner>/<repo> — strip a trailing .git / path / query.
_GH_RE = re.compile(
    r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"/([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)",
    re.IGNORECASE,
)

# A "package-ish" token: lower-ish slug with a hyphen/scope or a known stack
# suffix. Conservative on purpose (worker refines); we only surface clear
# candidates so the worker isn't flooded with prose words.
_PKG_RE = re.compile(
    r"(?<![\w/])(@[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*"
    r"|[a-z][a-z0-9]+(?:-[a-z0-9]+)+)(?![\w/])"
)

_TRACKING_QS_RE = re.compile(r"[?&](utm_[^=&]+|mc_eid|mc_cid|ck_subscriber_id)=[^&]*", re.I)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _is_emoji(ch: str) -> bool:
    if unicodedata.category(ch).startswith("S"):
        return True
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0x1F000 <= cp <= 0x1F2FF
    )


def normalize_text(text: str) -> str:
    """Lowercase, strip emoji + punctuation, collapse whitespace.

    Diacritics preserved on purpose (mirrors scanner.normalize_title): a
    library name with an accented char must not collide with an unrelated
    ascii word.
    """
    if not text:
        return ""
    stripped = "".join(c for c in text if not _is_emoji(c))
    stripped = _PUNCT_RE.sub(" ", stripped)
    return " ".join(stripped.lower().split())


def strip_tracking(url: str) -> str:
    """Drop common newsletter tracking query params (utm_*, mailchimp,
    convertkit). Pure string surgery — no network."""
    if not url:
        return ""
    cleaned = _TRACKING_QS_RE.sub("", url)
    # Tidy a now-dangling ? or trailing &.
    cleaned = re.sub(r"\?(&|$)", r"\1", cleaned)
    return cleaned.rstrip("?&").rstrip("/.,);")


def extract_urls(body: str) -> list[str]:
    """All distinct http(s) URLs in order of first appearance, tracking
    params stripped. Order-stable + deduped."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_RE.finditer(body or ""):
        u = strip_tracking(m.group(0))
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def extract_github_repos(body: str) -> list[str]:
    """Distinct ``owner/repo`` slugs referenced via github.com links.

    Filters obvious non-repo paths (``sponsors``, ``orgs``, ``features``,
    ``about``, ``settings``, ``marketplace``). Order-stable + deduped.
    """
    _RESERVED = {
        "sponsors", "orgs", "features", "about", "settings",
        "marketplace", "topics", "collections", "trending", "pricing",
        "login", "join", "explore", "notifications",
    }
    seen: set[str] = set()
    out: list[str] = []
    for m in _GH_RE.finditer(body or ""):
        owner, repo = m.group(1), m.group(2)
        if owner.lower() in _RESERVED:
            continue
        slug = f"{owner}/{repo}"
        key = slug.lower()
        if key not in seen:
            seen.add(key)
            out.append(slug)
    return out


def extract_package_candidates(body: str) -> list[str]:
    """Hint-level package/library name candidates (scoped ``@a/b`` or
    hyphenated slugs). The worker still does the real semantic call on which
    of these are genuine libraries vs incidental slugs. Order-stable +
    deduped, lowercased."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _PKG_RE.finditer(body or ""):
        tok = m.group(1).lower()
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


# --- Project-fit scoring ----------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Project:
    """A project the newsletter findings are scored against. ``keywords`` is
    the deterministic match surface. The generic engine ships an EMPTY default
    project list (no personal data upstream); the real list is read at runtime
    from MemPalace ``projects/*`` and/or ``SKILL.local.md`` per SKILL.md."""

    name: str
    keywords: tuple[str, ...] = ()


# Generic default: deliberately empty. No user project names ship upstream
# (CLAUDE.md composability contract). The worker pulls the real list from
# MemPalace projects/* at runtime; an optional SKILL.local.md may pre-seed it.
DEFAULT_PROJECTS: tuple[Project, ...] = ()

# Fit threshold (mirrors SKILL.md "Apply fit threshold ≥2").
FIT_THRESHOLD = 2


def fit_score(
    artifact_text: str,
    project: Project,
) -> int:
    """Deterministic 0-3 fit score by normalized keyword overlap.

    This is the cheap, transparent floor the worker BUILDS ON — it is NOT a
    substitute for the worker's semantic judgement (same division of labour
    as iga-proactive-research: the engine is deterministic, the worker is the
    LLM). Scoring rubric, mirroring SKILL.md:

      * 3 — strong: 2+ distinct project keywords hit, OR an exact project-name
            hit plus any keyword.
      * 2 — category: exactly one project keyword hit.
      * 1 — tangential: only the bare project name appears (no keyword).
      * 0 — no signal.

    Pure: same inputs → same output, no I/O.
    """
    hay = normalize_text(artifact_text)
    if not hay:
        return 0
    hay_tokens = set(hay.split())

    name_norm = normalize_text(project.name)
    # Token/phrase match — NOT a bare substring (a short project name like
    # "A" or "Iga" must not spuriously "hit" inside "alpha"/"abigail").
    if not name_norm:
        name_hit = False
    elif " " in name_norm:
        name_hit = name_norm in hay
    else:
        name_hit = name_norm in hay_tokens

    kw_hits = 0
    for kw in project.keywords:
        kwn = normalize_text(kw)
        if not kwn:
            continue
        # Multi-word keyword → substring; single token → token match.
        if " " in kwn:
            if kwn in hay:
                kw_hits += 1
        elif kwn in hay_tokens:
            kw_hits += 1

    if kw_hits >= 2 or (name_hit and kw_hits >= 1):
        return 3
    if kw_hits == 1:
        return 2
    if name_hit:
        return 1
    return 0


def best_fit(
    artifact_text: str,
    projects: Iterable[Project],
) -> tuple[Project | None, int]:
    """Return the highest-scoring project and its score (deterministic ties
    broken by project order). ``(None, 0)`` if nothing scores > 0."""
    best: Project | None = None
    best_score = 0
    for p in projects:
        s = fit_score(artifact_text, p)
        if s > best_score:
            best, best_score = p, s
    return best, best_score


# --- Dedup keying -----------------------------------------------------------


def finding_key(
    title: str,
    url: str,
    artifact_type: str,
) -> str:
    """Stable 16-char SHA1 prefix identifying a finding.

    Normalizes title + url so cosmetic differences (case, tracking params,
    trailing slash, emoji) do not produce a second key for the same artifact.
    Mirrors scanner.topic_hash's determinism contract. The worker uses this
    as the ``FINDING:<key>`` AAAK header so re-processing the same newsletter
    never double-files (the engine cooldown is the second guard)."""
    norm_title = normalize_text(title)
    norm_url = normalize_text(strip_tracking(url))
    norm_type = (artifact_type or "").strip().lower()
    raw = f"{norm_type}|{norm_title}|{norm_url}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --- Output contract --------------------------------------------------------


@dataclasses.dataclass
class Finding:
    """One filed artifact. This is the canonical shape of (a) an entry in the
    findings JSON the menu-bar board reads and (b) the MemPalace vault drawer
    body the worker writes. The engine defines the shape; the WORKER fills the
    semantic fields (``why_it_fits`` etc.)."""

    finding_key: str
    title: str
    url: str
    artifact_type: str  # one of ARTIFACT_TYPES
    project: str
    fit_score: int  # 0-3
    why_it_fits: str  # one-sentence worker rationale
    source_newsletter: str
    source_message_id: str
    date_found: str  # ISO date
    status: str = "new"  # new | reviewed

    def to_entry(self) -> dict:
        return dataclasses.asdict(self)


def vault_drawer_body(f: Finding) -> str:
    """AAAK drawer body the worker writes to MemPalace ``vault/<project>``.

    Mirrors the research port's ``RESEARCH:<hash>|...`` AAAK header style so
    surfacing/dedup parse identically across both skills."""
    return (
        f"FINDING:{f.finding_key}|{f.date_found}|fit:{f.fit_score}|{f.status}\n"
        f"TITLE: {f.title}\n"
        f"TYPE: {f.artifact_type}\n"
        f"URL: {f.url}\n"
        f"PROJECT: {f.project}\n"
        f"WHY: {f.why_it_fits}\n"
        f"SOURCE: {f.source_newsletter} (msg {f.source_message_id})"
    )
