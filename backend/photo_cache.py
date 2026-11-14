"""In-memory caches that make the web kiosk responsive.

* ``DimsCache``       - oriented (w, h) of photos, so the browser can lay out a justified
                        gallery without downloading any image first.
* ``PreviewRenderer`` - renders the "what will be printed" preview. Decoded sources are kept
                        in a small down-scaled proxy cache and finished JPEGs in a result
                        cache, so switching between print-size cards is instant.
"""
import io
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from PIL import Image

from backend.thumbnails import probe_oriented_size
from imaging.preview import generate_preview

PROXY_MAX_SIDE = 2000
PROXY_CACHE_SIZE = 3
RESULT_CACHE_SIZE = 64
DIMS_CACHE_SIZE = 50000


class _LRU(OrderedDict):
    def __init__(self, capacity: int):
        super().__init__()
        self.capacity = capacity

    def touch(self, key):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return None

    def put(self, key, value):
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.capacity:
            self.popitem(last=False)


def _file_key(path: Path) -> Tuple[str, int, int]:
    st = path.stat()
    return (str(path), st.st_size, st.st_mtime_ns)


class DimsCache:
    """Thread-safe cache of oriented photo dimensions keyed by (path, size, mtime)."""

    def __init__(self, workers: int = 8):
        self._lock = threading.Lock()
        self._cache = _LRU(DIMS_CACHE_SIZE)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dims")

    @staticmethod
    def _key(abs_path: str, size: int, mtime: float) -> Tuple[str, int, int]:
        return (abs_path, int(size), int(mtime * 1e9))

    def peek(self, abs_path: str, size: int, mtime: float) -> Optional[Tuple[int, int]]:
        with self._lock:
            return self._cache.touch(self._key(abs_path, size, mtime))

    def probe_many(self, paths: Iterable[Path]) -> Dict[Path, Optional[Tuple[int, int]]]:
        """Probe image headers in parallel (I/O bound, helps on slow USB media)."""
        paths = list(paths)

        def one(p: Path):
            try:
                st = p.stat()
                key = self._key(str(p), st.st_size, st.st_mtime)
                with self._lock:
                    hit = self._cache.touch(key)
                if hit:
                    return hit
                size = probe_oriented_size(str(p))
                if size:
                    with self._lock:
                        self._cache.put(key, size)
                return size
            except OSError:
                return None

        return dict(zip(paths, self._pool.map(one, paths)))


class PreviewRenderer:
    """Renders and caches print previews as JPEG bytes plus a small metadata dict."""

    def __init__(self):
        self._lock = threading.Lock()
        self._results = _LRU(RESULT_CACHE_SIZE)
        self._proxies = _LRU(PROXY_CACHE_SIZE)

    def _proxy(self, path: Path) -> Image.Image:
        key = _file_key(path)
        with self._lock:
            img = self._proxies.touch(key)
        if img is not None:
            return img
        with Image.open(path) as src:
            try:
                src.draft("RGB", (PROXY_MAX_SIDE, PROXY_MAX_SIDE))  # fast JPEG downscale on decode
            except Exception:
                pass
            proxy = src.copy()
            proxy.info = dict(src.info)  # keep EXIF / DPI so orientation maths stay identical
        proxy.thumbnail((PROXY_MAX_SIDE, PROXY_MAX_SIDE), Image.Resampling.BILINEAR)
        with self._lock:
            self._proxies.put(key, proxy)
        return proxy

    def render(
        self,
        path: Path,
        crop_mode: str,
        paper_w_mm: float,
        paper_h_mm: float,
        box: Tuple[int, int],
        border_mm: float,
        auto_orientation: bool,
    ) -> Tuple[bytes, Dict[str, Any]]:
        key = (_file_key(path), crop_mode, round(paper_w_mm, 2), round(paper_h_mm, 2), box,
               round(border_mm, 2), auto_orientation)
        with self._lock:
            cached = self._results.touch(key)
        if cached is not None:
            return cached

        kwargs = dict(
            crop_mode=crop_mode,
            paper_width_mm=paper_w_mm,
            paper_height_mm=paper_h_mm,
            border_width_mm=border_mm,
            preview_box_size=box,
            auto_orientation=auto_orientation,
        )
        result: Optional[Dict[str, Any]] = None
        if crop_mode != "no_resize":
            try:
                result = generate_preview(source_image=self._proxy(path), **kwargs)
            except Exception:
                result = None  # fall back to the full-size image below
        if result is None:
            # no_resize depends on true pixel dimensions / DPI, so it always uses the original
            with Image.open(path) as img:
                result = generate_preview(source_image=img, **kwargs)

        buf = io.BytesIO()
        result["preview_image"].save(buf, format="JPEG", quality=82)
        meta = {
            "paper_mm": list(result["paper_physical_mm"]),
            "displayed_mm": list(result["displayed_physical_mm"]),
            "fits": bool(result["fits_paper"]),
            "warnings": list(result["warnings"]),
            "dpi": list(result["dpi"]),
        }
        value = (buf.getvalue(), meta)
        with self._lock:
            self._results.put(key, value)
        return value
