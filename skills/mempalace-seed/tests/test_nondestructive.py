import pytest
from engine.nondestructive import ReadOnly, WriteAttemptError

class FakeCollection:
    def get(self, *a, **k): return {"ids": []}
    def query(self, *a, **k): return {"ids": [[]]}
    def add(self, *a, **k): raise AssertionError("real add called")
    def update(self, *a, **k): raise AssertionError("real update called")
    def delete(self, *a, **k): raise AssertionError("real delete called")

def test_reads_pass_through():
    ro = ReadOnly(FakeCollection())
    assert ro.get() == {"ids": []}

def test_writes_blocked_before_reaching_backend():
    ro = ReadOnly(FakeCollection())
    for op in ("add", "update", "delete", "upsert"):
        with pytest.raises(WriteAttemptError):
            getattr(ro, op)(ids=["x"])
