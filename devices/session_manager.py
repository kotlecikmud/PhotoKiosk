"""Date range filtering utilities and temporary session staging management."""
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple
from PIL import Image, ImageDraw, ImageFont


def get_date_range_bounds(now: datetime = None) -> Dict[str, Dict[str, Any]]:
    """
    Computes date range intervals (YYYY-MM-DD to YYYY-MM-DD):
      - '1_month': last 30 days
      - '3_months': last 90 days
      - '6_months': last 180 days
      - 'all': unbounded
    """
    if now is None:
        now = datetime.now()

    today_str = now.strftime("%Y-%m-%d")

    m1_start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    m3_start = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    m6_start = (now - timedelta(days=180)).strftime("%Y-%m-%d")

    return {
        "1_month": {
            "label": "Last Month",
            "range_str": f"{m1_start} - {today_str}",
            "start_time": (now - timedelta(days=30)).timestamp(),
            "end_time": now.timestamp(),
        },
        "3_months": {
            "label": "Last 3 Months",
            "range_str": f"{m3_start} - {today_str}",
            "start_time": (now - timedelta(days=90)).timestamp(),
            "end_time": now.timestamp(),
        },
        "6_months": {
            "label": "Last 6 Months",
            "range_str": f"{m6_start} - {today_str}",
            "start_time": (now - timedelta(days=180)).timestamp(),
            "end_time": now.timestamp(),
        },
        "all": {
            "label": "All Photos",
            "range_str": "All time",
            "start_time": 0.0,
            "end_time": float("inf"),
        },
    }


def filter_photos_by_date_range(
    photos: List[Dict[str, Any]],
    range_key: str,
    now: datetime = None
) -> List[Dict[str, Any]]:
    """Filters photos according to selected time range."""
    if range_key == "all":
        return list(photos)

    bounds = get_date_range_bounds(now)
    info = bounds.get(range_key)
    if not info:
        return list(photos)

    start_ts = info["start_time"]
    end_ts = info["end_time"]

    filtered = []
    for p in photos:
        mtime = p.get("modified_time", 0.0) or 0.0
        # If mtime is 0, include it so photos without timestamps aren't discarded
        if mtime == 0.0 or (start_ts <= mtime <= end_ts):
            filtered.append(p)
    return filtered


def get_temp_session_dir(session_id: int, base_dir: str = "storage/temp_sessions") -> Path:
    """Returns path to the temporary session staging directory."""
    path = Path(base_dir) / f"session_{session_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def clean_temp_session_dir(session_id: int, base_dir: str = "storage/temp_sessions"):
    """Safely cleans up the session temporary directory."""
    path = Path(base_dir) / f"session_{session_id}"
    if path.exists():
        try:
            shutil.rmtree(path)
        except Exception:
            pass


def clean_all_temp_sessions(base_dir: str = "storage/temp_sessions"):
    """Cleans all lingering temporary session folders."""
    path = Path(base_dir)
    if path.exists():
        try:
            shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


def stage_session_thumbnails(
    photos: List[Dict[str, Any]],
    session_id: int,
    range_key: str = "all",
    base_dir: str = "storage/temp_sessions",
    now: datetime = None
) -> List[Dict[str, Any]]:
    """
    Filters photos by selected date range, and prepares valid image files
    in the temporary session directory so that GalleryScreen can immediately load them.
    """
    filtered = filter_photos_by_date_range(photos, range_key, now=now)
    temp_dir = get_temp_session_dir(session_id, base_dir=base_dir)

    staged_photos = []
    for idx, p in enumerate(filtered):
        fname = p.get("filename") or f"photo_{idx+1}.jpg"
        target_path = temp_dir / fname

        # If source file already exists on local filesystem, copy/link thumbnail
        source_path = p.get("filepath")
        if source_path and os.path.exists(source_path):
            try:
                with Image.open(source_path) as img:
                    img.thumbnail((400, 300))
                    img.convert("RGB").save(str(target_path), "JPEG", quality=85)
            except Exception:
                _create_placeholder_thumbnail(target_path, fname)
        else:
            # Staged placeholder thumbnail for WPD items ready for on-demand fetch
            _create_placeholder_thumbnail(target_path, fname)

        staged_photos.append({
            "filename": fname,
            "filepath": str(target_path.resolve()),
            "size_bytes": target_path.stat().st_size if target_path.exists() else 0,
            "modified_time": p.get("modified_time", 0.0),
        })

    return staged_photos


def _create_placeholder_thumbnail(target_path: Path, filename: str):
    """Generates a high-contrast placeholder thumbnail for the device photo."""
    img = Image.new("RGB", (320, 240), color=(30, 41, 59))  # Dark blue-slate
    draw = ImageDraw.Draw(img)
    # Draw simple frame and text
    draw.rectangle([10, 10, 310, 230], outline=(59, 130, 246), width=2)
    draw.text((20, 110), filename[:26], fill=(241, 245, 249))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(target_path), "JPEG", quality=85)
