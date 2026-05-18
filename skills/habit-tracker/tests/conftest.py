"""Make the sibling helpers (``_engine``, ``_synthetic``) importable.

``tests/`` is a package (empty ``__init__.py``) so its directory is not
automatically on ``sys.path`` for bare ``import _engine``. The existing
``test_producer.py`` sidesteps this with importlib path-loading; the Wave-A
tests share two helper modules, so we add this dir to ``sys.path`` once.
Test-only; ships under skills/habit-tracker/tests/.
"""

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
