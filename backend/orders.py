"""Order processing engine, manifest creation, and recovery mechanism.

Guarantees:
- ID format: ORD-YYYYMMDD-HHMMSS-XXXX.
- Byte-for-byte copy of originals into ORIGINALS/.
- Clean collision handling if multiple sources share the same filename.
- Organized outputs: [size]/[finish]/x[qty]/[filename].
- Comprehensive order.json manifest.
- Non-destructive failure recovery: temporary directories preserved with diagnostic details.
"""
import os
import shutil
import json
import random
import string
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from backend.database import Order, OrderItem, PrintSize, SystemSetting
from imaging.processor import process_image
from imaging.dpi import DEFAULT_DPI


def generate_order_id(now: Optional[datetime] = None) -> str:
    """Generates ORD-YYYYMMDD-HHMMSS-XXXX collision-safe identifier."""
    if now is None:
        now = datetime.utcnow()
    timestamp_str = now.strftime("%Y%m%d-%H%M%S")
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(random.choices(chars, k=4))
    return f"ORD-{timestamp_str}-{suffix}"


def resolve_collision_safe_filename(dest_dir: Path, filename: str) -> str:
    """Ensures a unique filename in dest_dir without overwriting existing files."""
    dest_path = dest_dir / filename
    if not dest_path.exists():
        return filename

    p = Path(filename)
    stem = p.stem
    suffix = p.suffix
    counter = 1
    while True:
        candidate = f"{stem}_{counter}{suffix}"
        if not (dest_dir / candidate).exists():
            return candidate
        counter += 1


class OrderProcessor:
    def __init__(self, orders_base_dir: str = "storage/orders"):
        self.orders_base_dir = Path(orders_base_dir)
        self.orders_base_dir.mkdir(parents=True, exist_ok=True)

    def process_order(
        self,
        db: Session,
        items: List[Dict[str, Any]],
        default_dpi: float = DEFAULT_DPI,
        border_width_mm: float = 4.0,
        auto_orientation: bool = True
    ) -> str:
        """
        Creates, processes, and finalizes an order atomically.
        items: list of dicts with keys:
          - source_filepath: str
          - print_size_name: str
          - paper_finish: str
          - crop_mode: str
          - quantity: int
        """
        order_id = generate_order_id()
        created_at = datetime.utcnow()

        # Load print sizes map
        sizes = {s.name: (s.width_mm, s.height_mm) for s in db.query(PrintSize).all()}

        total_prints = sum(item.get("quantity", 1) for item in items)

        # 1. Staging directory setup
        staging_dir = self.orders_base_dir / f".tmp_{order_id}"
        staging_dir.mkdir(parents=True, exist_ok=True)
        originals_dir = staging_dir / "ORIGINALS"
        originals_dir.mkdir(parents=True, exist_ok=True)

        final_order_dir = self.orders_base_dir / order_id

        # 2. Record order in DB with PENDING status
        db_order = Order(
            order_id=order_id,
            status="PENDING",
            created_at=created_at,
            total_items=len(items),
            total_prints=total_prints,
        )
        db.add(db_order)
        db.commit()

        manifest: Dict[str, Any] = {
            "order_id": order_id,
            "created_at": created_at.isoformat() + "Z",
            "total_items": len(items),
            "total_prints": total_prints,
            "status": "PROCESSING",
            "items": [],
            "processing_warnings": [],
        }

        try:
            db_order.status = "PROCESSING"
            db.commit()

            # Track original file copies to avoid re-copying duplicate files across items
            copied_originals_map: Dict[str, str] = {}  # source_filepath -> staged_filename

            for item_idx, item_req in enumerate(items):
                src_path = Path(item_req["source_filepath"])
                if not src_path.exists():
                    raise FileNotFoundError(f"Source photo not found: {src_path}")

                size_name = item_req["print_size_name"]
                if size_name not in sizes:
                    raise ValueError(f"Unknown print size: {size_name}")
                paper_w_mm, paper_h_mm = sizes[size_name]

                finish = str(item_req["paper_finish"]).upper()
                crop_mode = item_req["crop_mode"]
                qty = int(item_req.get("quantity", 1))

                # Copy original byte-for-byte to ORIGINALS if not copied yet
                canonical_src = str(src_path.resolve())
                if canonical_src in copied_originals_map:
                    original_staged_name = copied_originals_map[canonical_src]
                else:
                    original_staged_name = resolve_collision_safe_filename(originals_dir, src_path.name)
                    dest_orig_path = originals_dir / original_staged_name
                    shutil.copy2(src_path, dest_orig_path)
                    copied_originals_map[canonical_src] = original_staged_name

                # Target directory structure: [size]/[finish]/x[qty]/[filename]
                target_variant_dir = staging_dir / size_name / finish / f"x{qty}"
                target_variant_dir.mkdir(parents=True, exist_ok=True)

                output_filename = resolve_collision_safe_filename(target_variant_dir, original_staged_name)
                # Ensure .jpg extension for output print
                if not output_filename.lower().endswith(('.jpg', '.jpeg')):
                    output_filename = f"{Path(output_filename).stem}.jpg"
                output_file_path = target_variant_dir / output_filename

                # Process image
                result = process_image(
                    source_path=str(src_path),
                    output_path=str(output_file_path),
                    crop_mode=crop_mode,
                    paper_width_mm=paper_w_mm,
                    paper_height_mm=paper_h_mm,
                    border_width_mm=border_width_mm,
                    dpi=default_dpi,
                    auto_orientation=auto_orientation,
                )

                # Relative paths for manifest and DB
                rel_orig = f"ORIGINALS/{original_staged_name}"
                rel_out = f"{size_name}/{finish}/x{qty}/{output_filename}"

                # Append to manifest
                manifest["items"].append({
                    "item_index": item_idx + 1,
                    "original_filename": src_path.name,
                    "original_path_source": str(src_path),
                    "original_archived_rel_path": rel_orig,
                    "print_size": size_name,
                    "paper_finish": finish,
                    "crop_mode": crop_mode,
                    "quantity": qty,
                    "output_rel_path": rel_out,
                    "original_pixels": result["original_pixels"],
                    "original_dpi": result["original_dpi"],
                    "exif_orientation": result["exif_orientation"],
                    "icc_profile": result["icc_profile"],
                    "paper_size_applied_mm": result["paper_size_applied_mm"],
                    "output_pixels": result["output_pixels"],
                    "output_dpi": result["output_dpi"],
                    "calculated_physical_mm": result["calculated_physical_mm"],
                    "warnings": result["warnings"],
                })

                if result["warnings"]:
                    manifest["processing_warnings"].extend(
                        [f"Item {item_idx + 1} ({src_path.name}): {w}" for w in result["warnings"]]
                    )

                # Record DB item
                db_item = OrderItem(
                    order_id=order_id,
                    original_filename=src_path.name,
                    original_rel_path=rel_orig,
                    print_size_name=size_name,
                    paper_finish=finish,
                    crop_mode=crop_mode,
                    quantity=qty,
                    output_rel_path=rel_out,
                    status="COMPLETED",
                )
                db.add(db_item)

            # Write manifest
            manifest["status"] = "COMPLETED"
            manifest["completed_at"] = datetime.utcnow().isoformat() + "Z"
            manifest_file = staging_dir / "order.json"
            with open(manifest_file, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2)

            # Atomic directory finalization: rename .tmp_order_id -> order_id
            staging_dir.rename(final_order_dir)

            # Store order data (originals, crops, manifest) in shared directory for remote LAN browsing & download
            try:
                shared_base = Path("storage/shared/orders")
                shared_base.mkdir(parents=True, exist_ok=True)
                dest_shared_order = shared_base / order_id
                if dest_shared_order.exists():
                    shutil.rmtree(dest_shared_order)
                shutil.copytree(final_order_dir, dest_shared_order)
            except Exception:
                pass

            # Update DB order status
            db_order.status = "COMPLETED"
            db_order.completed_at = datetime.utcnow()
            db_order.manifest_path = str(final_order_dir / "order.json")
            db.commit()

            return order_id

        except Exception as e:
            # Non-destructive error handling: preserve staging directory with diagnostic info
            err_msg = str(e)
            manifest["status"] = "FAILED"
            manifest["error"] = err_msg
            manifest["failed_at"] = datetime.utcnow().isoformat() + "Z"

            diag_file = staging_dir / "order_failure_diagnosis.json"
            try:
                with open(diag_file, "w", encoding="utf-8") as df:
                    json.dump(manifest, df, indent=2)
                # Keep staging folder prefixed with .failed_ for troubleshooting
                failed_dir = self.orders_base_dir / f".failed_{order_id}"
                if staging_dir.exists():
                    staging_dir.rename(failed_dir)
            except Exception:
                pass

            db_order.status = "FAILED"
            db_order.error_message = err_msg
            db.commit()
            raise


def recover_incomplete_orders(db: Session, orders_base_dir: str = "storage/orders") -> List[str]:
    """
    Scans orders directory and DB on startup.
    Marks any unfinalized or interrupted orders as FAILED with diagnostic notes,
    preserving files for administrative inspection.
    """
    base_dir = Path(orders_base_dir)
    recovered = []

    # Check database orders in PENDING or PROCESSING state
    stuck_orders = db.query(Order).filter(Order.status.in_(["PENDING", "PROCESSING"])).all()
    for o in stuck_orders:
        o.status = "FAILED"
        o.error_message = "Order interrupted due to system shutdown or power failure."
        recovered.append(o.order_id)

    # Check filesystem for leftover .tmp_* folders
    if base_dir.exists():
        for item in base_dir.iterdir():
            if item.is_dir() and item.name.startswith(".tmp_"):
                failed_name = item.name.replace(".tmp_", ".failed_")
                failed_target = base_dir / failed_name
                try:
                    item.rename(failed_target)
                    recovered.append(item.name)
                except Exception:
                    pass

    db.commit()
    return recovered
