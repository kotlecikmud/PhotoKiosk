"""Thread-safe fan-out of server events (devices, scanner, library) to SSE clients."""
import asyncio
import threading
from typing import Any, Callable, Dict, Optional, Set, Tuple

Event = Tuple[str, Dict[str, Any]]


class EventHub:
    """Publish from any thread, consume from asyncio (one queue per SSE connection)."""

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subs: Set[asyncio.Queue] = set()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> "asyncio.Queue[Event]":
        q: "asyncio.Queue[Event]" = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: str, data: Dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event, data)
        except RuntimeError:
            pass

    def _fanout(self, event: str, data: Dict[str, Any]) -> None:
        for q in list(self._subs):
            if q.full():  # slow client: drop the oldest event rather than block everyone
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait((event, data))


class Debouncer:
    """Calls ``fn(count)`` once, ``delay`` seconds after the last ``poke()`` (count = pokes)."""

    def __init__(self, delay: float, fn: Callable[[int], None]):
        self._delay = delay
        self._fn = fn
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._count = 0

    def poke(self) -> None:
        with self._lock:
            self._count += 1
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            count, self._count, self._timer = self._count, 0, None
        self._fn(count)


def slim_scanner_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Scanner state without the (potentially huge) discovered photo list."""
    slim = {k: v for k, v in state.items() if k != "discovered_photos"}
    slim["discovered_count"] = len(state.get("discovered_photos", []))
    return slim
