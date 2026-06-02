from engine.categories import CATEGORIES, SOURCE_WINGS, EXCLUDED_WINGS


def test_fixed_category_set():
    assert CATEGORIES == [
        "identity", "family", "work_projects", "tools_stack",
        "preferences", "health", "finance", "schedule",
        "commitments", "abandoned",
    ]


def test_sessions_wing_excluded():
    assert "sessions" in EXCLUDED_WINGS
    assert "sessions" not in SOURCE_WINGS
