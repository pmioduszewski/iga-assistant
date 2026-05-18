"""The SANCTIONED habit-management seam: the single way habits are
created-shaped / renamed / deleted / goal-edited / imported / exported.

WHY THIS EXISTS (Iga v3 Wave D)
-------------------------------
The menu-bar app's per-habit ``⋯`` menu lets the user RENAME a habit, DELETE
it, change its GOAL/SCHEDULE, and IMPORT / EXPORT the whole tracker. Every one
of those is a MUTATION (or, for export, a read of intimate data). The hard
architectural contract (MemPalace gaia/decisions/3542bae6, extended to the
substrate in Wave A/B and to the record click in Wave B) is unchanged here:

  * the **app holds zero habit logic and issues no writes** — it relays a
    named intent to exactly ONE engine seam, analogous to ``record.py``;
  * this module mutates the substrate **only** through the FROZEN Wave-A
    ``substrate.py`` (``SubstrateStore`` load/save — atomic tmp+os.replace,
    isolation-aware) and reuses the FROZEN ``import_habitkit`` /
    ``export_habitkit`` verbatim — it never re-implements them;
  * it is **``$IGA_STATE_DIR``-rooted with a MANDATORY ``--state-dir``** —
    there is deliberately NO implicit real-state default, exactly like
    ``record.py`` / ``import_habitkit`` / ``export_habitkit``; a careless
    invocation can never write (or export) the user's live ``~/Gaia/state``;
  * after any MUTATION it **re-emits the derived widget JSON** via the
    FROZEN ``widget_projection`` so the polling app refreshes immediately
    (export is a pure read — no re-emit).

The pure ``apply_*`` functions take a substrate in memory and return the new
substrate + a non-private result counter dict (never names/notes), so they
are unit-testable in isolation and a log of them stays privacy-safe.

Idempotency: a delete of an absent habit, a rename to the same name, and a
set-goal to the identical goal are reported no-ops. ``set_goal`` keys the
active interval id deterministically (``goal-<entity>-<start>``) so a repeat
is a fixpoint and an export/import round-trip is stable.

Stdlib only. No LLM. No clock read except an explicit ``--today`` (caller
passes the civil day; same determinism contract as ``stats.py`` /
``record.py``).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from datetime import date, datetime, timezone
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


# Consume FROZEN Wave-A/B contracts — never reach past their public surface.
_sub = _load("ht_substrate", "substrate.py")
_wp = _load("ht_widget_projection", "widget_projection.py")
_imp = _load("ht_import_habitkit", "import_habitkit.py")
_exp = _load("ht_export_habitkit", "export_habitkit.py")

Substrate = _sub.Substrate
GoalInterval = _sub.GoalInterval
SubstrateStore = _sub.SubstrateStore
PERIODS = _sub.PERIODS


class ManageError(ValueError):
    """Unknown habit, bad period, etc. The CLI maps this to a non-zero exit;
    the app surfaces it as a benign 'couldn't do that'."""


# --------------------------------------------------------------------------- #
# pure mutations (no I/O — unit-testable in isolation)
# --------------------------------------------------------------------------- #
def apply_rename(
    s: Substrate, *, entity_id: str, name: str
) -> tuple[Substrate, dict]:
    ent = s.entity(entity_id)
    if ent is None:
        raise ManageError(f"unknown habit {entity_id!r}")
    new = (name or "").strip()
    if not new:
        raise ManageError("a habit name cannot be empty")
    changed = new != ent.name
    ent.name = new
    return s, {"op": "rename", "changed": changed}


def apply_delete(
    s: Substrate, *, entity_id: str
) -> tuple[Substrate, dict]:
    """Remove the entity AND cascade everything that references it (events,
    goal intervals, category mappings, reminders) so no orphan rows survive
    to confuse stats / a later round-trip. Idempotent: deleting an absent
    habit is a reported no-op (NOT an error — the end-state is the same)."""
    present = s.entity(entity_id) is not None
    if not present:
        return s, {"op": "delete", "changed": False, "deleted": False}
    s.entities = [e for e in s.entities if e.id != entity_id]
    s.events = [e for e in s.events if e.entity_id != entity_id]
    s.goal_intervals = [
        g for g in s.goal_intervals if g.entity_id != entity_id
    ]
    s.mappings = [m for m in s.mappings if m.entity_id != entity_id]
    s.reminders = [r for r in s.reminders if r.entity_id != entity_id]
    return s, {"op": "delete", "changed": True, "deleted": True}


def apply_set_goal(
    s: Substrate,
    *,
    entity_id: str,
    period: str,
    target: int | None,
    per_day_target: int | None,
    allow_exceed: bool,
    today: date,
) -> tuple[Substrate, dict]:
    """Replace the entity's ACTIVE goal interval with one matching the
    requested shape.

    ``period`` must be in ``PERIODS`` (day|week|month|none). the tracker
    "bi-weekly" is NOT representable in this substrate — pass the closest
    representable period; this seam never fabricates a recurrence model the
    frozen stats engine cannot evaluate.

    Mechanics (deterministic, round-trip-stable): every currently-active
    interval (``end is None``) for the entity is dropped; if ``period`` is
    not "none" a single new active interval is appended with
    ``start = today``, a STABLE id ``goal-<entity>-<today>`` (so a repeat is
    a fixpoint), and the requested target/per_day_target/allow_exceed.
    ``period == "none"`` means "tracked only" → no active interval (the
    frozen ``stats`` treats an absent interval as a binary daily habit)."""
    ent = s.entity(entity_id)
    if ent is None:
        raise ManageError(f"unknown habit {entity_id!r}")
    if period not in PERIODS:
        raise ManageError(
            f"period must be one of {PERIODS}, got {period!r}"
        )
    if target is not None and int(target) < 1:
        raise ManageError("--target must be >= 1 (omit it for no target)")
    if per_day_target is not None and int(per_day_target) < 1:
        raise ManageError("--per-day-target must be >= 1 (or omit)")

    before = [
        g for g in s.intervals_for(entity_id) if g.end is None
    ]
    s.goal_intervals = [
        g
        for g in s.goal_intervals
        if not (g.entity_id == entity_id and g.end is None)
    ]
    added = None
    if period != "none":
        added = GoalInterval(
            id=f"goal-{entity_id}-{today.isoformat()}",
            entity_id=entity_id,
            start=today.isoformat(),
            end=None,
            period=period,
            target=(int(target) if target is not None else None),
            per_day_target=(
                int(per_day_target)
                if per_day_target is not None
                else None
            ),
            allow_exceed=bool(allow_exceed),
        )
        s.goal_intervals.append(added)

    def _shape(g) -> tuple:
        return (
            g.period, g.target, g.per_day_target, g.allow_exceed,
        )

    changed = not (
        len(before) == (1 if added else 0)
        and (added is None or _shape(before[0]) == _shape(added))
    )
    return s, {
        "op": "set-goal",
        "changed": changed,
        "period": period,
        "has_target": target is not None,
        "has_per_day_target": per_day_target is not None,
    }


def apply_archive(
    s: Substrate, *, entity_id: str, archived: bool
) -> tuple[Substrate, dict]:
    """Graduate (archive) or restore a habit by flipping ``Entity.archived``.
    Archiving keeps ALL history (events/intervals untouched) — it only
    removes the habit from the active widget/aggregate (the projection
    already excludes archived). The Atomic-Habits 'graduate an automatic
    habit to free a focus slot' action. Idempotent: setting the flag to its
    current value is a reported no-op."""
    ent = s.entity(entity_id)
    if ent is None:
        raise ManageError(f"unknown habit {entity_id!r}")
    changed = bool(ent.archived) != bool(archived)
    ent.archived = bool(archived)
    return s, {
        "op": "archive" if archived else "unarchive",
        "changed": changed,
        "archived": bool(archived),
    }


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def apply_set_color(
    s: Substrate, *, entity_id: str, color: str
) -> tuple[Substrate, dict]:
    """Set the habit's colour (``Entity.attrs['color']``). Accepts an
    explicit ``#rgb``/``#rrggbb`` hex (what the app's colour picker sends);
    the projection's ``color_hex_for`` passes a hex through verbatim. Stored
    lowercased for round-trip stability. Idempotent."""
    ent = s.entity(entity_id)
    if ent is None:
        raise ManageError(f"unknown habit {entity_id!r}")
    c = (color or "").strip()
    if not _HEX_RE.match(c):
        raise ManageError(
            f"--set-color expects #rgb or #rrggbb, got {color!r}"
        )
    c = c.lower()
    attrs = dict(ent.attrs or {})
    changed = attrs.get("color") != c
    attrs["color"] = c
    ent.attrs = attrs
    return s, {"op": "set-color", "changed": changed}


def apply_reorder(
    s: Substrate, *, entity_id: str, position: int
) -> tuple[Substrate, dict]:
    """Move ``entity_id`` to 1-based ``position`` among the NON-archived
    habits (the order the widget shows), renumbering every active habit so
    ``order_index`` is contiguous 0-based in the new order. Archived habits
    keep their own ``order_index`` (they're excluded from the widget/active
    aggregate anyway). Idempotent: if it's already at that position the
    indices are unchanged → reported no-op. ``position`` is clamped to
    1..count so the UI stepper can't push it out of range."""
    ent = s.entity(entity_id)
    if ent is None:
        raise ManageError(f"unknown habit {entity_id!r}")
    if ent.archived:
        raise ManageError("cannot reorder an archived habit")
    active = sorted(
        (e for e in s.entities if not e.archived),
        key=lambda e: (e.order_index, e.id),
    )
    ids = [e.id for e in active]
    n = len(ids)
    pos = max(1, min(int(position), n))
    ids.remove(entity_id)
    ids.insert(pos - 1, entity_id)
    new_index = {eid: i for i, eid in enumerate(ids)}
    changed = False
    for e in s.entities:
        if e.id in new_index and e.order_index != new_index[e.id]:
            e.order_index = new_index[e.id]
            changed = True
    return s, {
        "op": "reorder",
        "changed": changed,
        "position": pos,
        "count": n,
    }


# --------------------------------------------------------------------------- #
# the seam: dispatch a named intent, persist, re-emit the widget
# --------------------------------------------------------------------------- #
def _reemit(window_days: int | None) -> tuple:
    kw: dict = {}
    if window_days is not None:
        kw["window_days"] = max(1, int(window_days))
    return _wp.project_all(**kw)


def manage(
    *,
    state_dir: str | Path,
    op: str,
    entity_id: str | None = None,
    name: str | None = None,
    period: str | None = None,
    target: int | None = None,
    per_day_target: int | None = None,
    allow_exceed: bool = True,
    today: date | None = None,
    path: str | Path | None = None,
    position: int | None = None,
    archived: bool = False,
    color: str | None = None,
    window_days: int | None = None,
) -> dict:
    """Load the substrate at ``state_dir`` (isolation-rooted), apply the
    named op atomically via the FROZEN store, then (for mutations) re-emit
    the derived widget JSON via the FROZEN projection. ``export`` is a pure
    read (no save, no re-emit). ``state_dir`` is MANDATORY."""
    import os

    if not state_dir:
        raise ManageError(
            "state_dir is mandatory (no implicit real default)"
        )
    os.environ["IGA_STATE_DIR"] = str(state_dir)
    today = today or datetime.now(timezone.utc).date()

    # ---- pure-read: export (never writes / re-emits the state tree) ----- #
    if op == "export":
        if not path:
            raise ManageError("--export requires an output path")
        _exp.export_file(Path(state_dir), Path(path))
        return {"op": "export", "changed": False, "path": str(path)}

    # ---- import: delegate verbatim to the FROZEN importer, then re-emit - #
    if op == "import":
        if not path:
            raise ManageError("--import requires an input path")
        counts = _imp.import_file(Path(path), Path(state_dir))
        v1, hb = _reemit(window_days)
        return {
            "op": "import",
            "changed": True,
            "counts": counts,
            "widget_path": str(v1),
            "habits_widget_path": str(hb),
        }

    # ---- substrate mutations ------------------------------------------- #
    store = SubstrateStore("habit-tracker")
    s = store.load()
    if op == "rename":
        s, result = apply_rename(s, entity_id=entity_id, name=name)
    elif op == "delete":
        s, result = apply_delete(s, entity_id=entity_id)
    elif op == "set-goal":
        s, result = apply_set_goal(
            s,
            entity_id=entity_id,
            period=period or "none",
            target=target,
            per_day_target=per_day_target,
            allow_exceed=allow_exceed,
            today=today,
        )
    elif op == "reorder":
        s, result = apply_reorder(
            s, entity_id=entity_id, position=int(position or 1)
        )
    elif op == "archive":
        s, result = apply_archive(
            s, entity_id=entity_id, archived=bool(archived)
        )
    elif op == "set-color":
        s, result = apply_set_color(
            s, entity_id=entity_id, color=color or ""
        )
    else:  # pragma: no cover - argparse constrains this
        raise ManageError(f"unknown op {op!r}")

    store.save(s)  # atomic tmp+os.replace via the frozen store
    v1, hb = _reemit(window_days)
    result["widget_path"] = str(v1)
    result["habits_widget_path"] = str(hb)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="manage",
        description="The sanctioned habit-tracker management seam: "
        "rename / delete / set-goal a habit, or import / export the whole "
        "tracker, via the FROZEN Wave-A substrate, then re-emit the derived "
        "widget JSON. The menu-bar ⋯ menu relays here; it never mutates "
        "JSON or computes habit logic itself.",
    )
    ap.add_argument(
        "--state-dir",
        required=True,
        help="REQUIRED substrate state root ($IGA_STATE_DIR). There is NO "
        "implicit real-state default — pass an explicit dir so the user's "
        "live ~/Gaia/state can never be clobbered (or exported) by a "
        "careless run.",
    )
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--rename", metavar="NAME",
                     help="rename --habit to NAME")
    grp.add_argument("--delete", action="store_true",
                     help="delete --habit and cascade its data")
    grp.add_argument("--set-goal", action="store_true",
                     help="set --habit's active goal (--period/--target/"
                     "--per-day-target/--allow-exceed)")
    grp.add_argument("--export", metavar="PATH",
                     help="write a tracker-format export of the WHOLE "
                     "tracker to PATH (pure read)")
    grp.add_argument("--import", dest="imp", metavar="PATH",
                     help="import a tracker export from PATH (merges, "
                     "idempotent)")
    grp.add_argument("--set-order", dest="set_order", type=int,
                     metavar="N",
                     help="move --habit to 1-based position N among the "
                     "non-archived habits (reorders all)")
    grp.add_argument("--archive", action="store_true",
                     help="graduate/hide --habit (keeps all history)")
    grp.add_argument("--unarchive", action="store_true",
                     help="restore an archived --habit")
    grp.add_argument("--set-color", dest="set_color", metavar="HEX",
                     help="set --habit colour (#rgb or #rrggbb)")

    ap.add_argument("--habit", default=None,
                    help="substrate entity id (rename/delete/set-goal)")
    ap.add_argument(
        "--period", default="none", choices=list(PERIODS),
        help="goal period for --set-goal (day|week|month|none). the tracker "
        "'bi-weekly' is not representable — choose the closest.",
    )
    ap.add_argument("--target", type=int, default=None,
                    help="per-PERIOD required count (omit = none)")
    ap.add_argument("--per-day-target", type=int, default=None,
                    dest="per_day_target",
                    help="per-DAY required count, e.g. 50 push-ups "
                    "(drives the in-square ring; omit = binary)")
    ax = ap.add_mutually_exclusive_group()
    ax.add_argument("--allow-exceed", dest="allow_exceed",
                    action="store_true", default=True,
                    help="allow exceeding the goal (default)")
    ax.add_argument("--no-allow-exceed", dest="allow_exceed",
                    action="store_false",
                    help="cap progress at the goal")
    ap.add_argument("--today", default=None,
                    help="civil day YYYY-MM-DD for set-goal start "
                    "(default: system UTC date — determinism contract)")
    ap.add_argument("--days", type=int, default=None,
                    help="re-projection grid window (default: projection "
                    "default)")
    ns = ap.parse_args(argv)

    if ns.rename is not None:
        op, kw = "rename", {"entity_id": ns.habit, "name": ns.rename}
    elif ns.delete:
        op, kw = "delete", {"entity_id": ns.habit}
    elif ns.set_goal:
        op, kw = "set-goal", {
            "entity_id": ns.habit,
            "period": ns.period,
            "target": ns.target,
            "per_day_target": ns.per_day_target,
            "allow_exceed": ns.allow_exceed,
        }
    elif ns.set_order is not None:
        op, kw = "reorder", {
            "entity_id": ns.habit, "position": ns.set_order}
    elif ns.archive:
        op, kw = "archive", {"entity_id": ns.habit, "archived": True}
    elif ns.unarchive:
        op, kw = "archive", {"entity_id": ns.habit, "archived": False}
    elif ns.set_color is not None:
        op, kw = "set-color", {
            "entity_id": ns.habit, "color": ns.set_color}
    elif ns.export is not None:
        op, kw = "export", {"path": ns.export}
    else:
        op, kw = "import", {"path": ns.imp}

    if op in ("rename", "delete", "set-goal", "reorder",
              "archive", "set-color") and not ns.habit:
        print(
            f"manage error: --{op} requires --habit <entity id>",
            file=sys.stderr,
        )
        return 2

    today = None
    if ns.today:
        try:
            today = date.fromisoformat(ns.today)
        except ValueError:
            print(f"manage error: invalid --today {ns.today!r}",
                  file=sys.stderr)
            return 2

    try:
        res = manage(
            state_dir=ns.state_dir,
            op=op,
            today=today,
            window_days=ns.days,
            **kw,
        )
    except ManageError as exc:
        print(f"manage error: {exc}", file=sys.stderr)
        return 2

    noop = "" if res.get("changed", True) else " [no-op]"
    print(f"managed: {res['op']}{noop} (widget re-emitted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
