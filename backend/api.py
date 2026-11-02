"""REST API endpoints for Kiosk GUI and remote integration."""
import os
import io
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session
from PIL import Image

from backend.database import get_db_engine, PrintSize, PaperFinish, Order, OrderItem, SystemSetting
from backend.models import (
    PrintSizeSchema,
    PaperFinishSchema,
    OrderCreateRequest,
    OrderSummaryResponse,
    PreviewRequest,
    SystemSettingsSchema,
)
from backend.orders import OrderProcessor, generate_order_id
from backend.library import scan_directory_for_photos
from backend.thumbnails import ThumbnailCache
from imaging.preview import generate_preview

api_router = APIRouter(prefix="/api/v1", tags=["kiosk-api"])
thumb_cache = ThumbnailCache()
order_processor = OrderProcessor()


def get_db():
    engine = get_db_engine()
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@api_router.get("/health")
def health_check():
    return {"status": "ok", "service": "PhotoKiosk Backend"}


@api_router.get("/print-sizes", response_model=List[PrintSizeSchema])
def get_active_print_sizes(db: Session = Depends(get_db)):
    """Returns active print sizes for kiosk touch display."""
    return db.query(PrintSize).filter_by(is_active=True).order_by(PrintSize.sort_order).all()


@api_router.get("/paper-finishes", response_model=List[PaperFinishSchema])
def get_paper_finishes(db: Session = Depends(get_db)):
    """Returns available paper finishes."""
    return db.query(PaperFinish).filter_by(is_active=True).order_by(PaperFinish.sort_order).all()


@api_router.get("/settings", response_model=SystemSettingsSchema)
def get_system_settings(db: Session = Depends(get_db)):
    settings_map = {s.key: s.value for s in db.query(SystemSetting).all()}
    return SystemSettingsSchema(
        default_dpi=int(settings_map.get("default_dpi", 300)),
        border_width_mm=float(settings_map.get("border_width_mm", 4.0)),
        auto_orientation_matching=settings_map.get("auto_orientation_matching", "true").lower() == "true",
        admin_password_must_change=settings_map.get("admin_password_must_change", "false").lower() == "true",
        storage_base_dir=settings_map.get("storage_base_dir", "storage"),
    )


@api_router.get("/photos")
def list_photos(folder: str = Query(..., description="Directory path to scan for photos")):
    """Scans and returns photos found in specified local directory or mounted device."""
    folder_path = Path(folder)
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"Source path {folder} not found")
    photos = scan_directory_for_photos(str(folder_path))
    return {"directory": str(folder_path.resolve()), "count": len(photos), "photos": photos}


@api_router.get("/thumbnail")
def get_photo_thumbnail(filepath: str = Query(...)):
    """Retrieves or asynchronously creates cached thumbnail for a photo."""
    p = Path(filepath)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        thumb_path = thumb_cache.generate_thumbnail(filepath)
        return FileResponse(str(thumb_path), media_type="image/jpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Thumbnail generation error: {e}")


@api_router.post("/preview")
def generate_order_item_preview(req: PreviewRequest, db: Session = Depends(get_db)):
    """Generates a dynamic preview matching the final processed JPEG output."""
    p = Path(req.source_filepath)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Source photo not found")

    size = db.query(PrintSize).filter_by(name=req.print_size_name).first()
    if not size:
        raise HTTPException(status_code=400, detail=f"Unknown print size: {req.print_size_name}")

    auto_orient = True
    setting = db.query(SystemSetting).filter_by(key="auto_orientation_matching").first()
    if setting and setting.value == "false":
        auto_orient = False

    with Image.open(p) as img:
        preview_data = generate_preview(
            source_image=img,
            crop_mode=req.crop_mode,
            paper_width_mm=size.width_mm,
            paper_height_mm=size.height_mm,
            border_width_mm=req.border_width_mm,
            auto_orientation=auto_orient,
        )

    # Convert preview PIL Image to JPEG bytes
    buffer = io.BytesIO()
    preview_data["preview_image"].save(buffer, format="JPEG", quality=85)
    buffer.seek(0)

    # Return base64 or custom headers
    return Response(
        content=buffer.getvalue(),
        media_type="image/jpeg",
        headers={
            "X-Display-Width-MM": str(preview_data["displayed_physical_mm"][0]),
            "X-Display-Height-MM": str(preview_data["displayed_physical_mm"][1]),
            "X-Fits-Paper": str(preview_data["fits_paper"]),
            "X-Warnings": "; ".join(preview_data["warnings"]),
        }
    )


@api_router.post("/orders")
def submit_order(order_req: OrderCreateRequest, db: Session = Depends(get_db)):
    """Submits customer order and initiates atomic processing pipeline."""
    if not order_req.items:
        raise HTTPException(status_code=400, detail="Order must contain at least one item")

    settings_map = {s.key: s.value for s in db.query(SystemSetting).all()}
    default_dpi = float(settings_map.get("default_dpi", 300))
    border_mm = float(settings_map.get("border_width_mm", 4.0))
    auto_orient = settings_map.get("auto_orientation_matching", "true").lower() == "true"

    items_payload = [item.dict() for item in order_req.items]

    try:
        order_id = order_processor.process_order(
            db=db,
            items=items_payload,
            default_dpi=default_dpi,
            border_width_mm=border_mm,
            auto_orientation=auto_orient,
        )
        return {"status": "success", "order_id": order_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/orders/{order_id}", response_model=OrderSummaryResponse)
def get_order_details(order_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter_by(order_id=order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
