"""pytest bootstrap: put the skill's ``engine/`` dir on sys.path."""

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "engine"))
