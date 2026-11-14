"""Browser-facing API of the web kiosk (``/api/v1/kiosk``).

Design rules
------------
* The browser never handles file-system paths: sources and photos are opaque ids that the
  server resolves with :class:`backend.sources.SourceRegistry` (traversal-proof).
* Prices are computed on the server; the browser only displays them.
* Slow work (thumbnails, previews, order processing) never blocks the event loop; every
  endpoint that touches disk is a plain ``def`` and runs in FastAPI's thread pool.
"""
import asyncio
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import sessionmaker

from backend.catalog import PrintCatalog
from backend.database import SystemSetting, get_db_engine
from backend.events import Debouncer, EventHub, slim_scanner_state
from backend.i18n import BASE_LOCALE, get_i18n
from backend.jobs import OrderJobs
from backend.library import PhotoLibraryWatcher
from backend.orders import OrderProcessor
from backend.photo_cache import DimsCache, PreviewRenderer
from backend.sources import DEFAULT_ENABLED, SourceError, SourceNotFound, SourceRegistry
from backend.staging import CartStager
from backend.theme import get_theme_colors, resolve_theme_key
from backend.thumbnails import ThumbnailCache
from devices.monitor import DeviceMonitorDaemon
from devices.scanner import authoritative_scanner
from devices.session_manager import (
    clean_all_temp_sessions,
    filter_photos_by_date_range,
    get_date_range_bounds,
    stage_session_thumbnails,
)

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"
CROP_MODES = ("fill_in", "fit_in", "border", "no_resize")
DATE_RANGES = ("1_month", "3_months", "6_months", "all")
SETTINGS_TTL_SECONDS = 3.0
MAX_ORDER_ITEMS = 5000


def load_kiosk_config(path: str = "config/settings.yaml") -> Dict[str, Any]:
    """Reads the ``kiosk:`` section of settings.yaml (missing file / keys -> defaults)."""
    cfg: Dict[str, Any] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = (yaml.safe_load(f) or {}).get("kiosk", {}) or {}
    except (OSError, yaml.YAMLError):
        pass
    return {"auto_reset_seconds": max(0, int(cfg.get("auto_reset_timeout_seconds", 60) or 0))}


class KioskServices:
    """Everything the kiosk API shares. Stored on ``app.state.kiosk`` (replaceable in tests)."""

    def __init__(
        self,
        shared_dir: str = "storage/shared",
        temp_base: str = "storage/temp_sessions",
        thumbs_dir: str = "storage/thumbnails",
        orders_dir: str = "storage/orders",
    ):
        self.boot_id = secrets.token_hex(6)
        self.hub = EventHub()
        self.registry = SourceRegistry(shared_dir, temp_base, enabled_provider=self._enabled_sources)
        self.thumbs = ThumbnailCache(thumbs_dir)
        # leave a core for the browser that runs on the same machine
        self.thumb_gate = asyncio.Semaphore(max(1, min(4, (os.cpu_count() or 2) - 1)))
        self.dims = DimsCache()
        self.previews = PreviewRenderer()
        self.stager = CartStager(temp_base)
        self.jobs = OrderJobs()
        self.processor = OrderProcessor(orders_dir)
        self.shared_dir = shared_dir
        self.temp_base = temp_base
        self._settings: Dict[str, str] = {}
        self._settings_at = 0.0
        self._lock = threading.Lock()
        self._monitor: Optional[DeviceMonitorDaemon] = None
        self._watcher: Optional[PhotoLibraryWatcher] = None
        self._unsubscribe_scanner = None

    # ---- database-backed settings (short TTL cache: previews hit this often) ----
    def settings(self) -> Dict[str, str]:
        with self._lock:
            if time.monotonic() - self._settings_at < SETTINGS_TTL_SECONDS and self._settings:
                return self._settings
        with sessionmaker(bind=get_db_engine())() as db:
            fresh = {s.key: s.value for s in db.query(SystemSetting).all()}
        with self._lock:
            self._settings, self._settings_at = fresh, time.monotonic()
        return fresh

    def _enabled_sources(self) -> Dict[str, bool]:
        s = self.settings()
        return {k: s.get(k, "true").lower() == "true" for k in DEFAULT_ENABLED}

    def catalog(self) -> PrintCatalog:
        return PrintCatalog.load()

    # ---- background services (started from the app lifespan) ----
    def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        if loop is not None:
            self.hub.bind_loop(loop)
        clean_all_temp_sessions(self.temp_base)  # leftovers of a previous run
        self.stager.sweep_stale()
        self._unsubscribe_scanner = authoritative_scanner.subscribe(
            lambda _event, state: self.hub.publish("scanner", slim_scanner_state(state))
        )
        self._monitor = DeviceMonitorDaemon(self._on_device_event)
        self._monitor.start()

        shared = os.path.abspath(self.shared_dir)
        os.makedirs(shared, exist_ok=True)
        notify = Debouncer(1.5, lambda n: self.hub.publish("library", {"changes": n}))
        self._watcher = PhotoLibraryWatcher(shared, on_change_callback=lambda *_: notify.poke())
        self._watcher.start()

    def stop(self) -> None:
        if self._unsubscribe_scanner:
            self._unsubscribe_scanner()
        if self._monitor:
            self._monitor.stop()
        if self._watcher:
            self._watcher.stop()

    def _on_device_event(self, action: str, details: Dict[str, Any]) -> None:
        self.hub.publish("device", {
            "action": action,
            "type": details.get("type", ""),
            "device": details.get("device", ""),
        })


def services(request: Request) -> KioskServices:
    return request.app.state.kiosk


def _http(exc: SourceError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, SourceNotFound) else 400, detail=str(exc))


router = APIRouter(prefix="/api/v1/kiosk", tags=["kiosk-web"])


# ---------------------------------------------------------------------------
# Bootstrap & localisation
# ---------------------------------------------------------------------------
@router.get("/bootstrap")
def bootstrap(request: Request):
    svc = services(request)
    settings = svc.settings()
    i18n = get_i18n()
    available = i18n.get_available_languages()
    default_lang = settings.get("system_language", BASE_LOCALE)
    if default_lang not in available:
        default_lang = BASE_LOCALE
    theme_key = resolve_theme_key(settings.get("ui_theme"))
    return {
        "version": APP_VERSION,
        "boot_id": svc.boot_id,
        "catalog": svc.catalog().to_dict(),
        "settings": {
            "border_width_mm": float(settings.get("border_width_mm", 4.0)),
            "currency": "PLN",
        },
        "theme": {"key": theme_key, "colors": get_theme_colors(theme_key)},
        "language": {
            "default": default_lang,
            "available": [{"code": c, "name": i18n.locale_names.get(c, c.upper())} for c in available],
        },
        "kiosk": load_kiosk_config(),
    }


@router.get("/i18n/{lang}")
def i18n_strings(lang: str):
    i18n = get_i18n()
    lang = lang.lower()
    if lang not in i18n.translations:
        raise HTTPException(status_code=404, detail="unknown language")
    strings = dict(i18n.translations.get(BASE_LOCALE, {}))  # base first: never a missing key
    strings.update(i18n.translations[lang])
    return {"lang": lang, "strings": strings}


# ---------------------------------------------------------------------------
# Sources & photos
# ---------------------------------------------------------------------------
def _phone_summary(enabled: Dict[str, bool]) -> Optional[Dict[str, Any]]:
    state = authoritative_scanner.get_state()
    if state.get("connection_status") != "connected":
        return None
    if not (enabled.get("source_iphone") or enabled.get("source_android")):
        return None
    return slim_scanner_state(state)


@router.get("/sources")
def list_sources(request: Request):
    svc = services(request)
    sources = svc.registry.discover()
    return {
        "sources": [{"id": s.id, "kind": s.kind, "label": s.label} for s in sources],
        "phone": _phone_summary(svc.registry.enabled_flags()),
    }


@router.get("/sources/{source_id}/photos")
def list_photos(request: Request, source_id: str):
    svc = services(request)
    try:
        source = svc.registry.get(source_id)
    except SourceError as exc:
        raise _http(exc)
    photos = svc.registry.list_photos(source)
    for p in photos:
        abs_path = p.pop("_abs")
        dims = svc.dims.peek(abs_path, p["size"], p["mtime"])
        p["w"], p["h"] = dims if dims else (None, None)
    return {"source_id": source.id, "count": len(photos), "photos": photos}


class DimsRequest(BaseModel):
    ids: List[str] = Field(max_length=400)


@router.post("/sources/{source_id}/dims")
def photo_dims(request: Request, source_id: str, body: DimsRequest):
    svc = services(request)
    paths: Dict[str, Path] = {}
    for pid in body.ids:
        try:
            paths[pid] = svc.registry.resolve_photo(source_id, pid)
        except SourceNotFound:
            continue
        except SourceError as exc:
            raise _http(exc)
    probed = svc.dims.probe_many(paths.values())
    return {"dims": {pid: (list(probed[p]) if probed.get(p) else None) for pid, p in paths.items()}}


@router.get("/sources/{source_id}/photos/{photo_id}/thumb")
async def thumbnail(request: Request, source_id: str, photo_id: str, v: Optional[str] = None):
    svc = services(request)

    def lookup():
        path = svc.registry.resolve_photo(source_id, photo_id)
        cached = svc.thumbs.get_thumbnail_path(str(path)) if svc.thumbs.has_valid_thumbnail(str(path)) else None
        return path, cached

    try:
        path, thumb = await run_in_threadpool(lookup)
    except SourceError as exc:
        raise _http(exc)
    if thumb is None:
        # Generating thumbnails is CPU work that shares the machine with the browser, so it
        # is rationed. While a request waits here the browser may scroll on and abort it;
        # such orphaned requests are dropped instead of being generated for nobody.
        async with svc.thumb_gate:
            if await request.is_disconnected():
                return Response(status_code=499)
            try:
                thumb = await run_in_threadpool(svc.thumbs.generate_thumbnail, str(path))
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"cannot read image: {exc}")
    # The URL carries the file's mtime (?v=), so the browser may cache it "forever".
    return FileResponse(str(thumb), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/sources/{source_id}/photos/{photo_id}/preview")
def preview(
    request: Request,
    source_id: str,
    photo_id: str,
    size: str,
    crop: str = "fill_in",
    w: int = 600,
    h: int = 500,
):
    svc = services(request)
    if crop not in CROP_MODES:
        raise HTTPException(status_code=400, detail=f"unknown crop mode: {crop}")
    try:
        path = svc.registry.resolve_photo(source_id, photo_id)
    except SourceError as exc:
        raise _http(exc)
    spec = svc.catalog().size(size)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"unknown print size: {size}")
    settings = svc.settings()
    box = (max(100, min(w, 1600)), max(100, min(h, 1600)))
    try:
        body, meta = svc.previews.render(
            path, crop, spec.width_mm, spec.height_mm, box,
            border_mm=float(settings.get("border_width_mm", 4.0)),
            auto_orientation=settings.get("auto_orientation_matching", "true").lower() == "true",
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"cannot render preview: {exc}")
    return Response(body, media_type="image/jpeg", headers={
        "X-Preview-Meta": json.dumps(meta),  # ASCII-only JSON: safe in a header
        "Cache-Control": "private, max-age=120",
    })


# ---------------------------------------------------------------------------
# Cart staging
# ---------------------------------------------------------------------------
class StageRequest(BaseModel):
    source_id: str
    add: List[str] = Field(default_factory=list, max_length=5000)
    remove: List[str] = Field(default_factory=list, max_length=5000)


@router.put("/carts/{cart_id}/staged", status_code=202)
def update_staged(request: Request, cart_id: str, body: StageRequest):
    svc = services(request)
    if not svc.stager.valid_cart_id(cart_id):
        raise HTTPException(status_code=400, detail="invalid cart id")
    for pid in body.add:
        try:
            svc.stager.stage(cart_id, body.source_id, pid, svc.registry.resolve_photo(body.source_id, pid))
        except SourceError:
            continue
    for pid in body.remove:
        svc.stager.unstage(cart_id, body.source_id, pid)
    return {"ok": True}


@router.delete("/carts/{cart_id}", status_code=204)
def discard_cart(request: Request, cart_id: str):
    svc = services(request)
    if svc.stager.valid_cart_id(cart_id):
        svc.stager.discard(cart_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
class KioskOrderItem(BaseModel):
    source_id: str
    photo_id: str
    size: str
    finish: str
    crop: Literal["fill_in", "fit_in", "border", "no_resize"]
    qty: int = Field(ge=1, le=999)


class KioskOrderRequest(BaseModel):
    items: List[KioskOrderItem] = Field(min_length=1, max_length=MAX_ORDER_ITEMS)
    cart_id: Optional[str] = None
    request_id: Optional[str] = Field(default=None, max_length=64)


@router.post("/orders", status_code=202)
def submit_order(request: Request, body: KioskOrderRequest):
    svc = services(request)
    catalog = svc.catalog()
    finishes = {f.upper(): f for f in catalog.finish_names}
    cart_id = body.cart_id if svc.stager.valid_cart_id(body.cart_id or "") else None

    payload: List[Dict[str, Any]] = []
    total_price = 0.0
    for it in body.items:
        if catalog.size(it.size) is None:
            raise HTTPException(status_code=400, detail=f"unknown print size: {it.size}")
        if it.finish.upper() not in finishes:
            raise HTTPException(status_code=400, detail=f"unknown paper finish: {it.finish}")
        # A staged copy exists only for ids that already passed resolve_photo(), and it keeps
        # working after the customer unplugs the USB stick - so prefer it to the original.
        staged = svc.stager.staged_path(cart_id, it.source_id, it.photo_id) if cart_id else None
        if staged is None:
            try:
                staged = svc.registry.resolve_photo(it.source_id, it.photo_id)
            except SourceError as exc:
                raise _http(exc)
        payload.append({
            "source_filepath": str(staged),
            "print_size_name": it.size,
            "paper_finish": it.finish.upper(),
            "crop_mode": it.crop,
            "quantity": it.qty,
        })
        total_price += catalog.price(it.size, it.finish) * it.qty
    total_prints = sum(i["quantity"] for i in payload)
    settings = svc.settings()

    def work(progress) -> Dict[str, Any]:
        with sessionmaker(bind=get_db_engine())() as db:
            order_id = svc.processor.process_order(
                db, payload,
                default_dpi=float(settings.get("default_dpi", 300)),
                border_width_mm=float(settings.get("border_width_mm", 4.0)),
                auto_orientation=settings.get("auto_orientation_matching", "true").lower() == "true",
                progress=progress,
            )
        if cart_id:
            svc.stager.discard(cart_id)
        return {"order_id": order_id, "total_prints": total_prints, "total_price": round(total_price, 2)}

    return {"job_id": svc.jobs.submit(work, body.request_id)}


@router.get("/orders/jobs/{job_id}")
def order_job(request: Request, job_id: str):
    job = services(request).jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return {k: v for k, v in job.items() if k != "created"}


# ---------------------------------------------------------------------------
# Phone (iPhone / Android) scanning
# ---------------------------------------------------------------------------
@router.post("/phone/scan")
def phone_scan():
    """Starts (or restarts) scanning the connected phone; no-op if a scan is already running."""
    try:
        return slim_scanner_state(authoritative_scanner.request_rescan())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError:
        return slim_scanner_state(authoritative_scanner.get_state())


@router.get("/phone/date-ranges")
def phone_date_ranges():
    state = authoritative_scanner.get_state()
    photos = state.get("discovered_photos", [])
    bounds = get_date_range_bounds()
    return {"ranges": [
        {"key": k, "range": bounds[k]["range_str"], "count": len(filter_photos_by_date_range(photos, k))}
        for k in DATE_RANGES
    ]}


class PhoneOpenRequest(BaseModel):
    range_key: Literal["1_month", "3_months", "6_months", "all"]


@router.post("/phone/open")
def phone_open(request: Request, body: PhoneOpenRequest):
    """Stages the scanned phone photos of a date range and exposes them as a source."""
    svc = services(request)
    state = authoritative_scanner.get_state()
    if state.get("connection_status") != "connected":
        raise HTTPException(status_code=409, detail="No phone is currently connected.")
    session_id = int(state.get("session_id", 0))
    stage_session_thumbnails(
        state.get("discovered_photos", []), session_id=session_id,
        range_key=body.range_key, base_dir=svc.temp_base,
    )
    device = state.get("device") or "Mobile Phone"
    src = svc.registry.register_phone_session(session_id, device)
    if src is None:
        raise HTTPException(status_code=500, detail="could not prepare phone photos")
    return {"source": {"id": src.id, "kind": src.kind, "label": src.label}}


# ---------------------------------------------------------------------------
# Live events (Server-Sent Events)
# ---------------------------------------------------------------------------
def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/events")
async def events(request: Request):
    svc = services(request)
    svc.hub.bind_loop(asyncio.get_running_loop())
    queue = svc.hub.subscribe()

    async def stream():
        try:
            yield _sse("hello", {
                "boot_id": svc.boot_id,
                "scanner": slim_scanner_state(authoritative_scanner.get_state()),
            })
            while not await request.is_disconnected():
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _sse(event, data)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            svc.hub.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no",
    })
