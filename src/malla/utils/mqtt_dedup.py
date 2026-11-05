"""Bounded, process-local MQTT duplicate suppression.

Only an envelope successfully persisted to the database should be remembered.
The MQTT callback runs serially for its client; lookups are additionally guarded
against concurrent access. The cache intentionally resets on process restart.
"""

import hashlib
import threading
import time
from collections import OrderedDict
from collections.abc import Callable


class RecentMQTTMessages:
    """Track exact topic + payload pairs within a bounded time window."""

    def __init__(
        self,
        window_seconds: float = 600,
        max_entries: int = 100_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if window_seconds < 0:
            raise ValueError("window_seconds must be nonnegative")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.window_seconds = window_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._seen: OrderedDict[bytes, float] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(topic: str, payload: bytes) -> bytes:
        """Hash both the exact MQTT topic and the original envelope bytes."""
        digest = hashlib.sha256()
        digest.update(topic.encode("utf-8"))
        digest.update(b"\x00")  # MQTT topic names cannot contain a NUL.
        digest.update(payload)
        return digest.digest()

    def _expire(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._seen:
            first_key, first_seen = next(iter(self._seen.items()))
            if first_seen > cutoff:
                break
            self._seen.pop(first_key)

    def contains(self, fingerprint: bytes) -> bool:
        """Return True only for a recently persisted envelope."""
        if self.window_seconds == 0:
            return False
        with self._lock:
            self._expire(self._clock())
            return fingerprint in self._seen

    def remember(self, fingerprint: bytes) -> None:
        """Record an envelope AFTER successful database persistence."""
        if self.window_seconds == 0:
            return
        with self._lock:
            now = self._clock()
            self._expire(now)
            # A refreshed entry must be the newest in insertion order.
            self._seen.pop(fingerprint, None)
            self._seen[fingerprint] = now
            while len(self._seen) > self.max_entries:
                self._seen.popitem(last=False)
