"""Keep every engine test off the real palace.

``scan_tick`` lazily imports mempalace for the ``not exists drawer`` condition
when no ``mempalace_mod`` / ``drawer_exists`` is injected. Under the mempalace
venv that import would succeed and query the user's palace, so block it: the
lookup then raises PalaceError and the condition fails open, which is the
behaviour the pre-existing tests were written against.
"""

import sys

import pytest


@pytest.fixture(autouse=True)
def _no_real_mempalace(monkeypatch):
    monkeypatch.setitem(sys.modules, "mempalace", None)
