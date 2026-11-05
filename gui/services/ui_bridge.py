"""Thread-safe hand-off of background results to the Tk main thread.

Tkinter widgets must only be touched from the main thread, and calling
``widget.after`` from worker threads is not reliably safe. Workers therefore
``post()`` callables into a queue which the main thread drains on a timer.
"""
import logging
import queue
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class UiBridge:
    def __init__(self, widget, interval_ms: int = 30, max_per_tick: int = 64):
        self._widget = widget
        self._interval = interval_ms
        self._max_per_tick = max_per_tick
        self._queue: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        self._after_id: Optional[str] = None
        self._closed = False
        self._schedule()

    def post(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        """Queue ``fn(*args, **kwargs)`` to run on the UI thread (callable from any thread)."""
        if not self._closed:
            self._queue.put((fn, args, kwargs))

    def close(self) -> None:
        self._closed = True
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _schedule(self) -> None:
        if self._closed:
            return
        try:
            self._after_id = self._widget.after(self._interval, self._poll)
        except Exception:  # widget already destroyed
            self._closed = True

    def _poll(self) -> None:
        self._after_id = None
        if self._closed:
            return
        for _ in range(self._max_per_tick):
            try:
                fn, args, kwargs = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("UI callback failed")
        self._schedule()
