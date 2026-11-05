"""Authoritative Event-Driven Smartphone (iPhone / Android) Photo Scanner and File Classifier.

This module provides the central orchestration for smartphone detection, recursive scanning,
file classification (images, videos, breakdown by extension and app source/album: Camera, WhatsApp,
Messenger, Screenshots, Instagram, Telegram, Downloads, etc.), session tracking, throttling,
and multi-subscriber event publishing.
"""
import copy
import enum
import os
import sys
import threading
import time
from typing import Dict, Any, List, Set, Optional, Callable

from devices.wpd_ios import WindowsWPDHandler, is_win32_com_available
from devices.classifier import (
    classify_media,
    CATEGORY_CAMERA,
    CATEGORY_WHATSAPP,
    CATEGORY_MESSENGER,
    CATEGORY_SCREENSHOTS,
    CATEGORY_INSTAGRAM,
    CATEGORY_TELEGRAM,
    CATEGORY_DOWNLOADS,
    CATEGORY_SNAPCHAT,
    CATEGORY_TWITTER,
    CATEGORY_OTHER,
)


# Configurable file classification constants matching wp_iphone_scanner_prototype.py
IMAGE_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".tif", ".tiff", ".bmp", ".gif", ".webp",
    ".dng", ".raw", ".cr2", ".cr3", ".nef",
    ".arw", ".orf", ".rw2"
}

VIDEO_EXTENSIONS: Set[str] = {
    ".mov", ".mp4", ".m4v", ".avi", ".3gp"
}


def is_image_extension(ext: str, custom_extensions: Optional[Set[str]] = None) -> bool:
    """Checks if extension is a supported image format (case-insensitive)."""
    extensions = custom_extensions if custom_extensions is not None else IMAGE_EXTENSIONS
    return ext.strip().lower() in extensions


def is_video_extension(ext: str, custom_extensions: Optional[Set[str]] = None) -> bool:
    """Checks if extension is a supported video format (case-insensitive)."""
    extensions = custom_extensions if custom_extensions is not None else VIDEO_EXTENSIONS
    return ext.strip().lower() in extensions


class ScanStatus(str, enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    SCANNING = "SCANNING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


class ScannerEvent(str, enum.Enum):
    DEVICE_CONNECTED = "device_connected"
    SCAN_STARTED = "scan_started"
    SCAN_PROGRESS = "scan_progress"
    SCAN_COMPLETED = "scan_completed"
    DEVICE_DISCONNECTED = "device_disconnected"
    SCAN_ERROR = "scan_error"


import logging

logger = logging.getLogger("photokiosk.scanner")


class IPhonePhotoScanner:
    """Authoritative singleton scanner for iPhone & Android photo storage."""

    def __init__(self, throttle_interval_sec: float = 0.15):
        self._lock = threading.RLock()
        self.throttle_interval_sec = throttle_interval_sec  # 5-10 updates/sec

        # Session & scan tracking to ensure old/delayed events don't overwrite new session state
        self._session_id: int = 0
        self._scan_id: int = 0
        self._worker_thread: Optional[threading.Thread] = None
        self._cancel_scan: bool = False

        # Authoritative state
        self._state: Dict[str, Any] = {
            "device": "",
            "device_type": "mobile",
            "connection_status": "disconnected",
            "scan_status": ScanStatus.DISCONNECTED.value,
            "current_path": "",
            "images_found": 0,
            "videos_found": 0,
            "files_scanned": 0,
            "folders_scanned": 0,
            "errors": 0,
            "images_by_extension": {},
            "images_by_category": {},
            "albums": {},
            "session_id": 0,
            "scan_id": 0,
            "start_time": 0.0,
            "elapsed_seconds": 0.0,
            "needs_authorization": False,
            "discovered_photos": [],
        }

        # Event subscribers: callbacks receiving (event_name: str, state: dict)
        self._subscribers: List[Callable[[str, Dict[str, Any]], None]] = []

    def subscribe(self, callback: Callable[[str, Dict[str, Any]], None]) -> Callable[[], None]:
        """Registers an event callback. Returns an unsubscribe function."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def _publish_event(self, event: ScannerEvent):
        """Notifies all registered subscribers of the current state snapshot."""
        with self._lock:
            state_snapshot = copy.deepcopy(self._state)
            subscribers = list(self._subscribers)

        for sub in subscribers:
            try:
                sub(event.value, state_snapshot)
            except Exception as e:
                logger.debug(f"Subscriber error: {e}")

    def get_state(self) -> Dict[str, Any]:
        """Returns deepcopy of the current authoritative state."""
        with self._lock:
            return copy.deepcopy(self._state)

    def on_device_connected(
        self,
        device_name: str = "Apple iPhone",
        auto_scan: bool = True,
        device_type: Optional[str] = None
    ) -> int:
        """
        Called when a smartphone connection is detected.
        Creates a new session and new scan_id, resets counters, and starts a scan.
        """
        with self._lock:
            # If already connected and scanning same device, ignore duplicate connection trigger
            if self._state["connection_status"] == "connected" and self._state["device"] == device_name:
                return self._session_id

            # Determine device type
            if not device_type:
                device_type = "iphone" if "iphone" in device_name.lower() or "ipad" in device_name.lower() else "android"

            # Stop any existing scan
            self._cancel_scan = True
            self._session_id += 1
            self._scan_id += 1
            session_id = self._session_id
            scan_id = self._scan_id

            self._state = {
                "device": device_name,
                "device_type": device_type,
                "connection_status": "connected",
                "scan_status": ScanStatus.CONNECTED.value,
                "current_path": "Waiting to scan...",
                "images_found": 0,
                "videos_found": 0,
                "files_scanned": 0,
                "folders_scanned": 0,
                "errors": 0,
                "images_by_extension": {},
                "images_by_category": {},
                "albums": {},
                "session_id": session_id,
                "scan_id": scan_id,
                "start_time": 0.0,
                "elapsed_seconds": 0.0,
                "needs_authorization": False,
                "discovered_photos": [],
            }

        self._publish_event(ScannerEvent.DEVICE_CONNECTED)

        if auto_scan:
            self.start_scan(session_id=session_id, scan_id=scan_id)

        return session_id

    def on_device_disconnected(self):
        """Called when device is disconnected."""
        with self._lock:
            self._cancel_scan = True
            self._session_id += 1
            self._scan_id += 1
            self._state = {
                "device": "",
                "device_type": "mobile",
                "connection_status": "disconnected",
                "scan_status": ScanStatus.DISCONNECTED.value,
                "current_path": "",
                "images_found": 0,
                "videos_found": 0,
                "files_scanned": 0,
                "folders_scanned": 0,
                "errors": 0,
                "images_by_extension": {},
                "images_by_category": {},
                "albums": {},
                "session_id": self._session_id,
                "scan_id": self._scan_id,
                "start_time": 0.0,
                "elapsed_seconds": 0.0,
                "needs_authorization": False,
                "discovered_photos": [],
            }

        self._publish_event(ScannerEvent.DEVICE_DISCONNECTED)

    def request_rescan(self) -> Dict[str, Any]:
        """
        Explicitly triggers a rescan if a device is connected and not currently scanning.
        Generates a new scan_id while preserving session_id.
        """
        with self._lock:
            if self._state["connection_status"] != "connected":
                raise ValueError("No phone is currently connected.")

            if self._state["scan_status"] == ScanStatus.SCANNING.value:
                raise RuntimeError("Scan is already in progress.")

            # New scan attempt under existing session
            self._scan_id += 1
            scan_id = self._scan_id
            session_id = self._session_id
            dev_name = self._state.get("device", "Mobile Device")
            dev_type = self._state.get("device_type", "mobile")

            self._state["scan_status"] = ScanStatus.CONNECTED.value
            self._state["current_path"] = f"{dev_name}\\Internal Storage"
            self._state["images_found"] = 0
            self._state["videos_found"] = 0
            self._state["files_scanned"] = 0
            self._state["folders_scanned"] = 0
            self._state["errors"] = 0
            self._state["images_by_extension"] = {}
            self._state["images_by_category"] = {}
            self._state["albums"] = {}
            self._state["scan_id"] = scan_id
            self._state["start_time"] = 0.0
            self._state["elapsed_seconds"] = 0.0
            self._state["needs_authorization"] = False
            self._state["discovered_photos"] = []

        self.start_scan(session_id=session_id, scan_id=scan_id)
        return self.get_state()

    def start_scan(
        self,
        session_id: Optional[int] = None,
        scan_id: Optional[int] = None,
        custom_worker: Optional[Callable[..., None]] = None
    ):
        """Starts asynchronous scanning on a dedicated worker thread."""
        with self._lock:
            if session_id is None:
                session_id = self._session_id
            if scan_id is None:
                scan_id = self._scan_id

            if self._session_id != session_id or self._scan_id != scan_id:
                return

            dev_name = self._state.get("device", "Mobile Device")
            self._cancel_scan = False
            self._state["scan_status"] = ScanStatus.SCANNING.value
            self._state["current_path"] = f"{dev_name}\\Internal Storage"
            self._state["start_time"] = time.time()
            self._state["elapsed_seconds"] = 0.0
            self._state["needs_authorization"] = False
            self._state["discovered_photos"] = []

        self._publish_event(ScannerEvent.SCAN_STARTED)

        def _worker_wrapper():
            if custom_worker:
                custom_worker(session_id, scan_id)
            elif sys.platform == "win32" and is_win32_com_available():
                self._run_windows_com_scan(session_id, scan_id)
            else:
                self._run_simulated_or_stub_scan(session_id, scan_id)

        self._worker_thread = threading.Thread(target=_worker_wrapper, daemon=True)
        self._worker_thread.start()

    def _run_windows_com_scan(self, session_id: int, scan_id: int):
        """Executes WPD COM scanning entirely within this dedicated initialized COM worker thread."""
        import pythoncom
        pythoncom.CoInitialize()
        try:
            with self._lock:
                if self._session_id != session_id or self._scan_id != scan_id or self._cancel_scan:
                    return
                current_device_name = self._state.get("device", "")

            # Discover phone device (iPhone or Android)
            phone = None
            if "iphone" in current_device_name.lower() or "ipad" in current_device_name.lower():
                phone = WindowsWPDHandler.find_iphone_device()
            else:
                phone = WindowsWPDHandler.find_mobile_device()
                if not phone:
                    phone = WindowsWPDHandler.find_iphone_device()

            if not phone:
                logger.error("Phone device not found in Shell COM tree.")
                with self._lock:
                    if self._session_id == session_id and self._scan_id == scan_id:
                        self._state["scan_status"] = ScanStatus.ERROR.value
                        self._state["errors"] += 1
                        self._state["current_path"] = "Error: Device not found"
                self._publish_event(ScannerEvent.SCAN_ERROR)
                return

            # Discover all storage folders (Internal Storage, SD Card, etc.)
            storages = WindowsWPDHandler.find_device_storages(phone)
            if not storages:
                # Fallback to find_internal_storage
                single_storage = WindowsWPDHandler.find_internal_storage(phone)
                if single_storage:
                    storages = [single_storage]

            if not storages:
                logger.error("No accessible storage folders found on device.")
                with self._lock:
                    if self._session_id == session_id and self._scan_id == scan_id:
                        self._state["scan_status"] = ScanStatus.ERROR.value
                        self._state["errors"] += 1
                        self._state["current_path"] = "Error: Storage inaccessible"
                self._publish_event(ScannerEvent.SCAN_ERROR)
                return

            # Scan each storage folder found
            for storage_folder in storages:
                with self._lock:
                    if self._session_id != session_id or self._scan_id != scan_id or self._cancel_scan:
                        return
                self._execute_scan_loop(storage_folder, session_id, scan_id)

            # Finished scan successfully across all storages
            with self._lock:
                if self._session_id != session_id or self._scan_id != scan_id or self._cancel_scan:
                    return
                if self._state["start_time"] > 0:
                    self._state["elapsed_seconds"] = round(time.time() - self._state["start_time"], 1)
                self._state["current_path"] = "Scan complete"
                self._state["scan_status"] = ScanStatus.COMPLETE.value
                if self._state["images_found"] == 0:
                    self._state["needs_authorization"] = True

            self._publish_event(ScannerEvent.SCAN_COMPLETED)

        except Exception as e:
            logger.exception(f"Unexpected error during phone COM scan: {e}")
            with self._lock:
                if self._session_id == session_id and self._scan_id == scan_id:
                    self._state["scan_status"] = ScanStatus.ERROR.value
                    self._state["errors"] += 1
                    self._state["current_path"] = f"Error: {e}"
            self._publish_event(ScannerEvent.SCAN_ERROR)
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _execute_scan_loop(self, storage_folder, session_id: int, scan_id: int):
        """Traverses storage generator, throttles progress updates, and classifies files."""
        last_publish_time = time.time()

        generator = WindowsWPDHandler.scan_storage_generator(
            storage_folder,
            is_cancelled=lambda: (
                self._session_id != session_id or
                self._scan_id != scan_id or
                self._cancel_scan
            ),
            on_error=lambda p, err: logger.warning(f"Error accessing {p}: {err}")
        )

        for event in generator:
            with self._lock:
                # Discard event if session or scan has changed or been cancelled
                if self._session_id != session_id or self._scan_id != scan_id or self._cancel_scan:
                    return

                event_type = event.get("type")
                if event_type == "folder_enter":
                    self._state["folders_scanned"] += 1
                    self._state["current_path"] = event.get("path", "")
                elif event_type == "file":
                    self._state["files_scanned"] += 1
                    ext = (event.get("extension") or "").lower()
                    if is_image_extension(ext):
                        self._state["images_found"] += 1
                        ext_map = self._state["images_by_extension"]
                        ext_map[ext] = ext_map.get(ext, 0) + 1

                        # Source / Album classification
                        cat = event.get("category")
                        cat_name = event.get("category_name")
                        album = event.get("album")
                        if not cat or not cat_name or not album:
                            cat_info = classify_media(event.get("name", ""), event.get("relative_path", ""))
                            cat = cat_info["category"]
                            cat_name = cat_info["category_name"]
                            album = cat_info["album"]

                        cat_map = self._state["images_by_category"]
                        cat_map[cat] = cat_map.get(cat, 0) + 1

                        album_map = self._state["albums"]
                        album_map[album] = album_map.get(album, 0) + 1

                        self._state["discovered_photos"].append({
                            "filename": event.get("name", ""),
                            "relative_path": event.get("relative_path", ""),
                            "extension": ext,
                            "modified_time": event.get("modified_time", 0.0),
                            "size_bytes": event.get("size_bytes", 0),
                            "category": cat,
                            "category_name": cat_name,
                            "album": album,
                        })
                    elif is_video_extension(ext):
                        self._state["videos_found"] += 1
                elif event_type == "error":
                    self._state["errors"] += 1

                if self._state["start_time"] > 0:
                    self._state["elapsed_seconds"] = round(time.time() - self._state["start_time"], 1)

            now = time.time()
            if now - last_publish_time >= self.throttle_interval_sec:
                last_publish_time = now
                self._publish_event(ScannerEvent.SCAN_PROGRESS)

    def _run_simulated_or_stub_scan(self, session_id: int, scan_id: int):
        """Fallback scan for environments where Windows COM is not present."""
        with self._lock:
            if self._session_id != session_id or self._scan_id != scan_id or self._cancel_scan:
                return
            if self._state["start_time"] > 0:
                self._state["elapsed_seconds"] = round(time.time() - self._state["start_time"], 1)
            self._state["current_path"] = "Scan complete"
            self._state["scan_status"] = ScanStatus.COMPLETE.value
            if self._state["images_found"] == 0:
                self._state["needs_authorization"] = True

        self._publish_event(ScannerEvent.SCAN_COMPLETED)


# Global authoritative scanner instance
authoritative_scanner = IPhonePhotoScanner()
