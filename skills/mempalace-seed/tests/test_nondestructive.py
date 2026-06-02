import pytest
from engine.nondestructive import ReadOnly, WriteAttemptError

class FakeCollection:
    def get(self, *a, **k): return {"ids": []}
    def query(self, *a, **k): return {"ids": [[]]}
    def add(self, *a, **k): raise AssertionError("real add called")
    def update(self, *a, **k): raise AssertionError("real update called")
    def delete(self, *a, **k): raise AssertionError("real delete called")
    def upsert(self, *a, **k): raise AssertionError("real upsert called")
    def modify(self, *a, **k): raise AssertionError("real modify called")

def test_reads_pass_through():
    ro = ReadOnly(FakeCollection())
    assert ro.get() == {"ids": []}

def test_writes_blocked_before_reaching_backend():
    ro = ReadOnly(FakeCollection())
    for op in ("add", "update", "delete", "upsert"):
        with pytest.raises(WriteAttemptError):
            getattr(ro, op)(ids=["x"])

def test_all_blocked_ops_raise_write_attempt_error():
    ro = ReadOnly(FakeCollection())
    for op in ("add", "update", "upsert", "delete", "modify"):
        with pytest.raises(WriteAttemptError):
            getattr(ro, op)()

def test_unknown_method_raises_attribute_error():
    ro = ReadOnly(FakeCollection())
    with pytest.raises(AttributeError):
        _ = ro.frobnicate

def test_old_underscore_b_handle_inaccessible():
    ro = ReadOnly(FakeCollection())
    with pytest.raises(AttributeError):
        _ = ro._b

def test_setattr_raises_write_attempt_error():
    ro = ReadOnly(FakeCollection())
    with pytest.raises(WriteAttemptError):
        ro.x = 1
