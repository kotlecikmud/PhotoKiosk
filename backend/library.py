"""Photo library management and filesystem watcher using watchdog."""
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.thumbnails import ThumbnailCache
from devices.classifier import classify_media

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}

# Directory names to skip for fast scanning
SKIP_DIRS = {
    ".thumbnails", ".trash", ".cache", "cache", "lost.dir",
    "system volume information", "$recycle.bin", ".git", ".idea"
}


def is_supported_image(filepath: Path) -> bool:
    return filepath.is_file() and filepath.suffix.lower() in SUPPORTED_EXTENSIONS


def scan_directory_for_photos(directory_path: str) -> List[Dict[str, Any]]:
    """
    Recursively scans directory for supported photo files, skipping cache/trash directories
    and attaching source & album categorization.
    """
    base = Path(directory_path)
    if not base.exists():
        return []

    photos = []
    try:
        for root, dirs, files in os.walk(str(base.resolve())):
            # Prune unwanted directories in-place for fast traversal
            dirs[:] = [
                d for d in dirs
                if d.lower() not in SKIP_DIRS and not (d.startswith(".") and d not in {".", ".."})
            ]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    full_path = os.path.join(root, fname)
                    try:
                        stat = os.stat(full_path)
                        rel_path = os.path.relpath(full_path, str(base.resolve()))
                        cat_info = classify_media(fname, rel_path)
                        photos.append({
                            "filename": fname,
                            "filepath": os.path.abspath(full_path),
                            "size_bytes": stat.st_size,
                            "modified_time": stat.st_mtime,
                            "category": cat_info["category"],
                            "category_name": cat_info["category_name"],
                            "album": cat_info["album"],
                        })
                    except Exception:
                        pass
    except Exception:
        pass

    # Default sort by filename
    photos.sort(key=lambda x: x["filename"].lower())
    return photos


class PhotoLibraryWatcher(FileSystemEventHandler):
    """Monitors directory for external changes (e.g. via SMB network shares)."""
    def __init__(self, watch_dir: str, on_change_callback: Optional[Callable[[str, str], None]] = None):
        self.watch_dir = watch_dir
        self.on_change_callback = on_change_callback
        self.observer = Observer()

    def start(self):
        if os.path.exists(self.watch_dir):
            self.observer.schedule(self, path=self.watch_dir, recursive=True)
            self.observer.start()

    def stop(self):
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join()

    def on_created(self, event):
        if not event.is_directory and Path(event.src_path).suffix.lower() in SUPPORTED_EXTENSIONS:
            if self.on_change_callback:
                self.on_change_callback("created", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory and Path(event.src_path).suffix.lower() in SUPPORTED_EXTENSIONS:
            if self.on_change_callback:
                self.on_change_callback("deleted", event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            if self.on_change_callback:
                self.on_change_callback("moved", event.dest_path)
