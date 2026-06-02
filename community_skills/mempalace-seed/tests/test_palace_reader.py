from engine.palace_reader import select_curated

def test_excludes_sessions_includes_curated(mini_palace):
    kept = select_curated(mini_palace)
    wings = {d.wing for d in kept}
    assert "sessions" not in wings
    assert {"user", "projects"} <= wings
    assert len(kept) == 3

def test_returns_drawer_ids_for_traceability(mini_palace):
    kept = select_curated(mini_palace)
    assert all(d.drawer_id for d in kept)
