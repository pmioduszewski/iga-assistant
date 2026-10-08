"""Runtime scan-tick tests — including the end-to-end no-duplicate guarantee.

The headline test (`test_same_candidate_twice_one_cooldown_exactly_one_request`)
proves the anti-duplicate property at the SCAN level, not just at the ledger
level: scan the same Todoist task twice inside one cooldown window and assert
the engine produces exactly ONE dispatchable WORKER_REQUEST. This is the
literal regression the whole engine exists to prevent.
"""

import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ENGINE = str(Path(__file__).resolve().parents[1] / "engine")
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

from ledger import Ledger  # noqa: E402
from governor import Governor  # noqa: E402
from runtime import (  # noqa: E402
    scan_tick,
    render_template,
    eval_condition,
    discover_job_sources,
    load_jobs,
    research_drawer_exists,
    PalaceError,
)
import dispatcher as disp  # noqa: E402

NOW = datetime(2026, 5, 16, 9, 0, tzinfo=timezone.utc)


# ---------- helpers -----------------------------------------------------
def _make_skill(skills_dir: Path, name: str, proactive_block: str) -> Path:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: test skill\n"
        f"{proactive_block}"
        "---\n\n# Body\n",
        encoding="utf-8",
    )
    return d


_TODOIST_JOB = textwrap.dedent(
    """\
    proactive:
      - id: research-todoist
        trigger: todoist(label:iga-research, due:<7d)
        condition: not exists drawer for task
        action: spawn_worker(prompt: engine/worker.prompt.md, depth: deep)
        idempotency_key: research::{{task.id}}::{{task.due}}
        budget:
          model: claude-opus-4-7[1m]
          wall_min: 30
        deliver: surface_next_brief
        cooldown: 48h
    """
)


def _one_task(tid="101", due="2026-05-18"):
    return [
        {
            "id": tid,
            "content": "Prep Acme demo",
            "description": "ctx",
            "due": {"date": due},
        }
    ]


# ---------- render_template --------------------------------------------
def test_render_template_substitutes():
    out = render_template(
        "research::{{task.id}}::{{ task.due }}",
        {"task.id": "101", "task.due": "2026-05-18"},
    )
    assert out == "research::101::2026-05-18"


def test_render_template_missing_key_is_empty():
    assert render_template("a::{{nope}}::b", {}) == "a::::b"


# ---------- eval_condition ---------------------------------------------
def test_condition_none_is_true():
    assert eval_condition(None, {}) is True


def test_condition_not_exists_fails_open():
    assert eval_condition("not exists drawer for task", {}) is True


# ---------- not exists drawer for task (store-backed) -------------------
class _FakeCollection:
    """Chroma-shaped ``get(where=...)`` over a list of (id, metadata)."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    @staticmethod
    def _match(meta, where):
        if "$and" in where:
            return all(_FakeCollection._match(meta, w) for w in where["$and"])
        return all(meta.get(k) == v for k, v in where.items())

    def get(self, where=None, include=None, limit=None):
        self.calls.append(where)
        ids = [i for i, m in self.rows if where is None or self._match(m, where)]
        return {"ids": ids[:limit] if limit else ids}


class _FakePalace:
    def __init__(self, rows=None, collection_error=None):
        self._col = _FakeCollection(rows or [])
        self._err = collection_error

    def _get_collection(self):
        if self._err:
            raise self._err
        return self._col


# Shape of a real worker-filed research drawer:
# chunked, wing projects_<x>, room research, source_file todoist:<task id>.
_RESEARCH_ROWS = [
    (
        "drawer_projects_demo_research_abc_chunk_000000",
        {"wing": "projects_demo", "room": "research", "source_file": "todoist:101"},
    ),
    (
        "drawer_projects_demo_research_abc_chunk_000001",
        {"wing": "projects_demo", "room": "research", "source_file": "todoist:101"},
    ),
    # Same source_file but NOT a research room: must not suppress.
    ("drawer_x_research_queue_1", {"wing": "x", "room": "research-queue", "source_file": "todoist:202"}),
]


def test_research_drawer_exists_matches_source_file_in_research_room():
    pal = _FakePalace(_RESEARCH_ROWS)
    assert research_drawer_exists(pal, "todoist:101") is True
    assert research_drawer_exists(pal, "todoist:202") is False
    assert research_drawer_exists(pal, "todoist:999") is False


def test_research_drawer_exists_wraps_palace_failures():
    with pytest.raises(PalaceError):
        research_drawer_exists(_FakePalace(collection_error=OSError("locked")), "todoist:1")

    class _NoPalace:
        def _get_collection(self):
            return None

    with pytest.raises(PalaceError):
        research_drawer_exists(_NoPalace(), "todoist:1")


def test_condition_drawer_lookup_uses_trigger_kind_and_source_id():
    seen = []

    def lookup(sf):
        seen.append(sf)
        return sf == "todoist:101"

    ns = {"trigger.kind": "todoist", "source.id": "101"}
    assert eval_condition("not exists drawer for task", ns, drawer_exists=lookup) is False
    ns2 = {"trigger.kind": "todoist", "source.id": "102"}
    assert eval_condition("not exists drawer for task", ns2, drawer_exists=lookup) is True
    assert seen == ["todoist:101", "todoist:102"]


def test_condition_drawer_palace_error_fails_open():
    def lookup(sf):
        raise PalaceError("palace down")

    ns = {"trigger.kind": "todoist", "source.id": "101"}
    assert eval_condition("not exists drawer for task", ns, drawer_exists=lookup) is True


def test_condition_drawer_non_palace_error_propagates():
    def lookup(sf):
        raise KeyError("bug")

    ns = {"trigger.kind": "todoist", "source.id": "101"}
    with pytest.raises(KeyError):
        eval_condition("not exists drawer for task", ns, drawer_exists=lookup)


def test_scan_tick_existing_research_drawer_suppresses_candidate(tmp_path):
    """Regression: a task already researched (drawer with
    source_file todoist:<id> in a research room) must NOT be re-queued once
    the ledger cooldown has expired."""
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)
    tasks = _one_task("101") + [
        {"id": "202", "content": "Fresh topic", "description": "", "due": {"date": "2026-05-18"}}
    ]
    pal = _FakePalace(_RESEARCH_ROWS)
    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=tmp_path / "e.db",
        token="t",
        todoist_fetcher=lambda tok, label: tasks,
        mempalace_mod=pal,
    )
    queued = [q.candidate.source_id for q in res.queue]
    assert queued == ["202"]
    assert res.condition_skipped == 1
    assert res.errors == []


def test_scan_tick_palace_error_fails_open(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)
    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=tmp_path / "e.db",
        token="t",
        todoist_fetcher=lambda tok, label: _one_task("101"),
        mempalace_mod=_FakePalace(collection_error=OSError("locked")),
    )
    assert [q.candidate.source_id for q in res.queue] == ["101"]


def test_scan_tick_condition_bug_fails_closed(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)

    def lookup(sf):
        raise KeyError("bug")

    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=tmp_path / "e.db",
        token="t",
        todoist_fetcher=lambda tok, label: _one_task("101"),
        drawer_exists=lookup,
    )
    assert res.queue == []
    assert any("condition raised" in e for e in res.errors)


def test_research_drawer_exists_against_real_chromadb(tmp_path):
    """Same fixture drawer through a real chromadb ``where`` clause, so the
    query shape is checked against the store, not just the fake."""
    chromadb = pytest.importorskip("chromadb")
    client = chromadb.PersistentClient(path=str(tmp_path / "palace"))
    col = client.get_or_create_collection("drawers_test", embedding_function=None)
    col.add(
        ids=[i for i, _ in _RESEARCH_ROWS],
        metadatas=[m for _, m in _RESEARCH_ROWS],
        documents=["RESEARCH:x"] * len(_RESEARCH_ROWS),
        embeddings=[[0.1, 0.2, 0.3]] * len(_RESEARCH_ROWS),
    )

    class _Palace:
        def _get_collection(self):
            return col

    assert research_drawer_exists(_Palace(), "todoist:101") is True
    assert research_drawer_exists(_Palace(), "todoist:202") is False


def test_condition_exists_checks_namespace():
    assert eval_condition("task.id exists", {"task.id": "1"}) is True
    assert eval_condition("task.id exists", {"task.id": ""}) is False


def test_condition_equality():
    assert eval_condition("task.label == iga-research", {"task.label": "iga-research"}) is True
    assert eval_condition("task.label == other", {"task.label": "iga-research"}) is False


def test_condition_contains():
    assert eval_condition("title contains demo", {"title": "the demo prep"}) is True


def test_condition_unparseable_fails_open():
    assert eval_condition("$$$ garbage $$$", {}) is True


# ---------- discovery ---------------------------------------------------
def test_discover_finds_skill_md_and_proactive_yaml(tmp_path):
    sd = tmp_path / "skills"
    a = sd / "skill-a"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text("---\nname: a\n---\n")
    b = sd / "skill-b"
    b.mkdir(parents=True)
    (b / "proactive.yaml").write_text("proactive: []\n")
    found = discover_job_sources(sd)
    names = [p.name for p in found]
    assert "SKILL.md" in names
    assert "proactive.yaml" in names


def test_load_jobs_malformed_proactive_block_records_error(tmp_path):
    """A `proactive:` block that IS present but malformed (missing required
    fields) MUST still surface as a real error — the genuine-error path is
    preserved, not suppressed by the non-proactive-skip fix."""
    sd = tmp_path / "skills"
    _make_skill(sd, "good", _TODOIST_JOB)
    bad = sd / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: bad\nproactive:\n  - id: x\n---\n"  # missing required fields
    )
    jobs, errors, skipped_np = load_jobs(discover_job_sources(sd))
    assert len(jobs) == 1
    assert any("bad" in e for e in errors)
    # 'good' has a valid proactive block, 'bad' has a malformed one — neither
    # is a non-proactive skip.
    assert skipped_np == 0


def test_load_jobs_no_frontmatter_silently_skipped(tmp_path):
    """A skill with NO YAML frontmatter at all, and one WITH frontmatter but
    WITHOUT a `proactive:` key, are simply not proactive skills: silently
    skipped, ZERO errors, counted in skipped_non_proactive. Valid jobs from
    other skills are still discovered. This is the live false-red-noise bug
    (newsletter-research / trainer) proven dead."""
    sd = tmp_path / "skills"
    _make_skill(sd, "good", _TODOIST_JOB)

    # No frontmatter block at all (like newsletter-research observed live).
    nofm = sd / "newsletter-research"
    nofm.mkdir(parents=True)
    (nofm / "SKILL.md").write_text("# Newsletter Research\n\nJust prose, no fence.\n")

    # Frontmatter present but NO `proactive:` key (like trainer observed live).
    noproactive = sd / "trainer"
    noproactive.mkdir(parents=True)
    (noproactive / "SKILL.md").write_text(
        "---\nname: trainer\ndescription: a training skill\n---\n\n# Trainer\n"
    )

    jobs, errors, skipped_np = load_jobs(discover_job_sources(sd))
    assert errors == []  # the headline assertion: NO false red noise
    assert len(jobs) == 1  # the real job is still discovered
    assert jobs[0].id == "research-todoist"
    assert skipped_np == 2  # both non-proactive skills counted, not errored


def test_load_jobs_parses_proactive_yaml(tmp_path):
    sd = tmp_path / "skills"
    d = sd / "iga-proactive-research"
    d.mkdir(parents=True)
    (d / "proactive.yaml").write_text(_TODOIST_JOB)
    jobs, errors, skipped_np = load_jobs(discover_job_sources(sd))
    assert errors == []
    assert len(jobs) == 1
    assert jobs[0].id == "research-todoist"
    assert skipped_np == 0


# ---------- scan_tick basic --------------------------------------------
def test_scan_tick_fires_and_queues(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)
    db = tmp_path / "proactive.db"

    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=db,
        token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    assert res.discovered_jobs == 1
    assert res.fired_candidates == 1
    assert len(res.queue) == 1
    qc = res.queue[0]
    assert qc.idempotency_key == "research::101::2026-05-18"
    assert qc.job.id == "research-todoist"
    # Ledger now holds a live claimed row for the key.
    assert Ledger(db).should_skip("research::101::2026-05-18") is True


def test_scan_tick_non_proactive_skills_produce_no_errors(tmp_path):
    """End-to-end at the tick level: non-proactive skills next to a real
    proactive skill must NOT add anything to res.errors (the menu-bar app
    reads res.errors), while discovered/fired stay correct."""
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)

    nofm = sd / "newsletter-research"
    nofm.mkdir(parents=True)
    (nofm / "SKILL.md").write_text("# No fence here\n")

    trainer = sd / "trainer"
    trainer.mkdir(parents=True)
    (trainer / "SKILL.md").write_text(
        "---\nname: trainer\ndescription: x\n---\n\n# Trainer\n"
    )

    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=tmp_path / "proactive.db",
        token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    assert res.errors == []  # no false red noise
    assert res.skipped_non_proactive == 2
    assert res.discovered_jobs == 1
    assert res.fired_candidates == 1
    assert len(res.queue) == 1
    assert res.queue[0].job.id == "research-todoist"


def test_scan_tick_malformed_proactive_still_errors(tmp_path):
    """Regression guard: a skill WITH a `proactive:` block that fails to
    validate MUST still produce a res.errors[] entry — the fix must not
    suppress genuine malformed-proactive errors."""
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)

    bad = sd / "broken-proactive"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text(
        "---\nname: broken\nproactive:\n  - id: x\n---\n"  # missing required fields
    )

    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=tmp_path / "proactive.db",
        token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    assert any("broken-proactive" in e and "schema error" in e for e in res.errors)
    assert res.skipped_non_proactive == 0  # it HAS a proactive block
    # The valid skill is unaffected.
    assert res.discovered_jobs == 1
    assert res.queue[0].job.id == "research-todoist"


def test_scan_tick_governor_denies_when_breaker_tripped(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)
    db = tmp_path / "proactive.db"
    # Saturate the 5h invocation window so allow() returns ok=False.
    gov = Governor(db, max_invocations_5h=1)
    gov.record("m", 1)

    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=db,
        governor=gov,
        token="fake",
        todoist_fetcher=lambda *_: _one_task(),
    )
    assert res.governor_denied == 1
    assert res.queue == []
    # Denied -> key marked failed but cooldown still held (no retry storm).
    assert Ledger(db).should_skip("research::101::2026-05-18") is True


def test_scan_tick_caps_queue(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)
    db = tmp_path / "proactive.db"
    many = [
        {"id": str(i), "content": f"t{i}", "due": {"date": "2026-05-18"}}
        for i in range(10)
    ]
    res = scan_tick(
        now=NOW,
        skills_dir=sd,
        db_path=db,
        token="fake",
        todoist_fetcher=lambda *_: many,
        max_spawn_per_tick=3,
        queue_alert_threshold=5,
    )
    assert res.fired_candidates == 10
    assert len(res.queue) == 3  # hard cap
    assert res.queue_alert is True  # 10 survivors > 5 threshold (pre-trim)


def test_scan_tick_calendar_stub_does_not_abort_tick(tmp_path):
    sd = tmp_path / "skills"
    _make_skill(
        sd,
        "cal",
        textwrap.dedent(
            """\
            proactive:
              - id: cal-job
                trigger: calendar(window:48h)
                action: spawn_worker(prompt: x.md)
                idempotency_key: cal::{{source.id}}
                cooldown: 24h
              - id: ok-job
                trigger: manual
                action: spawn_worker(prompt: y.md)
                idempotency_key: ok::{{source.id}}
                cooldown: 24h
            """
        ),
    )
    res = scan_tick(now=NOW, skills_dir=sd, db_path=tmp_path / "db")
    # calendar stub recorded as error, manual job still queued.
    assert any("not implemented" in e for e in res.errors)
    assert len(res.queue) == 1
    assert res.queue[0].job.id == "ok-job"


# ---------- THE end-to-end anti-duplicate guarantee --------------------
def test_same_candidate_twice_one_cooldown_exactly_one_request(tmp_path):
    """Scan the SAME Todoist task on two consecutive ticks inside one 48h
    cooldown window. The engine MUST produce exactly ONE dispatchable
    WORKER_REQUEST across both ticks combined. This is the literal
    production regression (4 duplicate workers for one topic) proven dead
    at the scan level, not merely at the ledger level."""
    sd = tmp_path / "skills"
    _make_skill(sd, "research", _TODOIST_JOB)
    db = tmp_path / "proactive.db"

    fetch = lambda *_: _one_task(tid="555", due="2026-05-18")

    # --- Tick 1 ---
    res1 = scan_tick(
        now=NOW, skills_dir=sd, db_path=db, token="fake", todoist_fetcher=fetch
    )
    reqs1, _ = disp.build_dispatch(
        res1, prompt_base=sd / "research", write_state=False
    )

    # --- Tick 2: 1h later, SAME task, well within the 48h cooldown ---
    res2 = scan_tick(
        now=NOW + timedelta(hours=1),
        skills_dir=sd,
        db_path=db,
        token="fake",
        todoist_fetcher=fetch,
    )
    reqs2, _ = disp.build_dispatch(
        res2, prompt_base=sd / "research", write_state=False
    )

    total_requests = reqs1 + reqs2
    assert len(total_requests) == 1, (
        f"anti-duplicate violated: {len(total_requests)} WORKER_REQUESTs "
        f"for one task in one cooldown (expected exactly 1)"
    )
    assert total_requests[0]["idempotency_key"] == "research::555::2026-05-18"
    # Tick 1 produced it; tick 2 saw should_skip()/lost-claim and dropped it.
    assert len(reqs1) == 1
    assert len(reqs2) == 0
    assert res2.claim_skipped == 1
