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
    optical = StorageManager.get_optical_media_paths()
    assert isinstance(optical, list)


def test_windows_removable_drive_detection_and_optical_separation(monkeypatch):
    from devices import storage
    from unittest.mock import patch

    # Mock sys.platform as win32
    monkeypatch.setattr(storage.sys, "platform", "win32")

    # Mock os.path.exists: only E:\ and D:\ exist
    def mock_exists(path):
        return path in ("E:\\", "D:\\")

    # Mock get_windows_drive_type:
    # E:\ is removable pendrive (DRIVE_REMOVABLE = 2)
    # D:\ is optical drive (DRIVE_CDROM = 5)
    def mock_drive_type(drive_letter):
        if "E:" in drive_letter:
            return storage.DRIVE_REMOVABLE
        elif "D:" in drive_letter:
            return storage.DRIVE_CDROM
        return storage.DRIVE_UNKNOWN

    monkeypatch.setattr(storage.os.path, "exists", mock_exists)
    monkeypatch.setattr(storage.StorageManager, "get_windows_drive_type", staticmethod(mock_drive_type))

    removable_drives = storage.StorageManager.get_mounted_media_paths()
    assert len(removable_drives) == 1
    assert removable_drives[0]["name"] == "USB Drive (E:)"
    assert removable_drives[0]["path"] == "E:\\"
    assert removable_drives[0]["type"] == "removable_storage"

    optical_drives = storage.StorageManager.get_optical_media_paths()
    assert len(optical_drives) == 1
    assert optical_drives[0]["name"] == "CD/DVD Drive (D:)"
    assert optical_drives[0]["path"] == "D:\\"
    assert optical_drives[0]["type"] == "optical_media"
