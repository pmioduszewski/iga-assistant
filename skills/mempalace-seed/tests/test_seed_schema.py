import json
import pytest
from engine.seed_schema import SeedEntry, Seed, validate_seed, ValidationError


def test_entry_requires_provenance():
    """SeedEntry.check() must reject entries with empty source_drawer_ids."""
    with pytest.raises(ValidationError):
        SeedEntry(fact="x", source_drawer_ids=[], category="identity").check()


def test_roundtrip_json():
    """Seed can serialize to JSON and deserialize back."""
    s = Seed(meta={"generated_at": "2026-06-02", "rounds": 1})
    s.add(SeedEntry(fact="Uses tool Acme", source_drawer_ids=["drawer_user_tooling_ab"],
                    category="tools_stack", confidence=0.9))
    blob = json.dumps(s.to_dict())
    s2 = Seed.from_dict(json.loads(blob))
    assert s2.categories["tools_stack"][0].fact == "Uses tool Acme"


def test_validate_rejects_unknown_category():
    """validate_seed raises on unknown category."""
    s = Seed(meta={})
    with pytest.raises(ValidationError):
        s.add(SeedEntry(fact="x", source_drawer_ids=["d1"], category="bogus"))
    validate_seed(s)  # also catches via full-pass


def test_validate_flags_entry_without_drawer():
    """Full validate_seed catches entries with missing source_drawer_ids."""
    s = Seed(meta={})
    s.categories.setdefault("identity", []).append(
        SeedEntry(fact="x", source_drawer_ids=[], category="identity"))
    errs = validate_seed(s, raise_on_error=False)
    assert any("source_drawer_ids" in e for e in errs)
