"""Pydantic schemas and serialization models for PhotoKiosk."""
from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class PrintSizeSchema(BaseModel):
    id: Optional[int] = None
    name: str
    width_mm: float
    height_mm: float
    is_active: bool = True
    sort_order: int = 0

    class Config:
        from_attributes = True


class PrintServicePriceSchema(BaseModel):
    id: Optional[int] = None
    service_id: str
    name: str
    size_name: str
    paper_type: str
    sale_price: float
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PaperFinishSchema(BaseModel):
    id: Optional[int] = None
    name: str
    is_active: bool = True
    sort_order: int = 0

    class Config:
        from_attributes = True


class OrderItemInput(BaseModel):
    source_filepath: str
    print_size_name: str
    paper_finish: str
    crop_mode: str  # fill_in, fit_in, border, no_resize
    quantity: int = Field(ge=1, default=1)


class OrderCreateRequest(BaseModel):
    items: List[OrderItemInput]


class OrderItemSummary(BaseModel):
    original_filename: str
    print_size_name: str
    paper_finish: str
    crop_mode: str
    quantity: int
    output_rel_path: Optional[str] = None
    status: str

    class Config:
        from_attributes = True


class OrderSummaryResponse(BaseModel):
    order_id: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    total_items: int
    total_prints: int
    manifest_path: Optional[str] = None
    error_message: Optional[str] = None
    items: List[OrderItemSummary] = []

    class Config:
        from_attributes = True


class SystemSettingsSchema(BaseModel):
    default_dpi: int = 300
    border_width_mm: float = 4.0
    auto_orientation_matching: bool = True
    admin_password_must_change: bool = False
    storage_base_dir: str = "storage"


class LoginRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class PreviewRequest(BaseModel):
    source_filepath: str
    crop_mode: str
    print_size_name: str
    border_width_mm: float = 4.0


class ScannerStatusResponse(BaseModel):
    device: str
    connection_status: str
    scan_status: str
    current_path: str
    images_found: int
    videos_found: int
    files_scanned: int
    folders_scanned: int
    errors: int
    images_by_extension: Dict[str, int] = {}
    session_id: int = 0
    scan_id: int = 0
    start_time: float = 0.0
    elapsed_seconds: float = 0.0
    needs_authorization: bool = False
    discovered_photos: List[Dict[str, Any]] = []
