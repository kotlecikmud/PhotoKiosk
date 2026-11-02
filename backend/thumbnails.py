"""Asynchronous thumbnail generator with disk caching and change detection."""
import os
import hashlib
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageOps


class ThumbnailCache:
    def __init__(self, cache_dir: str = "storage/thumbnails", thumb_size: Tuple[int, int] = (250, 250)):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.thumb_size = thumb_size

    def _get_cache_key(self, filepath: str) -> str:
        """Generates a stable cache key based on path, size, and mtime."""
        p = Path(filepath)
        try:
            stat = p.stat()
            identity_str = f"{p.resolve()}_{stat.st_size}_{stat.st_mtime}"
        except Exception:
            identity_str = str(p.resolve())
        return hashlib.sha256(identity_str.encode("utf-8")).hexdigest()

    def get_thumbnail_path(self, filepath: str) -> Path:
        key = self._get_cache_key(filepath)
        return self.cache_dir / f"{key}.jpg"

    def has_valid_thumbnail(self, filepath: str) -> bool:
        thumb_path = self.get_thumbnail_path(filepath)
        return thumb_path.exists() and thumb_path.stat().st_size > 0

    def generate_thumbnail(self, filepath: str) -> Path:
        """Generates and caches thumbnail on disk; respects EXIF orientation."""
        thumb_path = self.get_thumbnail_path(filepath)
        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path

        p = Path(filepath)
        with Image.open(p) as img:
            # Respect EXIF orientation for thumbnail display
            oriented = ImageOps.exif_transpose(img)
            if oriented is None:
                oriented = img.copy()

            if oriented.mode not in ("RGB", "L"):
                oriented = oriented.convert("RGB")

            # High quality downsampling
            oriented.thumbnail(self.thumb_size, Image.Resampling.BILINEAR)

            # Atomic save to temporary file then rename
            tmp_thumb = thumb_path.with_suffix(".tmp")
            oriented.save(str(tmp_thumb), format="JPEG", quality=85)
            tmp_thumb.rename(thumb_path)

        return thumb_path
