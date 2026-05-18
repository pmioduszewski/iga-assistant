"""Tests for the newsletter-research deterministic helpers.

Pure-function tests (no network, no MCP, no LLM) — same testing posture as
skills/iga-proactive-research/tests/test_scanner.py.
"""

from __future__ import annotations

import extract as ex  # type: ignore
from extract import (  # type: ignore
    Finding,
    Project,
    best_fit,
    extract_github_repos,
    extract_package_candidates,
    extract_urls,
    finding_key,
    fit_score,
    normalize_text,
    strip_tracking,
    vault_drawer_body,
)


# ---------- normalize / tracking ---------------------------------------


def test_normalize_text_lowercases_strips_punct_emoji():
    assert normalize_text("  🚀 Drizzle-ORM,  v2!! ") == "drizzle orm v2"


def test_normalize_text_preserves_diacritics():
    assert normalize_text("Łukasz's lib") == "łukasz s lib"


def test_strip_tracking_removes_utm_and_mailers():
    u = "https://ex.com/post?utm_source=nl&utm_campaign=x&id=7&mc_eid=abc"
    out = strip_tracking(u)
    assert "utm_source" not in out
    assert "mc_eid" not in out
    assert "id=7" in out


def test_strip_tracking_trailing_slash_and_punct():
    assert strip_tracking("https://ex.com/a/).") == "https://ex.com/a"


# ---------- url / repo / pkg extraction --------------------------------


def test_extract_urls_dedup_order_stable():
    body = "see https://a.com/x?utm_source=n and https://b.io then https://a.com/x"
    out = extract_urls(body)
    assert out == ["https://a.com/x", "https://b.io"]


def test_extract_github_repos_filters_reserved_and_dedups():
    body = (
        "Check github.com/tanstack/router and "
        "https://github.com/Tanstack/Router/issues plus "
        "github.com/sponsors/someone and github.com/drizzle-team/drizzle-orm"
    )
    out = extract_github_repos(body)
    assert out == ["tanstack/router", "drizzle-team/drizzle-orm"]


def test_extract_package_candidates_scoped_and_hyphenated():
    body = "Try @tanstack/router or react-aria but not plainword or Word"
    out = extract_package_candidates(body)
    assert "@tanstack/router" in out
    assert "react-aria" in out
    assert "plainword" not in out


# ---------- fit scoring (pure) -----------------------------------------


def test_fit_score_two_keywords_is_3():
    p = Project("WebApp", ("drizzle", "postgres"))
    assert fit_score("A drizzle + postgres migration tip", p) == 3


def test_fit_score_name_plus_keyword_is_3():
    p = Project("Gaia", ("mempalace",))
    assert fit_score("Gaia mempalace recall tuning", p) == 3


def test_fit_score_single_keyword_is_2():
    p = Project("WebApp", ("drizzle", "postgres"))
    assert fit_score("A drizzle migration tip", p) == 2


def test_fit_score_bare_name_is_1():
    p = Project("Gaia", ("mempalace",))
    assert fit_score("Gaia got a new logo", p) == 1


def test_fit_score_no_signal_is_0():
    p = Project("WebApp", ("drizzle",))
    assert fit_score("Unrelated cooking newsletter", p) == 0


def test_fit_score_multiword_keyword_substring():
    p = Project("Infra", ("react compiler",))
    assert fit_score("the new React Compiler beta", p) == 2


def test_fit_score_threshold_constant():
    assert ex.FIT_THRESHOLD == 2


def test_default_projects_empty_no_personal_data_upstream():
    # CLAUDE.md composability contract: no user project names ship upstream.
    assert ex.DEFAULT_PROJECTS == ()


def test_best_fit_picks_highest_deterministic_ties():
    a = Project("A", ("alpha",))
    b = Project("B", ("alpha", "beta"))
    proj, score = best_fit("alpha beta gamma", [a, b])
    assert proj is b and score == 3
    # No fit anywhere.
    assert best_fit("nothing here", [a, b]) == (None, 0)


# ---------- dedup keying -----------------------------------------------


def test_finding_key_deterministic_and_16_chars():
    k1 = finding_key("Drizzle ORM", "https://x.io/a", "lib")
    k2 = finding_key("Drizzle ORM", "https://x.io/a", "lib")
    assert k1 == k2 and len(k1) == 16


def test_finding_key_ignores_cosmetic_url_title_diffs():
    k1 = finding_key("Drizzle ORM", "https://x.io/a?utm_source=nl", "lib")
    k2 = finding_key("  drizzle   orm! ", "https://x.io/a/", "lib")
    assert k1 == k2


def test_finding_key_changes_with_type():
    assert finding_key("X", "https://x.io", "lib") != finding_key(
        "X", "https://x.io", "tool"
    )


# ---------- output contract --------------------------------------------


def test_vault_drawer_body_aaak_shape():
    f = Finding(
        finding_key="abc1234567890def",
        title="Drizzle ORM",
        url="https://orm.drizzle.team",
        artifact_type="lib",
        project="WebApp",
        fit_score=3,
        why_it_fits="Matches the active drizzle migration.",
        source_newsletter="Bytes",
        source_message_id="msg-1",
        date_found="2026-05-16",
    )
    body = vault_drawer_body(f)
    assert body.startswith("FINDING:abc1234567890def|2026-05-16|fit:3|new")
    assert "TITLE: Drizzle ORM" in body
    assert "TYPE: lib" in body
    assert "PROJECT: WebApp" in body
    assert "SOURCE: Bytes (msg msg-1)" in body
    # round-trips to a plain dict for the findings JSON
    assert f.to_entry()["status"] == "new"


def test_artifact_types_vocabulary_stable():
    assert "blog-post" in ex.ARTIFACT_TYPES
    assert "lib" in ex.ARTIFACT_TYPES
