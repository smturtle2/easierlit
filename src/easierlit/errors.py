class EasierlitError(Exception):
    """Base exception for Easierlit."""


class DataPersistenceNotEnabledError(EasierlitError):
    """Raised when Chainlit data persistence is required but not configured."""


class ThreadSessionNotActiveError(EasierlitError):
    """Raised when a thread has no active websocket session and no data layer fallback."""


class WorkerAlreadyRunningError(EasierlitError):
    """Raised when run() is called while the worker is already running."""


class WorkerNotRunningError(EasierlitError):
    """Raised when an operation expects a running worker but none exists."""


class RunFuncExecutionError(EasierlitError):
    """Raised when run_func crashes inside a worker."""


class AppClosedError(EasierlitError):
    """Raised when using EasierlitApp after close()."""
