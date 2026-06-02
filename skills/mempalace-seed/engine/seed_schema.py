"""Seed schema: dataclasses and validation for seed entries with required provenance."""

from dataclasses import dataclass, field, asdict
from engine.categories import CATEGORIES


class ValidationError(Exception):
    """Raised when a seed entry or seed fails validation."""
    pass


@dataclass
class SeedEntry:
    """A single seed fact with provenance, category, and status."""
    fact: str
    source_drawer_ids: list
    category: str
    confidence: float = 1.0
    status: str = "current"  # "current" | "abandoned"
    tags: list = field(default_factory=list)

    def check(self):
        """Validate this entry. Raises ValidationError on failure."""
        if self.category not in CATEGORIES:
            raise ValidationError(f"unknown category: {self.category}")
        if not self.fact.strip():
            raise ValidationError("empty fact")
        if not self.source_drawer_ids:
            raise ValidationError("source_drawer_ids required (traceability)")
        if self.status not in ("current", "abandoned"):
            raise ValidationError(f"bad status: {self.status}")
        return self


@dataclass
class Seed:
    """A collection of seed entries, grouped by category, with metadata."""
    meta: dict
    categories: dict = field(default_factory=dict)
    needs_pablo: list = field(default_factory=list)

    def add(self, entry: SeedEntry):
        """Add a validated entry to the seed, grouped by category."""
        entry.check()
        self.categories.setdefault(entry.category, []).append(entry)

    def to_dict(self):
        """Serialize seed to a plain dict (JSON-ready)."""
        return {
            "meta": self.meta,
            "categories": {k: [asdict(e) for e in v] for k, v in self.categories.items()},
            "needs_pablo": self.needs_pablo,
        }

    @classmethod
    def from_dict(cls, d):
        """Deserialize seed from a plain dict."""
        s = cls(meta=d.get("meta", {}), needs_pablo=d.get("needs_pablo", []))
        for cat, entries in d.get("categories", {}).items():
            for e in entries:
                s.categories.setdefault(cat, []).append(SeedEntry(**e))
        return s


def validate_seed(seed: Seed, raise_on_error: bool = True):
    """
    Validate all entries in a seed.

    Returns list of error strings. If raise_on_error=True (default),
    raises ValidationError if any errors found.
    """
    errs = []
    for cat, entries in seed.categories.items():
        if cat not in CATEGORIES:
            errs.append(f"unknown category: {cat}")
        for e in entries:
            try:
                e.check()
            except ValidationError as ex:
                errs.append(f"{cat}: {ex}")
    if errs and raise_on_error:
        raise ValidationError("; ".join(errs))
    return errs
