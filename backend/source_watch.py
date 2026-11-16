"""Live detection of photo sources (USB sticks, SD cards, discs, drive letters).

A background thread re-checks the available sources every couple of seconds (it is cheap) and
pushes the new list to every connected kiosk screen when - and only when - it changed. On Linux
it also mounts freshly plugged-in removable partitions, because a headless kiosk has no desktop
that would do it. This works with or without udev events, which only make it faster (``poke``).
"""
import logging
import sys
import threading
from typing import Any, Callable, Dict, List, Optional

from backend.sources import SourceRegistry, source_to_dict
from devices.storage import StorageManager

logger = logging.getLogger(__name__)


class SourceWatcher:
    def __init__(
        self,
        registry: SourceRegistry,
        publish: Callable[[str, Dict[str, Any]], None],
        interval: float = 2.0,
        automount: bool = True,
    ):
        self.registry = registry
        self.publish = publish
        self.interval = interval
        self.automount = automount and sys.platform != "win32"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last: Optional[List[Dict[str, Any]]] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="source-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        """Check right now (called on device events / settings changes)."""
        self._wake.set()

    def check_once(self) -> bool:
        """Runs one detection round; publishes and returns True if the source list changed."""
        if self.automount:
            try:
                StorageManager.mount_pending_removables()
            except Exception:
                logger.warning("auto-mount failed", exc_info=True)
        sources = [source_to_dict(s) for s in self.registry.discover()]
        changed = self._last is not None and sources != self._last
        self._last = sources
        if changed:
            self.publish("sources", {"sources": sources})
        return changed

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception:
                logger.warning("source check failed", exc_info=True)
            self._wake.wait(self.interval)
            self._wake.clear()
