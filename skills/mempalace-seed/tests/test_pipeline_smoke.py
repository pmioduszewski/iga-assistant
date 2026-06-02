import json
from engine.pipeline import run_pipeline
from engine.seed_schema import Seed, SeedEntry

def fake_r1(_material):
    s = Seed(meta={"rounds": 1})
    s.add(SeedEntry(fact="Uses Widget CLI", source_drawer_ids=["d_user_tooling_1"],
                    category="tools_stack", confidence=0.9))
    return s

def fake_r2(seed): seed.meta["rounds"] = 2; return seed, {"missing_found": [], "contradictions_resolved": []}
def fake_r3(seed): seed.meta["rounds"] = 3; return seed, "signoff: ok"

def test_pipeline_writes_three_artifacts(tmp_path, mini_palace):
    out = run_pipeline(drawers=mini_palace, run_dir=tmp_path,
                       r1=fake_r1, r2=fake_r2, r3=fake_r3)
    assert (tmp_path / "seed.final.json").exists()
    final = json.loads((tmp_path / "seed.final.json").read_text())
    assert final["meta"]["rounds"] == 3
    assert final["categories"]["tools_stack"][0]["fact"] == "Uses Widget CLI"
    assert (tmp_path / "r3-signoff.md").exists()
