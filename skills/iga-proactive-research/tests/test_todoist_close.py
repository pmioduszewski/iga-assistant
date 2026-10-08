"""Close-the-loop post-step: label removal + one TL;DR comment, idempotent."""

import pytest

import todoist_close as tc


class _FakeCollection:
    def __init__(self, rows):
        self.rows = rows  # (id, document, metadata)

    def get(self, where=None, include=None, limit=None):
        conds = where["$and"]
        hits = [r for r in self.rows if all(r[2].get(k) == v for c in conds for k, v in c.items())]
        return {
            "ids": [h[0] for h in hits],
            "documents": [h[1] for h in hits],
            "metadatas": [h[2] for h in hits],
        }


class _FakePalace:
    def __init__(self, rows=None, error=None):
        self._col = _FakeCollection(rows or [])
        self._error = error

    def _get_collection(self):
        if self._error:
            raise self._error
        return self._col


_META = {"room": "research", "source_file": "todoist:101", "parent_drawer_id": "drawer_p_research_abc",
         "filed_at": "2026-06-27T08:26:03"}
_ROWS = [
    ("drawer_p_research_abc_chunk_000001", "SOURCES: x", dict(_META, chunk_index=1)),
    ("drawer_p_research_abc_chunk_000000", "RESEARCH:h|2026-06-30|depth:deep|***\nTLDR: Use lib A.\nFINDINGS:",
     dict(_META, chunk_index=0)),
    ("drawer_q_rq_1", "TLDR: nope", {"room": "research-queue", "source_file": "todoist:101"}),
]


class _FakeTodoist:
    def __init__(self, labels, comments=()):
        self.labels = list(labels)
        self.comments = [{"content": c} for c in comments]
        self.calls = []

    def __call__(self, method, path, body, token):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/tasks/"):
            return {"id": "101", "labels": list(self.labels)}
        if method == "POST" and path.startswith("/tasks/"):
            self.labels = body["labels"]
            return {}
        if method == "GET" and path.startswith("/comments"):
            return {"results": list(self.comments), "next_cursor": None}
        if method == "POST" and path == "/comments":
            self.comments.append({"content": body["content"]})
            return {}
        raise AssertionError((method, path))


_REQ = {"trigger_kind": "todoist", "context": {"task.id": "101"}}


def test_find_research_drawer_resolves_parent_id_and_tldr():
    d = tc.find_research_drawer(_FakePalace(_ROWS), "todoist:101")
    assert d == {"drawer_id": "drawer_p_research_abc", "tldr": "Use lib A."}
    assert tc.find_research_drawer(_FakePalace(_ROWS), "todoist:999") is None


def test_close_removes_only_research_label_and_comments_once():
    td = _FakeTodoist(["work", "iga-research", "deep"])
    out = tc.close_for_request(_REQ, mempalace_mod=_FakePalace(_ROWS), token="t", http=td)
    assert out["label_removed"] is True and out["comment"] == "posted"
    assert td.labels == ["work", "deep"]
    assert td.comments == [{"content": "[Iga prepared] Use lib A.\nDrawer: drawer_p_research_abc"}]

    again = tc.close_for_request(_REQ, mempalace_mod=_FakePalace(_ROWS), token="t", http=td)
    assert again["label_removed"] is False and again["comment"] == "exists"
    assert len(td.comments) == 1
    assert not any(m == "POST" for m, _, _ in td.calls[-2:])


def test_no_drawer_leaves_task_untouched():
    td = _FakeTodoist(["iga-research"])
    out = tc.close_for_request(_REQ, mempalace_mod=_FakePalace([]), token="t", http=td)
    assert "no research drawer" in out["skipped"]
    assert td.calls == [] and td.labels == ["iga-research"]


def test_non_todoist_request_skipped():
    out = tc.close_for_request({"trigger_kind": "mempalace", "context": {}}, mempalace_mod=None, token="t")
    assert "not a todoist" in out["skipped"]


@pytest.mark.parametrize("palace", [_FakePalace(error=OSError("locked"))])
def test_palace_error_reported_not_raised(palace):
    out = tc.close_for_request(_REQ, mempalace_mod=palace, token="t", http=_FakeTodoist([]))
    assert "palace query failed" in out["error"]


def test_todoist_error_reported_not_raised():
    def boom(method, path, body, token):
        raise tc.CloseError("GET /tasks/101: 503")

    out = tc.close_for_request(_REQ, mempalace_mod=_FakePalace(_ROWS), token="t", http=boom)
    assert "503" in out["error"]


def test_missing_token_reported():
    out = tc.close_for_request(_REQ, mempalace_mod=_FakePalace(_ROWS), token=None, http=_FakeTodoist([]))
    assert out["error"] == "no Todoist token"
