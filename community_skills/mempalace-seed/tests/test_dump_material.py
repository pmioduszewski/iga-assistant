from engine.dump_material import serialize_material


def test_serialize_excludes_sessions_keeps_fields(mini_palace):
    rows = serialize_material(mini_palace)
    assert all(r["wing"] != "sessions" for r in rows)
    assert len(rows) == 3
    assert set(rows[0].keys()) == {"drawer_id", "wing", "room", "created_at", "text"}
