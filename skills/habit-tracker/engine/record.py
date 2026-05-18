"""The SANCTIONED record seam: the single way a completion is mutated.

WHY THIS EXISTS (Iga v3 Wave B)
-------------------------------
The menu-bar widget lets the user *click a square* to add / remove / set a
day's completion. That click is a MUTATION. The hard architectural contract
(MemPalace gaia/decisions/3542bae6, extended to the substrate in Wave A/B) is
that the **app holds zero habit logic and issues no writes** except by relaying
to exactly one engine seam — analogous to the existing engine-scan seam the
app already uses for the proactive engine.

This module IS that seam for the habit substrate. It:

  * mutates the substrate **only** through Wave-A ``substrate.py``
    (``SubstrateStore`` load/save — atomic tmp+os.replace, isolation-aware);
    it never hand-writes JSON and never re-implements streak/goal math;
  * is **idempotent** — ``--add`` twice in one day is one completion at
    amount 1 (one-per-day "did it" semantics), ``--remove`` on an absent day is
    a no-op, ``--set-amount N`` is exactly-once convergent;
  * is **``$IGA_STATE_DIR``-rooted with a MANDATORY ``--state-dir``** — there
    is deliberately NO implicit real-state default in the CLI, exactly like
    ``import_habitkit`` / ``export_habitkit``; a careless invocation can never
    write the user's live ``~/Gaia/state``;
  * after the mutation, **re-emits the derived widget JSON** via
    ``widget_projection`` so the polling app sees the new grid/streak/goal
    immediately. The projection is pure Wave-A code — this seam consumes it,
    it does not duplicate it.

WHAT A "COMPLETION" IS HERE
---------------------------
One civil day for one entity carries a single canonical completion Event with
an integer ``amount`` (the source per-day completion count). Multiple raw
completions in a day are *summed* by ``stats.py``; for the click-to-log UX the
seam keeps exactly one Event per (entity, day) and adjusts its ``amount``:

  --add            amount = max(1, current + 1)   (first click logs "done"=1)
  --remove         amount -> 0; if it was the only marker, the Event is
                   DELETED (a clean "never happened" — the grid square goes
                   dark and the day no longer counts).
  --set-amount N   amount = N exactly (N>=0). N==0 deletes the Event, same
                   as --remove (an explicit zero-amount day is modelled as
                   "no event" so the projection/streak treat it as not-done
                   for a normal habit; an inverse habit's clean day is
                   already the absence of an event).

Idempotency key: ``(entity_id, date)``. Re-running the same ``--set-amount``
or a ``--remove`` on an already-absent day changes nothing and is reported as
a no-op. The Event id is stable & deterministic for a seam-authored day
(``rec-<entity>-<date>``) so a second identical call cannot create a duplicate
and a round-trip through export/import stays a fixpoint.

Stdlib only. No LLM. No clock read except ``--date`` (caller passes the civil
day explicitly, same determinism contract as ``stats.py``).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(modname: str, filename: str):
    if modname in sys.modules:
        return sys.modules[modname]
    spec = importlib.util.spec_from_file_location(modname, _HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register before exec — Py3.14 @dataclass looks up
    # sys.modules[cls.__module__] during class creation.
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Consume FROZEN Wave-A contracts — never reach past their public surface.
_sub = _load("ht_substrate", "substrate.py")
_wp = _load("ht_widget_projection", "widget_projection.py")

Substrate = _sub.Substrate
Event = _sub.Event
SubstrateStore = _sub.SubstrateStore


# --------------------------------------------------------------------------- #
# canonical per-(entity, day) completion id
# --------------------------------------------------------------------------- #
def seam_event_id(entity_id: str, day: str) -> str:
    """Deterministic, stable id for a seam-authored completion. Stable so a
    repeated identical click cannot duplicate the Event and so export/import
    stays a fixpoint."""
    return f"rec-{entity_id}-{day}"


def _events_for_day(s: Substrate, entity_id: str, day: str) -> list:
    return [
        e for e in s.events
        if e.entity_id == entity_id and e.date == day
    ]


# --------------------------------------------------------------------------- #
# the pure mutation (no I/O — testable in isolation)
# --------------------------------------------------------------------------- #
class RecordError(ValueError):
    """Raised for an unknown entity or an invalid amount. The CLI maps this
    to a non-zero exit; the app surfaces it as a benign 'couldn't log'."""


def apply_record(
    s: Substrate,
    *,
    entity_id: str,
    day: str,
    op: str,
    set_amount: int | None = None,
) -> tuple[Substrate, dict]:
    """Apply one record op to the substrate IN MEMORY and return
    ``(substrate, result)``. ``result`` carries only non-private counters /
    the resolved amount (never names or notes) so a caller/log stays private.

    ``op`` in {"add", "remove", "set"}. Idempotent: the post-state is a pure
    function of (op, set_amount, current day amount); re-applying the same op
    where it would not change the canonical amount is a reported no-op.
    """
    ent = s.entity(entity_id)
    if ent is None:
        raise RecordError(f"unknown entity {entity_id!r}")

    # Validate the civil date up front (deterministic; mirrors stats.py).
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise RecordError(f"invalid --date {day!r}") from exc

    existing = _events_for_day(s, entity_id, day)
    # The canonical "current amount" for the day is the SUM (same relation
    # stats.py uses); seam-authored days collapse to one Event but an
    # imported day may legitimately carry several.
    current = sum(int(e.amount) for e in existing)

    if op == "add":
        target = max(1, current + 1)
    elif op == "remove":
        target = 0
    elif op == "set":
        if set_amount is None or int(set_amount) < 0:
            raise RecordError("--set-amount requires N >= 0")
        target = int(set_amount)
    else:  # pragma: no cover - argparse constrains this
        raise RecordError(f"unknown op {op!r}")

    changed = target != current

    # Re-materialize the day as the single canonical Event (or none).
    # Remove every existing Event for the day first (collapses any
    # multi-completion day to the seam's one-Event invariant), then re-add
    # exactly one iff target > 0.
    if existing:
        keep_ids = {id(e) for e in existing}
        s.events = [e for e in s.events if id(e) not in keep_ids]

    # Preserve tz_offset / note from a prior seam Event for the day if any
    # (so toggling off→on the same day doesn't silently drop a note).
    prior = next(
        (e for e in existing if e.id == seam_event_id(entity_id, day)),
        existing[0] if existing else None,
    )
    if target > 0:
        s.events.append(
            Event(
                id=seam_event_id(entity_id, day),
                entity_id=entity_id,
                date=day,
                amount=target,
                tz_offset_min=(prior.tz_offset_min if prior else 0),
                note=(prior.note if prior else None),
                attrs={},
            )
        )

    return s, {
        "op": op,
        "previous_amount": current,
        "amount": target,
        "changed": changed,
        "deleted": target == 0,
    }


# --------------------------------------------------------------------------- #
# the seam: mutate the store, re-emit the widget (isolation-mandatory)
# --------------------------------------------------------------------------- #
def record(
    *,
    state_dir: str | Path,
    entity_id: str,
    day: str,
    op: str,
    set_amount: int | None = None,
    window_days: int | None = None,
) -> dict:
    """Load the substrate at ``state_dir`` (isolation-rooted), apply the op
    atomically, persist via the FROZEN ``SubstrateStore``, then re-emit the
    derived widget JSON via the FROZEN ``widget_projection``.

    ``state_dir`` is MANDATORY (callers — including the app seam — must pass
    an explicit root). Returns non-private counters + the re-projected widget
    path. Pure delegation: zero streak/goal/grid math lives here.
    """
    import os

    if not state_dir:
        raise RecordError("state_dir is mandatory (no implicit real default)")
    # Hard isolation for the whole operation, same idiom as import/export.
    os.environ["IGA_STATE_DIR"] = str(state_dir)

    store = SubstrateStore("habit-tracker")
    s = store.load()
    s, result = apply_record(
        s, entity_id=entity_id, day=day, op=op, set_amount=set_amount
    )
    store.save(s)  # atomic tmp+os.replace via the frozen store

    # Re-emit BOTH derived widget JSONs so the polling app refreshes whichever
    # widget it renders (frozen v1 single-habit + Wave-B multi-habit). Pure
    # Wave-A/B projection — consumed, never reimplemented; the seam computes
    # no streak/goal/grid math.
    kw: dict = {"entity_id": entity_id}
    if window_days is not None:
        kw["window_days"] = max(1, int(window_days))
    v1_path, habits_path = _wp.project_all(**kw)

    result["widget_path"] = str(v1_path)
    result["habits_widget_path"] = str(habits_path)
    result["entity_id"] = entity_id
    result["date"] = day
    return result


def reproject(
    *,
    state_dir: str | Path,
    window_days: int | None = None,
) -> dict:
    """NON-MUTATING refresh: re-emit BOTH derived widget JSONs from the
    CURRENT substrate, with the projection's own (system) ``today``, WITHOUT
    loading-mutating-saving the substrate.

    This exists because the running app, on a cold launch (e.g. after a Mac
    restart) when no scan/record has run since yesterday, otherwise renders a
    DAY-STALE window — the engine's last-emitted ``today`` is behind the real
    date and the strip has no cell for today until some mutation incidentally
    re-projects. The app triggers this on launch so the widget's
    ``today``/streak/goal/coach are current WITHOUT performing a fake write.

    Contract: this touches NO substrate state. ``widget_projection.project``
    / ``project_habits`` only ``load()`` the substrate and atomically write
    the *widget* files — they never save the substrate back, so the
    substrate file is byte-identical before and after. The engine still owns
    the projection; the app only triggers it (exactly like the read-only
    scan seam). Returns the two widget paths.
    """
    import os

    if not state_dir:
        raise RecordError("state_dir is mandatory (no implicit real default)")
    os.environ["IGA_STATE_DIR"] = str(state_dir)

    kw: dict = {}
    if window_days is not None:
        kw["window_days"] = max(1, int(window_days))
    v1_path, habits_path = _wp.project_all(**kw)
    return {
        "widget_path": str(v1_path),
        "habits_widget_path": str(habits_path),
        "reprojected": True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="record",
        description="The sanctioned habit-tracker record seam: mutate one "
        "(habit, day) completion via the Wave-A substrate, then re-emit the "
        "derived widget JSON. The menu-bar widget relays clicks here; it "
        "never mutates JSON or computes habit logic itself. With "
        "--reproject it instead does a NON-MUTATING widget refresh (no "
        "--habit/--date/op needed) so a cold-launched app is never stuck "
        "on a day-stale window.",
    )
    ap.add_argument(
        "--state-dir",
        required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR). There is NO "
        "implicit real-state default — pass an explicit dir so the user's "
        "live ~/Gaia/state can never be clobbered by a careless run.",
    )
    ap.add_argument(
        "--reproject",
        action="store_true",
        help="NON-MUTATING: re-emit the derived widget JSON from the current "
        "substrate with the system 'today' (no --habit/--date/op required; "
        "the substrate is left byte-identical). The app triggers this on "
        "launch so it never renders a day-stale window.",
    )
    # --habit/--date/op are required ONLY for a mutation (not for
    # --reproject). argparse can't express "required unless --reproject",
    # so they default to None here and are validated below.
    ap.add_argument(
        "--habit", default=None,
        help="substrate entity id (required unless --reproject)",
    )
    ap.add_argument(
        "--date",
        default=None,
        help="civil day YYYY-MM-DD this completion belongs to "
        "(required unless --reproject)",
    )
    grp = ap.add_mutually_exclusive_group(required=False)
    grp.add_argument(
        "--add", action="store_true",
        help="log a completion for the day (idempotent: amount -> max(1, "
        "current+1); first click = 1)",
    )
    grp.add_argument(
        "--remove", action="store_true",
        help="clear the day (amount -> 0; the day's completion is deleted)",
    )
    grp.add_argument(
        "--set-amount", type=int, metavar="N",
        help="set the day's amount to exactly N (N>=0; 0 deletes it)",
    )
    ap.add_argument(
        "--days", type=int, default=None,
        help="re-projection grid window (default: projection default)",
    )
    ns = ap.parse_args(argv)

    # ---- NON-MUTATING refresh path -------------------------------------- #
    if ns.reproject:
        if ns.add or ns.remove or ns.set_amount is not None \
                or ns.habit is not None or ns.date is not None:
            print(
                "record error: --reproject is non-mutating; do not pass "
                "--habit/--date/--add/--remove/--set-amount with it",
                file=sys.stderr,
            )
            return 2
        try:
            res = reproject(state_dir=ns.state_dir, window_days=ns.days)
        except RecordError as exc:
            print(f"record error: {exc}", file=sys.stderr)
            return 2
        print(
            "reprojected: widgets re-emitted "
            f"({res['widget_path']}, {res['habits_widget_path']})"
        )
        return 0

    # ---- MUTATION path (unchanged contract) ----------------------------- #
    if ns.habit is None or ns.date is None or not (
        ns.add or ns.remove or ns.set_amount is not None
    ):
        print(
            "record error: a mutation requires --habit, --date and exactly "
            "one of --add/--remove/--set-amount (or use --reproject for a "
            "non-mutating refresh)",
            file=sys.stderr,
        )
        return 2

    if ns.add:
        op, amt = "add", None
    elif ns.remove:
        op, amt = "remove", None
    else:
        op, amt = "set", ns.set_amount

    try:
        res = record(
            state_dir=ns.state_dir,
            entity_id=ns.habit,
            day=ns.date,
            op=op,
            set_amount=amt,
            window_days=ns.days,
        )
    except RecordError as exc:
        print(f"record error: {exc}", file=sys.stderr)
        return 2

    print(
        "recorded: {op} {entity}@{date} "
        "amount {prev}->{amt}{noop} (widget re-emitted)".format(
            op=res["op"],
            entity=res["entity_id"],
            date=res["date"],
            prev=res["previous_amount"],
            amt=res["amount"],
            noop="" if res["changed"] else " [no-op]",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
