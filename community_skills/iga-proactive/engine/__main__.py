"""``python -m engine`` entrypoint.

Run from ``skills/iga-proactive/`` (the repo's flat-import house style):

    cd ~/Gaia/skills/iga-proactive
    PYTHONPATH=engine python -m engine scan

All logic lives in :mod:`cli`; this module is only the ``-m`` shim so the
package is runnable. Deleting it does not touch the frozen engine.
"""

from __future__ import annotations

import sys

try:  # package import
    from .cli import main
except ImportError:  # flat import (engine/ on sys.path — house pattern)
    from cli import main  # type: ignore

if __name__ == "__main__":
    sys.exit(main())
