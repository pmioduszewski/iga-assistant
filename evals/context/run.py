"""Context-retrieval benchmark.

Measures the step BEFORE the assistant answers: given a situation (a user
message, a tool call about to happen, or a tool error), which memory drawers
reach the context? Scores each strategy against hand-labelled cases:

  must      drawers the assistant needs to act correctly (current truth)
  must_not  stale or look-alike drawers that would mislead if read as current

The palace is fully synthetic (fixtures/), built fresh in a temp directory on
every run. It never reads or writes a real palace.

Run with an interpreter that can import mempalace (with supersedence):
    python evals/context/run.py                      # retrieval-only strategies
    python evals/context/run.py --judge              # add the cheap-model judge
    python evals/context/run.py --strategies semantic --k 8 --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))  # iga_llm
sys.path.insert(0, str(REPO / "skills" / "palace-guard" / "engine"))  # guard (baseline)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
def load_fixtures(cases_file="cases.json"):
    drawers = []
    for p in sorted(FIXTURES.glob("palace_*.json")):
        drawers.extend(json.loads(p.read_text()))
    supersedes = json.loads((FIXTURES / "supersedes.json").read_text())
    cases = json.loads((FIXTURES / cases_file).read_text())
    return drawers, supersedes, cases


# --------------------------------------------------------------------------
# Temp palace
# --------------------------------------------------------------------------
class Palace:
    """A throwaway MemPalace populated from the synthetic fixtures."""

    def __init__(self, drawers, supersedes):
        self.tmp = tempfile.TemporaryDirectory(prefix="iga-ctx-bench-")
        root = Path(self.tmp.name)
        palace_path = root / "palace"
        cfg_dir = root / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(json.dumps({"palace_path": str(palace_path)}))
        # The env var wins over config.json, so a user's real palace path in the
        # environment would otherwise receive the synthetic drawers.
        os.environ["MEMPALACE_PALACE_PATH"] = str(palace_path)

        from mempalace.config import MempalaceConfig
        import mempalace.mcp_server as ms

        self.ms = ms
        ms._config = MempalaceConfig(config_dir=str(cfg_dir))
        ms._wal_log = lambda *a, **k: None  # keep synthetic writes out of the user's write log
        import logging

        logging.getLogger("mempalace_mcp").setLevel(logging.WARNING)  # one line per filed drawer otherwise
        self.palace_path = ms._config.palace_path
        if Path(self.palace_path).resolve() != palace_path.resolve():
            raise RuntimeError(f"refusing to run: palace resolved to {self.palace_path}, not the temp dir")

        import contextlib, io

        self.meta = {d["id"]: d for d in drawers}
        self.successor = {}  # synthetic old id -> synthetic new id
        self.to_synth = {}  # any physical/logical drawer id -> synthetic id
        self.logical = {}  # synthetic id -> logical drawer id
        for d in drawers:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                res = ms.tool_add_drawer(d["wing"], d["room"], d["content"], added_by="bench")
            if not res.get("success"):
                raise RuntimeError(f"add failed for {d['id']}: {res}")
            logical = res["drawer_id"]
            self.logical[d["id"]] = logical
            self.to_synth[logical] = d["id"]
            for cid in res.get("chunk_ids") or []:
                self.to_synth[cid] = d["id"]
            self._stamp_filed_at(logical, res.get("chunk_ids") or [], d["filed_at"])

        for e in supersedes:
            out = ms.tool_mark_superseded(
                self.logical[e["old"]], self.logical[e["new"]], reason=e.get("reason", "")
            )
            if "error" in out:
                raise RuntimeError(f"supersede failed {e}: {out}")
            self.successor[e["old"]] = e["new"]

    def link(self, old, new, reason=""):
        out = self.ms.tool_mark_superseded(self.logical[old], self.logical[new], reason=reason)
        if "error" in out:
            raise RuntimeError(f"link failed {old}->{new}: {out}")
        self.successor[old] = new

    def current_version(self, sid):
        seen = set()
        while sid in self.successor and sid not in seen:
            seen.add(sid)
            sid = self.successor[sid]
        return sid

    def _stamp_filed_at(self, logical, chunk_ids, filed_at):
        col = self.ms._get_collection()
        ids = chunk_ids or [logical]
        got = col.get(ids=ids, include=["metadatas"])
        metas = [{**(m or {}), "filed_at": f"{filed_at}T12:00:00"} for m in got["metadatas"]]
        col.update(ids=got["ids"], metadatas=metas)

    def search(self, query, n, max_distance=0.0):
        from mempalace.searcher import search_memories

        res = search_memories(query, self.palace_path, n_results=n, max_distance=max_distance)
        return res.get("results", []) if isinstance(res, dict) else []

    def synth(self, hit):
        return self.to_synth.get(hit.get("drawer_id") or "")

    def close(self):
        self.tmp.cleanup()


# --------------------------------------------------------------------------
# Situations -> query text
# --------------------------------------------------------------------------
def situation_text(trigger):
    parts = [trigger.get("text", "")]
    if trigger["kind"] in ("tool_call", "tool_error"):
        parts.append(f"tool: {trigger.get('tool', '')}")
    if trigger["kind"] == "tool_call":
        parts.append("args: " + json.dumps(trigger.get("args", {}), ensure_ascii=False))
    if trigger["kind"] == "tool_error":
        parts.append("error: " + trigger.get("error", ""))
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Strategies. Each returns an ordered list of synthetic drawer ids.
# --------------------------------------------------------------------------
def strat_names(palace, trigger, k):
    """Name-based guard (palace-guard skill): entities in the USER MESSAGE only,
    top drawer each. Needs the palace-guard skill installed under skills/."""
    import guard

    out = []
    for ent in guard.extract_entities(trigger.get("text", ""), vocab=None, cap=3):
        hits = guard.rank_hits(palace.search(ent, n=6, max_distance=0.65))
        if hits:
            sid = palace.synth(hits[0])
            if sid and sid not in out:
                out.append(sid)
    return out


MAX_DIST = 0.0  # set by --max-dist: drop hits farther than this (0 = off)


def strat_semantic(palace, trigger, k):
    """Meaning of the whole situation (message + tool call or error), top k."""
    out = []
    for h in palace.search(situation_text(trigger), n=k * 3):
        if MAX_DIST and (h.get("distance") or 0) > MAX_DIST:
            continue
        sid = palace.synth(h)
        if sid and sid not in out:
            out.append(sid)
        if len(out) >= k:
            break
    return out


JUDGE_SYSTEM = (
    "You pick which memory notes an assistant needs in context before it acts. "
    "Reply with JSON only."
)

JUDGE_PROMPT = """Situation:
{situation}

Candidate notes (number, date filed, text):
{candidates}

Which notes does the assistant need in order to act correctly in this situation?
- Include notes with rules, preferences, known tool problems or facts that change what it should do.
- When two notes conflict about the same thing, keep only the one that is current (usually the newer
  one, or the one that says the other is outdated or fixed).
- Leave out notes that are about something else, even if they share names or words.
- At most {max_pick} notes.
Reply exactly: {{"pick": [numbers]}}"""


def make_judge(model, provider, max_pick, pool):
    from iga_llm import core

    def strat_judge(palace, trigger, k):
        hits, seen = [], set()
        for h in palace.search(situation_text(trigger), n=pool * 3):
            sid = palace.synth(h)
            if sid and sid not in seen:
                seen.add(sid)
                hits.append((sid, h))
            if len(hits) >= pool:
                break
        if not hits:
            return []
        lines = []
        for i, (sid, h) in enumerate(hits, 1):
            text = " ".join((h.get("text") or "").split())[:700]
            filed = (h.get("created_at") or h.get("filed_at") or "")[:10]
            lines.append(f"[{i}] ({filed}) {text}")
        prompt = JUDGE_PROMPT.format(
            situation=situation_text(trigger), candidates="\n".join(lines), max_pick=max_pick
        )
        raw = core.complete(
            prompt, tier="cheap", model=model, system=JUDGE_SYSTEM, provider=provider, timeout=120
        )
        picks = _parse_picks(raw)
        return [hits[i - 1][0] for i in picks if 1 <= i <= len(hits)][:max_pick]

    return strat_judge


def _parse_picks(raw):
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        return []
    try:
        data = json.loads(raw[start : end + 1])
        return [int(x) for x in data.get("pick", [])]
    except (ValueError, TypeError):
        return []


# --------------------------------------------------------------------------
# Save-time linking simulation
# --------------------------------------------------------------------------
def simulate_links(palace, complete, neighbours=4, workers=6, cache_path=None):
    """Replay every note as if it was just saved: judge its closest OLDER notes and
    link the ones it makes outdated. Verdicts are cached so reruns are free."""
    from concurrent.futures import ThreadPoolExecutor
    import linker

    cache = {}
    if cache_path and Path(cache_path).exists():
        cache = json.loads(Path(cache_path).read_text())

    jobs = []
    for sid, d in palace.meta.items():
        older = []
        for h in palace.search(d["content"][:1500], n=neighbours * 4):
            oid = palace.synth(h)
            if oid and oid != sid and oid not in older and palace.meta[oid]["filed_at"] < d["filed_at"]:
                older.append(oid)
            if len(older) >= neighbours:
                break
        if older:
            jobs.append((sid, older))

    def run(job):
        sid, older = job
        key = sid + "|" + ",".join(older)
        if key in cache:
            return sid, older, cache[key]
        d = palace.meta[sid]
        items = linker.ask(
            complete, d["filed_at"], d["content"],
            [(palace.meta[o]["filed_at"], palace.meta[o]["content"]) for o in older],
        )
        return sid, older, items

    t = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))
    links = []
    for sid, older, items in results:
        cache[sid + "|" + ",".join(older)] = items  # raw verified claims: filters re-score for free
        olds = [(palace.meta[o]["filed_at"], palace.meta[o]["content"]) for o in older]
        for i in linker.decide(items, olds):
            links.append((older[i], sid))
    # apply oldest-first so chains resolve to the newest note
    for old, new in sorted(links, key=lambda p: palace.meta[p[1]]["filed_at"]):
        if palace.current_version(new) != old:  # never create a cycle
            palace.link(old, new, reason="save-time linker (benchmark)")
    if cache_path:
        Path(cache_path).write_text(json.dumps(cache))
    return {"judged": len(jobs), "links": len(links), "secs": round(time.time() - t, 1)}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def score(case, ctx, palace=None):
    """A must-have counts as present if it, or a note that supersedes it, is in
    context (a newer version carries the fact). Hiding a must-have behind a link
    is reported separately as `hidden`, so a wrong link can not look like a win."""
    must, must_not = set(case["must"]), set(case.get("must_not", []))
    got = set(ctx)
    if palace is not None:
        present = {m for m in must if m in got or palace.current_version(m) in got}
        hidden = {m for m in must if palace.current_version(m) != m}
    else:
        present, hidden = must & got, set()
    return {
        "hidden": len(hidden),
        "recall": len(present) / len(must) if must else 1.0,
        "all_must": present == must,
        "leak": bool(must_not & got),
        "size": len(ctx),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategies", default="names,semantic")
    ap.add_argument("--k", type=int, default=5, help="context size for semantic")
    ap.add_argument("--judge", action="store_true", help="add the cheap-model judge strategy")
    ap.add_argument("--judge-model", default=None, help="model override (default: provider's cheap tier)")
    ap.add_argument("--provider", default=None, help="iga_llm provider (default: IGA_PROVIDER)")
    ap.add_argument("--pool", type=int, default=15, help="candidates shown to the judge")
    ap.add_argument("--cases", default=None, help="comma list of case ids to run")
    ap.add_argument("--cases-file", default="cases.json", help="cases fixture (e.g. cases_hard.json)")
    ap.add_argument("--link", action="store_true", help="simulate save-time linking before scoring")
    ap.add_argument("--link-model", default=None, help="model for the linker (default: cheap tier)")
    ap.add_argument("--link-cache", default=None, help="verdict cache file (reruns skip model calls)")
    ap.add_argument("--max-dist", type=float, default=0.0, help="relevance floor for semantic (0 = off)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", dest="json_out", default=None, help="write per-case results here")
    args = ap.parse_args()

    global MAX_DIST
    MAX_DIST = args.max_dist
    drawers, supersedes, cases = load_fixtures(args.cases_file)
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    strategies = {"names": strat_names, "semantic": strat_semantic}
    chosen = [s for s in args.strategies.split(",") if s]
    if "names" in chosen:
        try:
            import guard  # noqa: F401
        except ImportError:
            print("skipping 'names': the palace-guard skill is not installed under skills/")
            chosen.remove("names")
    if args.judge:
        strategies["judge"] = make_judge(args.judge_model, args.provider, args.k, args.pool)
        chosen.append("judge")

    t0 = time.time()
    palace = Palace(drawers, supersedes)
    print(f"palace: {len(drawers)} drawers, {len(supersedes)} supersede links, "
          f"built in {time.time() - t0:.1f}s; {len(cases)} cases")
    if args.link:
        from iga_llm import core

        def complete(prompt, system):
            return core.complete(prompt, tier="cheap", model=args.link_model, system=system,
                                 provider=args.provider, timeout=180)

        stats = simulate_links(palace, complete, cache_path=args.link_cache)
        print(f"save-time linker: judged {stats['judged']} notes, made {stats['links']} links "
              f"in {stats['secs']}s")
    print()

    rows = []
    try:
        for case in cases:
            for name in chosen:
                t = time.time()
                try:
                    ctx = strategies[name](palace, case["trigger"], args.k)
                    err = None
                except Exception as ex:  # a judge/backend failure is a result, not a crash
                    ctx, err = [], str(ex)[:200]
                dt = time.time() - t
                r = {"case": case["id"], "pattern": case["pattern"], "kind": case["trigger"]["kind"],
                     "strategy": name, "ctx": ctx, "secs": round(dt, 3), "error": err,
                     **score(case, ctx, palace)}
                rows.append(r)
                if args.verbose:
                    flag = "OK " if r["all_must"] and not r["leak"] else "MISS" if not r["all_must"] else "LEAK"
                    print(f"{flag} {case['id']:>4} {name:<9} need={case['must']} "
                          f"stale={case.get('must_not', [])} got={ctx}{' ERR ' + err if err else ''}")
    finally:
        palace.close()

    report(rows, chosen)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(rows, indent=1))


def report(rows, chosen):
    def agg(rs):
        return (
            f"{sum(r['all_must'] for r in rs) / len(rs):6.0%} "
            f"{statistics.mean(r['recall'] for r in rs):6.0%} "
            f"{sum(r['leak'] for r in rs) / len(rs):6.0%} "
            f"{statistics.median(r['secs'] for r in rs):7.2f}s "
            f"{sum(1 for r in rs if r['error']):4d}"
        )

    print(f"\n{'strategy':<10} {'solved':>6} {'recall':>6} {'leak':>6} {'median':>8} {'errs':>4}")
    for s in chosen:
        rs = [r for r in rows if r["strategy"] == s]
        if rs:
            print(f"{s:<10} {agg(rs)}")

    for dim in ("kind", "pattern"):
        print(f"\nsolved by {dim}:")
        keys = sorted({r[dim] for r in rows})
        print(f"{'':<14}" + "".join(f"{s:>10}" for s in chosen))
        for key in keys:
            cells = []
            for s in chosen:
                rs = [r for r in rows if r["strategy"] == s and r[dim] == key]
                cells.append(f"{sum(r['all_must'] for r in rs)}/{len(rs)}" if rs else "-")
            print(f"{key:<14}" + "".join(f"{c:>10}" for c in cells))
    hidden = {r["case"]: r["hidden"] for r in rows if r["hidden"]}
    print(f"\nmust-have notes hidden behind a link (wrong links): {sum(hidden.values())} "
          f"in cases {sorted(hidden)}" if hidden else "\nmust-have notes hidden behind a link: 0")
    print("solved = every must-have drawer (or its newer version) in context; "
          "leak = a stale/decoy drawer in context")


if __name__ == "__main__":
    main()
