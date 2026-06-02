import pytest
from dataclasses import dataclass

@dataclass
class FakeDrawer:
    drawer_id: str; wing: str; room: str; text: str; created_at: str

@pytest.fixture
def mini_palace():
    return [
        FakeDrawer("d_user_identity_1", "user", "identity", "the user is a dev", "2026-01-01"),
        FakeDrawer("d_projects_planning_1", "projects", "planning", "Acme project active", "2026-02-01"),
        FakeDrawer("d_sessions_tech_1", "sessions", "technical", "ran pytest", "2026-03-01"),
        FakeDrawer("d_user_tooling_1", "user", "tooling", "uses Widget CLI", "2026-04-01"),
    ]
