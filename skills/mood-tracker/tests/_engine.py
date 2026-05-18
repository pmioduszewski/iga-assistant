"""Load mood-tracker engine modules by path (engine/ not on sys.path)."""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path

_ENG = Path(__file__).resolve().parents[1] / "engine"


def load(name: str):
    modname = f"mt_{name}"
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, _ENG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


substrate = load("substrate")
quadrant = load("quadrant")
import_mood_csv = load("import_mood_csv")
export_mood_csv = load("export_mood_csv")
stats = load("stats")
widget_projection = load("widget_projection")
summary = load("summary")
record = load("record")
ingest = load("ingest")
