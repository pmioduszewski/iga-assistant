class WriteAttemptError(RuntimeError):
    """Raised when a write operation is attempted on a read-only palace handle."""
    pass


_BLOCKED = {"add", "update", "upsert", "delete", "modify", "_modify"}
_ALLOWED = {"get", "query", "count", "peek"}


class ReadOnly:
    """Wraps a Chroma collection (or any palace store) and hard-blocks writes
    so the export pipeline can NEVER mutate the live palace."""

    def __init__(self, backend):
        self._b = backend

    def __getattr__(self, name):
        if name in _BLOCKED:
            def _blocked(*a, **k):
                raise WriteAttemptError(f"write op '{name}' blocked (read-only export)")
            return _blocked
        if name in _ALLOWED:
            return getattr(self._b, name)
        raise AttributeError(name)
