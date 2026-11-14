"""Background staging of selected photos into a per-cart temp folder.

Copying a photo the moment it is added to the order lets the customer unplug their USB
stick before submitting, and makes submitting fast. Each staged file lives in its own
sub-folder (hash of source+photo id) so two photos called ``IMG_0001.jpg`` from different
folders can never overwrite each other.
"""
import hashlib
import logging
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CART_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
STALE_CART_SECONDS = 12 * 3600


class CartStager:
    def __init__(self, temp_base: str = "storage/temp_sessions"):
        self.temp_base = Path(temp_base)
        self._lock = threading.Lock()
        self._done: Dict[tuple, Path] = {}
        self._queued: set = set()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stager")

    @staticmethod
    def valid_cart_id(cart_id: str) -> bool:
        return bool(CART_ID_RE.match(cart_id or ""))

    def _cart_dir(self, cart_id: str) -> Path:
        return self.temp_base / f"cart_{cart_id}"

    def _dest(self, cart_id: str, source_id: str, photo_id: str, name: str) -> Path:
        digest = hashlib.sha1(f"{source_id}/{photo_id}".encode("utf-8")).hexdigest()[:16]
        return self._cart_dir(cart_id) / digest / name

    def stage(self, cart_id: str, source_id: str, photo_id: str, src: Path) -> None:
        key = (cart_id, source_id, photo_id)
        with self._lock:
            if key in self._done or key in self._queued:
                return
            self._queued.add(key)
        self._pool.submit(self._copy, key, src)

    def _copy(self, key: tuple, src: Path) -> None:
        cart_id, source_id, photo_id = key
        try:
            dest = self._dest(cart_id, source_id, photo_id, src.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            shutil.copy2(src, tmp)
            tmp.replace(dest)
            with self._lock:
                if key in self._queued:  # not unstaged while copying
                    self._done[key] = dest
                else:
                    shutil.rmtree(dest.parent, ignore_errors=True)
        except Exception:
            logger.warning("Could not stage %s", src, exc_info=True)
        finally:
            with self._lock:
                self._queued.discard(key)

    def unstage(self, cart_id: str, source_id: str, photo_id: str) -> None:
        key = (cart_id, source_id, photo_id)
        with self._lock:
            self._queued.discard(key)
            dest = self._done.pop(key, None)
        if dest:
            shutil.rmtree(dest.parent, ignore_errors=True)

    def staged_path(self, cart_id: str, source_id: str, photo_id: str) -> Optional[Path]:
        with self._lock:
            dest = self._done.get((cart_id, source_id, photo_id))
        return dest if dest and dest.is_file() else None

    def discard(self, cart_id: str) -> None:
        with self._lock:
            for key in [k for k in list(self._done) + list(self._queued) if k[0] == cart_id]:
                self._done.pop(key, None)
                self._queued.discard(key)
        shutil.rmtree(self._cart_dir(cart_id), ignore_errors=True)

    def sweep_stale(self) -> None:
        """Remove cart folders left behind by abandoned sessions."""
        if not self.temp_base.exists():
            return
        cutoff = time.time() - STALE_CART_SECONDS
        for d in self.temp_base.glob("cart_*"):
            try:
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
