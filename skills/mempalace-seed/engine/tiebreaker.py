from dataclasses import dataclass

@dataclass
class Candidate:
    fact: str
    created_at: str          # ISO date/datetime, lexically sortable
    drawer_id: str
    is_correction: bool = False

def resolve(candidates, contradictory: bool = False):
    """Return (winner|None, reason). None winner => emit to needs_pablo."""
    if not candidates:
        return None, "needs_pablo: no candidates"
    corrections = [c for c in candidates if c.is_correction]
    if corrections:
        corrections.sort(key=lambda c: c.created_at, reverse=True)
        if contradictory and len(corrections) > 1 and \
           corrections[0].created_at == corrections[1].created_at:
            return None, "needs_pablo: contradictory same-day corrections"
        return corrections[0], "explicit correction overrides inferred"
    if contradictory:
        return None, "needs_pablo: contradictory non-correction facts"
    winner = max(candidates, key=lambda c: c.created_at)
    return winner, "newest-wins"
