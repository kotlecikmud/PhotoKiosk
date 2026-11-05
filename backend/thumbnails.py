"""Thumbnail generator with disk caching, change detection and fast size probing.

Thumbnails are scaled to fit *inside* ``thumb_size`` and therefore keep the
photo's original aspect ratio (they are never cropped to a square).
"""
import hashlib
import os
import threading
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageOps

# EXIF orientations that swap width and height (rotated 90 / 270 degrees)
_ROTATED_ORIENTATIONS = {5, 6, 7, 8}
_EXIF_ORIENTATION_TAG = 0x0112


def probe_oriented_size(filepath: str) -> Optional[Tuple[int, int]]:
    """Return (width, height) as displayed (EXIF orientation applied).

    Only the image header is read, so this is cheap even for large files.
    Returns None if the file cannot be read.
    """
    try:
        with Image.open(filepath) as img:
            w, h = img.size
            try:
                orientation = img.getexif().get(_EXIF_ORIENTATION_TAG, 1)
            except Exception:
                orientation = 1
            if orientation in _ROTATED_ORIENTATIONS:
                w, h = h, w
            return (w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


class ThumbnailCache:
    def __init__(self, cache_dir: str = "storage/thumbnails", thumb_size: Tuple[int, int] = (320, 320)):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.thumb_size = thumb_size

    def _get_cache_key(self, filepath: str) -> str:
        """Stable cache key based on path, size, mtime and thumbnail bounds."""
        p = Path(filepath)
        try:
            stat = p.stat()
            identity = f"{p.resolve()}_{stat.st_size}_{stat.st_mtime}"
        except Exception:
            identity = str(p.resolve())
        identity += f"_{self.thumb_size[0]}x{self.thumb_size[1]}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def get_thumbnail_path(self, filepath: str) -> Path:
        return self.cache_dir / f"{self._get_cache_key(filepath)}.jpg"

    def has_valid_thumbnail(self, filepath: str) -> bool:
        thumb_path = self.get_thumbnail_path(filepath)
        return thumb_path.exists() and thumb_path.stat().st_size > 0

    def generate_thumbnail(self, filepath: str) -> Path:
        """Generate (or reuse) the cached thumbnail; respects EXIF orientation."""
        thumb_path = self.get_thumbnail_path(filepath)
        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path

        with Image.open(filepath) as img:
            # JPEG draft mode lets the decoder downscale while reading: large speed-up
            try:
                img.draft("RGB", (self.thumb_size[0] * 2, self.thumb_size[1] * 2))
            except Exception:
                pass
            oriented = ImageOps.exif_transpose(img)
            if oriented is None:
                oriented = img.copy()
            if oriented.mode not in ("RGB", "L"):
                oriented = oriented.convert("RGB")

            # thumbnail() fits inside the box and preserves aspect ratio
            oriented.thumbnail(self.thumb_size, Image.Resampling.LANCZOS, reducing_gap=2.0)

            # Atomic write: unique temp name so concurrent workers never collide
            tmp = thumb_path.with_name(f"{thumb_path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                oriented.save(str(tmp), format="JPEG", quality=85)
                os.replace(tmp, thumb_path)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        return thumb_path

    def get_display_size(self, filepath: str) -> Optional[Tuple[int, int]]:
        """Oriented (w, h) of the photo, preferring the tiny cached thumbnail."""
        if self.has_valid_thumbnail(filepath):
            size = probe_oriented_size(str(self.get_thumbnail_path(filepath)))
            if size:
                return size
        return probe_oriented_size(filepath)
