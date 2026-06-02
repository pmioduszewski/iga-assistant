"""Test that all round prompts (R1, R2, R3) exist and contain required signal."""

from pathlib import Path

P = Path(__file__).parent.parent / "engine" / "prompts"


def test_three_round_prompts_exist():
    """All three round prompt files must exist and be non-empty."""
    for name in ("r1_distill.md", "r2_validate.md", "r3_signoff.md"):
        path = P / name
        assert path.exists(), f"{name} missing"
        content = path.read_text().strip()
        assert content, f"{name} empty"


def test_r2_is_fresh_context_adversarial():
    """R2 must explicitly note it has no Round-1 context and does adversarial checks."""
    body = (P / "r2_validate.md").read_text().lower()
    assert "no round-1 context" in body or "fresh context" in body, \
        "R2 must declare it runs with no Round-1 context"
    assert "missing" in body and "contradict" in body, \
        "R2 must cover COMPLETENESS (missing) and CONTRADICTION checks"


def test_r3_checks_traceability():
    """R3 must verify traceability and source_drawer_ids."""
    body = (P / "r3_signoff.md").read_text().lower()
    assert "traceable" in body or "source_drawer_ids" in body, \
        "R3 must verify traceability to drawer IDs"
