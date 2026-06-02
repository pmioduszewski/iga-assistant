from engine.categories import SOURCE_WINGS, EXCLUDED_WINGS


def select_curated(drawers):
    """Keep drawers from curated wings, drop the sessions noise.
    `drawers` is any iterable of objects with .wing (and .drawer_id)."""
    out = []
    for d in drawers:
        if d.wing in EXCLUDED_WINGS:
            continue
        if SOURCE_WINGS and d.wing not in SOURCE_WINGS:
            continue
        out.append(d)
    return out


def load_live_drawers(palace_path):
    """Read-only enumeration of the real palace via the mempalace lib.
    Imported lazily so unit tests never touch Chroma. Returns drawer-shaped
    namespaces. Wrap the collection in ReadOnly before any access.

    NOTE TO INTEGRATOR: the exact accessor for the drawers collection in the
    installed mempalace lib must be confirmed before the Task 8 live run; the
    call below is the expected shape and may need adjusting to match
    mempalace/palace.py. This function is intentionally NOT unit-tested."""
    import os
    os.environ.setdefault("MEMPALACE_PALACE_PATH", palace_path)
    from mempalace import palace as _p  # noqa: lazy import
    from engine.nondestructive import ReadOnly
    col = ReadOnly(_p.get_drawers_collection(palace_path))
    raw = col.get(include=["metadatas", "documents"])
    from types import SimpleNamespace
    drawers = []
    for did, meta, doc in zip(raw["ids"], raw["metadatas"], raw["documents"]):
        drawers.append(SimpleNamespace(
            drawer_id=did, wing=meta.get("wing", ""), room=meta.get("room", ""),
            text=doc or "", created_at=meta.get("filed_at", "")))
    return drawers
