import json
import os
from pathlib import Path
from engine.palace_reader import select_curated, load_live_drawers


def serialize_material(drawers):
    """Pure: curated drawers -> JSON-able list grouped for R1 consumption."""
    kept = select_curated(drawers)
    return [
        {"drawer_id": d.drawer_id, "wing": d.wing, "room": d.room,
         "created_at": getattr(d, "created_at", ""), "text": getattr(d, "text", "")}
        for d in kept
    ]


def dump_material(palace_path, out_path):
    """Live read-only dump of curated raw material to out_path (JSON)."""
    material = serialize_material(load_live_drawers(palace_path))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(material, indent=2, ensure_ascii=False))
    return len(material)


if __name__ == "__main__":
    import sys
    pp = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MEMPALACE_PALACE_PATH", "")
    out = sys.argv[2] if len(sys.argv) > 2 else "material.json"
    n = dump_material(pp, out)
    print(f"dumped {n} curated drawers -> {out}")
