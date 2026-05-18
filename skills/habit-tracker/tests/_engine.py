"""Load engine modules by path (engine/ is not on sys.path) — shared by the
substrate/import/export/stats/projection test files. Mirrors the existing
test_producer.py loading idiom."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ENG = Path(__file__).resolve().parents[1] / "engine"


def load(name: str):
    modname = f"ht_{name}"
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, _ENG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register BEFORE exec: Py3.14 @dataclass resolves
    # sys.modules[cls.__module__] during class creation.
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


substrate = load("substrate")
import_habitkit = load("import_habitkit")
export_habitkit = load("export_habitkit")
stats = load("stats")
widget_projection = load("widget_projection")
producer = load("producer")
record = load("record")
manage = load("manage")
summary = load("summary")
