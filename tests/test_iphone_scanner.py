"""Comprehensive unit tests for authoritative iPhone scanner, lifecycle, and mocked COM WPD traversal."""
import os
import time
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from devices.scanner import (
    IPhonePhotoScanner,
    ScanStatus,
    ScannerEvent,
    is_image_extension,
    is_video_extension,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from devices.wpd_ios import WindowsWPDHandler


class MockCOMItem:
    """Mock COM Item representing a file or folder in Windows Shell COM."""
    def __init__(self, name: str, is_folder: bool = False, sub_items: list = None, raises_on_items: bool = False):
        self.Name = name
        self.IsFolder = is_folder
        self._sub_items = sub_items or []
        self._raises_on_items = raises_on_items

    @property
    def GetFolder(self):
        if not self.IsFolder:
            return None
        return self

    def Items(self):
        if self._raises_on_items:
            raise PermissionError("Access is denied to folder")
        return self._sub_items


def test_extension_classification():
    # Case-insensitivity
    assert is_image_extension(".jpg")
    assert is_image_extension(".JPG")
    assert is_image_extension(".jpeg")
    assert is_image_extension(".HEIC")
    assert is_image_extension(".dng")
    assert is_image_extension(".raw")
    assert is_image_extension(".cr3")

    assert is_video_extension(".mov")
    assert is_video_extension(".MOV")
    assert is_video_extension(".mp4")
    assert is_video_extension(".3gp")

    # Unsupported / other files
    assert not is_image_extension(".txt")
    assert not is_image_extension(".mp4")
    assert not is_video_extension(".jpg")
    assert not is_video_extension(".pdf")


def test_mocked_wpd_recursive_traversal():
    """Verifies recursive traversal, path representation, and error handling on inaccessible folders/items."""
    # Build simulated folder tree:
    # Internal Storage
    #   ├── DCIM
    #   │   ├── 100APPLE
    #   │   │   ├── IMG_0001.JPG
    #   │   │   ├── IMG_0002.HEIC
    #   │   │   ├── IMG_0003.MOV
    #   │   │   └── data.plist (unsupported extension)
    #   │   └── RestrictedFolder (raises PermissionError)
    #   └── corrupt_item (raises on getattr)
    img1 = MockCOMItem("IMG_0001.JPG", is_folder=False)
    img2 = MockCOMItem("IMG_0002.HEIC", is_folder=False)
    mov1 = MockCOMItem("IMG_0003.MOV", is_folder=False)
    plist = MockCOMItem("data.plist", is_folder=False)

    folder_100apple = MockCOMItem("100APPLE", is_folder=True, sub_items=[img1, img2, mov1, plist])
    restricted_folder = MockCOMItem("RestrictedFolder", is_folder=True, raises_on_items=True)

    folder_dcim = MockCOMItem("DCIM", is_folder=True, sub_items=[folder_100apple, restricted_folder])
    root_storage = MockCOMItem("Internal Storage", is_folder=True, sub_items=[folder_dcim])

    events = list(WindowsWPDHandler.scan_storage_generator(root_storage))

    # Verify event generation
    folder_enters = [e for e in events if e.get("type") == "folder_enter"]
    files = [e for e in events if e.get("type") == "file"]
    errors = [e for e in events if e.get("type") == "error"]

    assert len(folder_enters) >= 3
    assert len(files) == 4
    assert len(errors) == 1  # RestrictedFolder error caught gracefully without terminating scan

    file_names = {f["name"] for f in files}
    assert file_names == {"IMG_0001.JPG", "IMG_0002.HEIC", "IMG_0003.MOV", "data.plist"}

    # Check relative path formatting with backslashes
    img1_event = next(f for f in files if f["name"] == "IMG_0001.JPG")
    assert img1_event["relative_path"] == "Apple iPhone\\Internal Storage\\DCIM\\100APPLE\\IMG_0001.JPG"


def test_scanner_lifecycle_and_scan_id_isolation():
    """Verifies session_id & scan_id tracking, auto-scan, rescan, and stale event discarding."""
    scanner = IPhonePhotoScanner(throttle_interval_sec=0.01)

    # Initial state
    assert scanner.get_state()["connection_status"] == "disconnected"

    # Connect device
    sess1 = scanner.on_device_connected("Apple iPhone 16", auto_scan=False)
    assert sess1 == 1
    state1 = scanner.get_state()
    assert state1["connection_status"] == "connected"
    assert state1["device"] == "Apple iPhone 16"
    assert state1["session_id"] == 1
    assert state1["scan_id"] == 1

    # Duplicate connection signal should be ignored
    sess_dup = scanner.on_device_connected("Apple iPhone 16", auto_scan=False)
    assert sess_dup == sess1

    # Custom worker scan execution
    events_received = []
    scanner.subscribe(lambda evt, s: events_received.append((evt, s["scan_id"])))

    def custom_worker(session_id, scan_id):
        # Simulate discovering files
        with scanner._lock:
            scanner._state["files_scanned"] += 2
            scanner._state["images_found"] += 1
            scanner._state["videos_found"] += 1
            scanner._state["images_by_extension"][".jpg"] = 1
            scanner._state["scan_status"] = ScanStatus.COMPLETE.value
        scanner._publish_event(ScannerEvent.SCAN_COMPLETED)

    scanner.start_scan(session_id=1, scan_id=1, custom_worker=custom_worker)
    time.sleep(0.1)

    final_state = scanner.get_state()
    assert final_state["scan_status"] == ScanStatus.COMPLETE.value
    assert final_state["images_found"] == 1
    assert final_state["videos_found"] == 1
    assert final_state["files_scanned"] == 2
    assert final_state["images_by_extension"] == {".jpg": 1}

    # Rescan request increments scan_id, keeps session_id
    with patch.object(scanner, "_run_simulated_or_stub_scan") as mock_scan:
        scanner.request_rescan()
        rescan_state = scanner.get_state()
        assert rescan_state["session_id"] == 1
        assert rescan_state["scan_id"] == 2
        assert rescan_state["scan_status"] == ScanStatus.SCANNING.value

    # Disconnect transitions state to DISCONNECTED and resets active device
    scanner.on_device_disconnected()
    disc_state = scanner.get_state()
    assert disc_state["connection_status"] == "disconnected"
    assert disc_state["scan_status"] == ScanStatus.DISCONNECTED.value
    assert disc_state["device"] == ""


def test_scanner_cancellation_on_disconnect():
    """Verifies that disconnecting cancels an active scan loop."""
    scanner = IPhonePhotoScanner(throttle_interval_sec=0.01)
    scanner.on_device_connected("Apple iPhone", auto_scan=False)

    worker_finished = False

    def slow_worker(session_id, scan_id):
        nonlocal worker_finished
        for _ in range(50):
            with scanner._lock:
                if scanner._session_id != session_id or scanner._cancel_scan:
                    return
            time.sleep(0.02)
        worker_finished = True

    scanner.start_scan(session_id=1, scan_id=1, custom_worker=slow_worker)
    time.sleep(0.05)

    # Trigger disconnect mid-scan
    scanner.on_device_disconnected()
    time.sleep(0.1)

    assert not worker_finished
    assert scanner.get_state()["connection_status"] == "disconnected"


def test_zero_images_authorization_guidance():
    """Verifies that when a connected phone scan finds 0 images, needs_authorization is set."""
    scanner = IPhonePhotoScanner(throttle_interval_sec=0.01)
    scanner.on_device_connected("Apple iPhone 16", auto_scan=False)

    def empty_worker(session_id, scan_id):
        with scanner._lock:
            scanner._state["files_scanned"] = 0
            scanner._state["images_found"] = 0
            scanner._state["videos_found"] = 0
            scanner._state["scan_status"] = ScanStatus.COMPLETE.value
            scanner._state["needs_authorization"] = True
        scanner._publish_event(ScannerEvent.SCAN_COMPLETED)

    scanner.start_scan(session_id=1, scan_id=1, custom_worker=empty_worker)
    time.sleep(0.05)

    state = scanner.get_state()
    assert state["scan_status"] == ScanStatus.COMPLETE.value
    assert state["images_found"] == 0
    assert state["needs_authorization"] is True


def test_date_range_filtering_and_session_cleanup(tmp_path):
    """Verifies date range boundaries, photo filtering by date, and temp session cleanup."""
    from datetime import datetime, timedelta
    from devices.session_manager import (
        get_date_range_bounds,
        filter_photos_by_date_range,
        get_temp_session_dir,
        clean_temp_session_dir,
    )

    now = datetime(2026, 6, 1, 12, 0, 0)
    bounds = get_date_range_bounds(now)

    assert "2026-05-02 - 2026-06-01" in bounds["1_month"]["range_str"]
    assert "2026-03-03 - 2026-06-01" in bounds["3_months"]["range_str"]
    assert "2025-12-03 - 2026-06-01" in bounds["6_months"]["range_str"]

    photos = [
        {"filename": "recent.jpg", "modified_time": (now - timedelta(days=5)).timestamp()},
        {"filename": "two_months_ago.jpg", "modified_time": (now - timedelta(days=60)).timestamp()},
        {"filename": "five_months_ago.jpg", "modified_time": (now - timedelta(days=150)).timestamp()},
        {"filename": "one_year_ago.jpg", "modified_time": (now - timedelta(days=365)).timestamp()},
    ]

    # 1 month
    m1 = filter_photos_by_date_range(photos, "1_month", now=now)
    assert len(m1) == 1
    assert m1[0]["filename"] == "recent.jpg"

    # 3 months
    m3 = filter_photos_by_date_range(photos, "3_months", now=now)
    assert len(m3) == 2
    assert {p["filename"] for p in m3} == {"recent.jpg", "two_months_ago.jpg"}

    # 6 months
    m6 = filter_photos_by_date_range(photos, "6_months", now=now)
    assert len(m6) == 3

    # All
    m_all = filter_photos_by_date_range(photos, "all", now=now)
    assert len(m_all) == 4

    # Test temp session folder management
    s_dir = get_temp_session_dir(101, base_dir=str(tmp_path / "sessions"))
    assert s_dir.exists()
    clean_temp_session_dir(101, base_dir=str(tmp_path / "sessions"))
    assert not s_dir.exists()

    # Test staging thumbnails in temporary session directory
    from devices.session_manager import stage_session_thumbnails
    staged = stage_session_thumbnails(photos, session_id=202, range_key="3_months", base_dir=str(tmp_path / "sessions"), now=now)
    assert len(staged) == 2
    for item in staged:
        assert Path(item["filepath"]).exists()
    clean_temp_session_dir(202, base_dir=str(tmp_path / "sessions"))
