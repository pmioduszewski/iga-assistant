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
    create=False so we never create/mutate; ReadOnly blocks any write op."""
    import os
    from types import SimpleNamespace
    os.environ["MEMPALACE_PALACE_PATH"] = palace_path
    from mempalace import palace as _p  # lazy import
    from engine.nondestructive import ReadOnly
    col = ReadOnly(_p.get_collection(palace_path, collection_name="mempalace_drawers", create=False))
    raw = col.get(include=["metadatas", "documents"])
    drawers = []
    for did, meta, doc in zip(raw["ids"], raw["metadatas"], raw["documents"]):
        meta = meta or {}
        drawers.append(SimpleNamespace(
            drawer_id=did, wing=meta.get("wing", ""), room=meta.get("room", ""),
            text=doc or "", created_at=meta.get("filed_at", "")))
    return drawers
