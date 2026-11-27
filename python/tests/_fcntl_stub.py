"""fcntl stand-in for importing the daemons in-process (virtuoso-cli-integration).

Keeps the real flock/LOCK_* where they exist, so the daemon's cross-bridge lock
still works in these tests; no-ops them only where fcntl is missing (Windows).
"""
import types

try:
    import fcntl as _real
except ImportError:
    _real = None


def fcntl_stub():
    return types.SimpleNamespace(
        fcntl=lambda *args, **kwargs: 0,
        F_GETFL=3,
        F_SETFL=4,
        flock=getattr(_real, "flock", lambda *args, **kwargs: None),
        LOCK_SH=getattr(_real, "LOCK_SH", 1),
        LOCK_EX=getattr(_real, "LOCK_EX", 2),
        LOCK_NB=getattr(_real, "LOCK_NB", 4),
        LOCK_UN=getattr(_real, "LOCK_UN", 8),
    )
