"""Thread-safe request pacing shared by every rate-limited source."""

import threading
import time
from collections.abc import Callable, Hashable


class RequestPacer:
    """Keeps at least ``interval`` seconds between request starts for each key (e.g. a host).

    Callers reserve the next free slot under a lock and sleep outside it, so concurrent callers for one key are
    spaced out while callers for different keys never wait on each other.
    """

    def __init__(
        self,
        interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if interval < 0:
            raise ValueError('interval must be nonnegative')
        self.interval = interval
        self._clock = clock
        self._sleep = sleep
        self._next: dict[Hashable | None, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: Hashable | None = None) -> float:
        """Block until this caller may start a request for ``key``; return the seconds waited."""
        with self._lock:
            now = self._clock()
            slot = max(now, self._next.get(key, now))
            self._next[key] = slot + self.interval
        delay = slot - now
        if delay > 0:
            self._sleep(delay)
        return delay
