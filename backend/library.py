"""Photo library management and filesystem watcher using watchdog."""
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Callable, Optional, Set
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.thumbnails import ThumbnailCache

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}


def is_supported_image(filepath: Path) -> bool:
    return filepath.is_file() and filepath.suffix.lower() in SUPPORTED_EXTENSIONS


def scan_directory_for_photos(directory_path: str) -> List[Dict[str, Any]]:
    """Recursively scans directory for supported photo files."""
    base = Path(directory_path)
    if not base.exists():
        return []

    photos = []
    for item in base.rglob("*"):
        if is_supported_image(item):
            try:
                stat = item.stat()
                photos.append({
                    "filename": item.name,
                    "filepath": str(item.resolve()),
                    "size_bytes": stat.st_size,
                    "modified_time": stat.st_mtime,
                })
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
