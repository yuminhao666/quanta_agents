from __future__ import annotations

from contextlib import contextmanager
import signal
import threading
from collections.abc import Iterator


@contextmanager
def hard_timeout(seconds: int | float, message: str) -> Iterator[None]:
    """Raise TimeoutError on Unix when a blocking vendor SDK ignores request timeout.

    The normal HTTP client timeout remains the first line of defense. This guard is
    only for long-running batch agents, where one stuck model response must not block
    the entire replay/update run. Signals only work safely in the main thread, so the
    context manager becomes a no-op for worker threads.
    """

    if seconds <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return
    if not hasattr(signal, "setitimer"):
        yield
        return

    old_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError(message)

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
