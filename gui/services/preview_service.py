"""Asynchronous, cached, debounced print-preview rendering.

Why this removes flicker / lag:
* Only *inputs that change the picture* (photo, crop mode, paper size, box) trigger
  a render. Changing quantities never does.
* Finished previews are cached, so switching between size cards is instant.
* Requests are debounced and stale results are discarded.
* The old image stays on screen until the new one is ready.
* A down-scaled copy of the source is cached so large originals are decoded once.
"""
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from PIL import Image

from gui.services.ui_bridge import UiBridge
from imaging.preview import generate_preview

logger = logging.getLogger(__name__)

_PROXY_MAX_SIDE = 2000   # long edge of cached source proxy
_PROXY_CACHE_SIZE = 3
_RESULT_CACHE_SIZE = 32


@dataclass(frozen=True)
class PreviewRequest:
    filepath: str
    crop_mode: str
    size_name: str
    paper_w_mm: float
    paper_h_mm: float
    box: Tuple[int, int]
    border_mm: float = 4.0


class PreviewService:
    def __init__(
        self,
        widget,
        bridge: UiBridge,
        on_ready: Callable[[PreviewRequest, Dict[str, Any]], None],
        debounce_ms: int = 60,
    ):
        self._widget = widget
        self._bridge = bridge
        self._on_ready = on_ready
        self._debounce_ms = debounce_ms
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
        self._results: "OrderedDict[PreviewRequest, Dict[str, Any]]" = OrderedDict()
        self._proxies: "OrderedDict[str, Image.Image]" = OrderedDict()
        self._lock = threading.Lock()
        self._generation = 0
        self._timer: Optional[str] = None
        self._closed = False

    # ---- public (UI thread) ----------------------------------------
    def request(self, req: PreviewRequest) -> None:
        if self._closed:
            return
        self._generation += 1
        gen = self._generation
        with self._lock:
            cached = self._results.get(req)
            if cached is not None:
                self._results.move_to_end(req)
        if self._timer is not None:
            try:
                self._widget.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        if cached is not None:
            self._on_ready(req, cached)  # instant, no thread
            return
        self._timer = self._widget.after(self._debounce_ms, lambda: self._submit(req, gen))

    def close(self) -> None:
        self._closed = True
        if self._timer is not None:
            try:
                self._widget.after_cancel(self._timer)
            except Exception:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ---- internals --------------------------------------------------
    def _submit(self, req: PreviewRequest, gen: int) -> None:
        self._timer = None
        if not self._closed:
            self._executor.submit(self._render, req, gen)

    def _proxy(self, filepath: str) -> Image.Image:
        with self._lock:
            img = self._proxies.get(filepath)
            if img is not None:
                self._proxies.move_to_end(filepath)
                return img
        # Keep info (EXIF/DPI) so orientation handling stays identical to the full image.
        with Image.open(filepath) as src:
            try:
                src.draft("RGB", (_PROXY_MAX_SIDE, _PROXY_MAX_SIDE))  # fast JPEG downscale on decode
            except Exception:
                pass
            proxy = src.copy()
            proxy.info = dict(src.info)
            try:
                proxy.getexif()
            except Exception:
                pass
        proxy.thumbnail((_PROXY_MAX_SIDE, _PROXY_MAX_SIDE), Image.Resampling.BILINEAR)
        with self._lock:
            self._proxies[filepath] = proxy
            while len(self._proxies) > _PROXY_CACHE_SIZE:
                self._proxies.popitem(last=False)
        return proxy

    def _render(self, req: PreviewRequest, gen: int) -> None:
        if gen != self._generation:  # superseded while queued
            return
        try:
            result = self._generate(req)
        except Exception:
            logger.warning("Preview failed for %s", req.filepath, exc_info=True)
            return
        with self._lock:
            self._results[req] = result
            while len(self._results) > _RESULT_CACHE_SIZE:
                self._results.popitem(last=False)
        if gen == self._generation:
            self._bridge.post(self._deliver, req, result, gen)

    def _deliver(self, req: PreviewRequest, result: Dict[str, Any], gen: int) -> None:
        if not self._closed and gen == self._generation:
            self._on_ready(req, result)

    def _generate(self, req: PreviewRequest) -> Dict[str, Any]:
        kwargs = dict(
            crop_mode=req.crop_mode,
            paper_width_mm=req.paper_w_mm,
            paper_height_mm=req.paper_h_mm,
            border_width_mm=req.border_mm,
            preview_box_size=req.box,
            auto_orientation=True,
        )
        if req.crop_mode != "no_resize":
            try:
                return generate_preview(source_image=self._proxy(req.filepath), **kwargs)
            except Exception:
                logger.debug("Proxy preview failed, falling back to full image", exc_info=True)
        # no_resize depends on true pixel dimensions / DPI -> always use the original
        with Image.open(req.filepath) as img:
            return generate_preview(source_image=img, **kwargs)
