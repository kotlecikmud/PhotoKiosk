"""Automated tests for device detection, Android MTP guidance, iOS trust, and thumbnail caching."""
import pytest
from pathlib import Path
from PIL import Image

from devices.storage import StorageManager
from devices.android import AndroidHandler
from devices.ios import AppleIOSHandler
from backend.thumbnails import ThumbnailCache


def test_thumbnail_cache_generation_and_reuse(tmp_path):
    cache_dir = tmp_path / "thumbs"
    cache = ThumbnailCache(str(cache_dir), thumb_size=(100, 100))

    img = Image.new("RGB", (800, 600), (200, 100, 50))
    img_path = tmp_path / "sample.jpg"
    img.save(img_path)

    # First generation
    assert not cache.has_valid_thumbnail(str(img_path))
    thumb1 = cache.generate_thumbnail(str(img_path))
    assert thumb1.exists()
    assert cache.has_valid_thumbnail(str(img_path))

    # Verify generated size conforms to thumbnail bounding box
    with Image.open(thumb1) as t_img:
        assert t_img.width <= 100
        assert t_img.height <= 100

    # Second fetch should hit cache without error
    thumb2 = cache.generate_thumbnail(str(img_path))
    assert thumb1 == thumb2


def test_device_handlers_graceful_fallbacks():
    # Android fallback when jmtpfs not connected
    android_status = AndroidHandler.inspect_mtp_device()
    assert isinstance(android_status, dict)
    assert "status" in android_status
    assert "guidance" in android_status

    # iOS fallback
    ios_status = AppleIOSHandler.inspect_ios_device()
    assert isinstance(ios_status, dict)
    assert "status" in ios_status

    # Storage manager media inspection
    media = StorageManager.get_mounted_media_paths()
    assert isinstance(media, list)
