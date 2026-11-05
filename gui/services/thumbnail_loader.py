"""Background thumbnail loading and image-dimension probing.

* A small worker pool (instead of one thread per thumbnail) decodes cached
  thumbnails into RGB PIL images, nearest-to-viewport first.
* Requests that scrolled out of view before a worker got to them are dropped.
* A separate probe thread reads image headers so the gallery can lay tiles out
  in their true aspect ratio.
"""
import itertools
import logging
import queue
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from PIL import Image

from backend.thumbnails import ThumbnailCache
from gui.services.ui_bridge import UiBridge

logger = logging.getLogger(__name__)

_STOP = -1.0


class ThumbnailLoader:
    def __init__(
        self,
        cache: ThumbnailCache,
        bridge: UiBridge,
        on_loaded: Callable[[str, Optional[Image.Image]], None],
        workers: int = 3,
    ):
        self._cache = cache
        self._bridge = bridge
        self._on_loaded = on_loaded
        self._queue: "queue.PriorityQueue[tuple]" = queue.PriorityQueue()
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._pending: Set[str] = set()
        self._wanted: Set[str] = set()
        self._probe_token = 0
        self._threads: List[threading.Thread] = []
        for n in range(workers):
            th = threading.Thread(target=self._worker, name=f"thumb-loader-{n}", daemon=True)
            th.start()
            self._threads.append(th)

    # ---- thumbnails -------------------------------------------------
    def set_wanted(self, paths: Iterable[str]) -> None:
        with self._lock:
            self._wanted = set(paths)

    def request(self, path: str, priority: float = 0.0) -> None:
        with self._lock:
            if path in self._pending:
                return
            self._pending.add(path)
        self._queue.put((max(priority, 0.0), next(self._seq), path))

    def _worker(self) -> None:
        while True:
            priority, _, path = self._queue.get()
            if priority == _STOP:
                return
            with self._lock:
                wanted = path in self._wanted
            img: Optional[Image.Image] = None
            if wanted:
                try:
                    thumb_path = self._cache.generate_thumbnail(path)
                    with Image.open(thumb_path) as im:
                        img = im.convert("RGB")
                        img.load()
                except Exception:
                    logger.debug("Thumbnail failed for %s", path, exc_info=True)
            with self._lock:
                self._pending.discard(path)
            if wanted:
                self._bridge.post(self._on_loaded, path, img)

    # ---- dimension probing -----------------------------------------
    def probe_sizes(
        self,
        items: List[Tuple[int, str]],
        on_batch: Callable[[Dict[int, Tuple[int, int]]], None],
        flush_every: float = 0.25,
    ) -> None:
        """Read (w, h) for every (slot, path); results arrive in batches on the UI thread."""
        with self._lock:
            self._probe_token += 1
            token = self._probe_token

        def run() -> None:
            batch: Dict[int, Tuple[int, int]] = {}
            last_flush = time.monotonic()
            for slot, path in items:
                with self._lock:
                    if token != self._probe_token:
                        return
                size = self._cache.get_display_size(path)
                if size:
                    batch[slot] = size
                if batch and time.monotonic() - last_flush >= flush_every:
                    self._bridge.post(on_batch, batch)
                    batch, last_flush = {}, time.monotonic()
            if batch:
                self._bridge.post(on_batch, batch)

        threading.Thread(target=run, name="thumb-probe", daemon=True).start()

    # ---- lifecycle --------------------------------------------------
    def shutdown(self) -> None:
        with self._lock:
            self._probe_token += 1
            self._wanted = set()
        for _ in self._threads:
            self._queue.put((_STOP, next(self._seq), ""))
