class WriteAttemptError(RuntimeError):
    """Raised when a write operation is attempted on a read-only palace handle."""
    pass


_BLOCKED = {"add", "update", "upsert", "delete", "modify", "_modify"}
_ALLOWED = {"get", "query", "count", "peek"}


class ReadOnly:
    """Wraps a palace collection and hard-blocks writes so the export pipeline
    can NEVER mutate the live palace. The backend is stored under a mangled,
    slotted name so it cannot be reached via normal attribute access."""
    __slots__ = ("_ReadOnly__backend",)

    def __init__(self, backend):
        object.__setattr__(self, "_ReadOnly__backend", backend)

    def __getattr__(self, name):
        if name in _BLOCKED:
            def _blocked(*a, **k):
                raise WriteAttemptError(f"write op '{name}' blocked (read-only export)")
            return _blocked
        if name in _ALLOWED:
            backend = object.__getattribute__(self, "_ReadOnly__backend")
            return getattr(backend, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        raise WriteAttemptError("read-only handle is immutable")
