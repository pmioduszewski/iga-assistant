import json
from pathlib import Path
from engine.palace_reader import select_curated
from engine.seed_schema import validate_seed


def _dump(seed, path):
    Path(path).write_text(json.dumps(seed.to_dict(), indent=2, ensure_ascii=False))


def run_pipeline(drawers, run_dir, r1, r2, r3):
    """r1/r2/r3 are callables (real = agent dispatch; tests = fakes).
    r1(material)->Seed ; r2(Seed)->(Seed,report) ; r3(Seed)->(Seed,signoff_md)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    material = select_curated(drawers)

    seed1 = r1(material)
    validate_seed(seed1)
    _dump(seed1, run_dir / "seed.v1.json")

    seed2, r2report = r2(seed1)
    validate_seed(seed2)
    _dump(seed2, run_dir / "seed.v2.json")
    (run_dir / "r2-report.json").write_text(json.dumps(r2report, indent=2, ensure_ascii=False))

    seed3, signoff = r3(seed2)
    validate_seed(seed3)
    _dump(seed3, run_dir / "seed.final.json")
    (run_dir / "r3-signoff.md").write_text(signoff)

    return seed3
