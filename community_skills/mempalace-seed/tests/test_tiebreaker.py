from engine.tiebreaker import resolve, Candidate

def mk(fact, created_at, is_correction=False, drawer_id="d"):
    return Candidate(fact=fact, created_at=created_at,
                     is_correction=is_correction, drawer_id=drawer_id)

def test_explicit_correction_beats_newer_inferred():
    older_correction = mk("X is true", "2026-01-01", is_correction=True, drawer_id="c")
    newer_inferred   = mk("X is false", "2026-05-01", is_correction=False, drawer_id="n")
    winner, reason = resolve([newer_inferred, older_correction])
    assert winner.drawer_id == "c"
    assert "correction" in reason

def test_newest_wins_when_no_correction():
    a = mk("old", "2026-01-01", drawer_id="a")
    b = mk("new", "2026-05-01", drawer_id="b")
    winner, _ = resolve([a, b])
    assert winner.drawer_id == "b"

def test_unresolved_returns_none_for_needs_user():
    a = mk("left", "2026-05-01", is_correction=True, drawer_id="a")
    b = mk("right", "2026-05-01", is_correction=True, drawer_id="b")
    winner, reason = resolve([a, b], contradictory=True)
    assert winner is None
    assert "needs_user" in reason
