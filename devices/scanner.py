"""Authoritative Event-Driven iPhone Photo Scanner and File Classifier.

This module provides the central orchestration for iPhone detection, recursive scanning,
file classification (images, videos, breakdown by extension), session tracking, throttling,
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


# File classification constants matching technical specification
IMAGE_EXTENSIONS: Set[str] = {
    ".jpg", ".jpeg",
    ".png",
    ".heic", ".heif",
    ".tif", ".tiff",
    ".bmp",
    ".gif",
    ".webp",
    ".dng", ".raw",
    ".cr2", ".cr3",
    ".nef", ".arw",
    ".orf", ".rw2",
}

VIDEO_EXTENSIONS: Set[str] = {
    ".mov", ".mp4",
    ".m4v", ".avi",
    ".3gp",
}


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


class IPhonePhotoScanner:
    """Authoritative singleton scanner for iPhone photo storage."""

    def __init__(self, throttle_interval_sec: float = 0.1):
        self._lock = threading.RLock()
        self.throttle_interval_sec = throttle_interval_sec  # ~10 updates/sec

        # Session tracking to ensure old/delayed events don't overwrite new session state
        self._session_id: int = 0
        self._worker_thread: Optional[threading.Thread] = None
        self._cancel_scan: bool = False

        # Authoritative state
        self._state: Dict[str, Any] = {
            "device": "",
            "connection_status": "disconnected",
            "scan_status": ScanStatus.DISCONNECTED.value,
            "current_path": "",
            "images_found": 0,
            "videos_found": 0,
            "files_scanned": 0,
            "folders_scanned": 0,
            "errors": 0,
            "images_by_extension": {},
            "session_id": 0,
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
            except Exception:
                pass

    def get_state(self) -> Dict[str, Any]:
        """Returns deepcopy of the current authoritative state."""
        with self._lock:
            return copy.deepcopy(self._state)

    def on_device_connected(self, device_name: str = "Apple iPhone", auto_scan: bool = True) -> int:
        """
        Called when an iPhone connection is detected.
        Creates a new session, resets counters, and starts a scan.
        """
        with self._lock:
            # Stop any existing scan
            self._cancel_scan = True
            self._session_id += 1
            session_id = self._session_id

            self._state = {
                "device": device_name,
                "connection_status": "connected",
                "scan_status": ScanStatus.CONNECTED.value,
                "current_path": "",
                "images_found": 0,
                "videos_found": 0,
                "files_scanned": 0,
                "folders_scanned": 0,
                "errors": 0,
                "images_by_extension": {},
                "session_id": session_id,
            }

        self._publish_event(ScannerEvent.DEVICE_CONNECTED)

        if auto_scan:
            self.start_scan(session_id=session_id)

        return session_id

    def on_device_disconnected(self):
        """Called when device is disconnected."""
        with self._lock:
            self._cancel_scan = True
            self._session_id += 1
            self._state = {
                "device": "",
                "connection_status": "disconnected",
                "scan_status": ScanStatus.DISCONNECTED.value,
                "current_path": "",
                "images_found": 0,
                "videos_found": 0,
                "files_scanned": 0,
                "folders_scanned": 0,
                "errors": 0,
                "images_by_extension": {},
                "session_id": self._session_id,
            }

        self._publish_event(ScannerEvent.DEVICE_DISCONNECTED)

    def request_rescan(self) -> Dict[str, Any]:
        """
        Explicitly triggers a rescan if an iPhone is connected and not currently scanning.
        """
        with self._lock:
            if self._state["connection_status"] != "connected":
                raise ValueError("No iPhone is currently connected.")

            if self._state["scan_status"] == ScanStatus.SCANNING.value:
                raise RuntimeError("Scan is already in progress.")

            # Reset counters and keep device connected
            self._session_id += 1
            session_id = self._session_id
            self._state["scan_status"] = ScanStatus.CONNECTED.value
            self._state["current_path"] = ""
            self._state["images_found"] = 0
            self._state["videos_found"] = 0
            self._state["files_scanned"] = 0
            self._state["folders_scanned"] = 0
            self._state["errors"] = 0
            self._state["images_by_extension"] = {}
            self._state["session_id"] = session_id

        self.start_scan(session_id=session_id)
        return self.get_state()

    def start_scan(self, session_id: Optional[int] = None, custom_worker: Optional[Callable[..., None]] = None):
        """Starts asynchronous scanning on a dedicated worker thread."""
        with self._lock:
            if session_id is None:
                session_id = self._session_id

            if self._session_id != session_id:
                return

            self._cancel_scan = False
            self._state["scan_status"] = ScanStatus.SCANNING.value

        self._publish_event(ScannerEvent.SCAN_STARTED)

        def _worker_wrapper():
            if custom_worker:
                custom_worker(session_id)
            elif sys.platform == "win32" and is_win32_com_available():
                self._run_windows_com_scan(session_id)
            else:
                self._run_simulated_or_stub_scan(session_id)

        self._worker_thread = threading.Thread(target=_worker_wrapper, daemon=True)
        self._worker_thread.start()

    def _run_windows_com_scan(self, session_id: int):
        """Executes WPD COM scanning on Windows with pythoncom CoInitialize()."""
        import pythoncom
        pythoncom.CoInitialize()
        try:
            with self._lock:
                if self._session_id != session_id or self._cancel_scan:
                    return

            # Discover iPhone & Internal Storage
            iphone = WindowsWPDHandler.find_iphone_device()
            if not iphone:
                with self._lock:
                    if self._session_id == session_id:
                        self._state["scan_status"] = ScanStatus.ERROR.value
                        self._state["errors"] += 1
                self._publish_event(ScannerEvent.SCAN_ERROR)
                return

            storage = WindowsWPDHandler.find_internal_storage(iphone)
            if not storage:
                with self._lock:
                    if self._session_id == session_id:
                        self._state["scan_status"] = ScanStatus.ERROR.value
                        self._state["errors"] += 1
                self._publish_event(ScannerEvent.SCAN_ERROR)
                return

            self._execute_scan_loop(storage, session_id)

        except Exception as e:
            with self._lock:
                if self._session_id == session_id:
                    self._state["scan_status"] = ScanStatus.ERROR.value
                    self._state["errors"] += 1
            self._publish_event(ScannerEvent.SCAN_ERROR)
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _execute_scan_loop(self, storage_folder, session_id: int):
        """Traverses storage generator, throttles progress updates, and classifies files."""
        last_publish_time = 0.0

        generator = WindowsWPDHandler.scan_storage_generator(
            storage_folder,
            is_cancelled=lambda: (self._session_id != session_id or self._cancel_scan)
        )

        for event in generator:
            with self._lock:
                if self._session_id != session_id or self._cancel_scan:
                    return

                event_type = event.get("type")
                if event_type == "folder_enter":
                    self._state["folders_scanned"] += 1
                    self._state["current_path"] = event.get("path", "")
                elif event_type == "file":
                    self._state["files_scanned"] += 1
                    ext = event.get("extension", "")
                    if ext in IMAGE_EXTENSIONS:
                        self._state["images_found"] += 1
                        ext_map = self._state["images_by_extension"]
                        ext_map[ext] = ext_map.get(ext, 0) + 1
                    elif ext in VIDEO_EXTENSIONS:
                        self._state["videos_found"] += 1
                elif event_type == "error":
                    self._state["errors"] += 1

            now = time.time()
            if now - last_publish_time >= self.throttle_interval_sec:
                last_publish_time = now
                self._publish_event(ScannerEvent.SCAN_PROGRESS)

        # Finished scan
        with self._lock:
            if self._session_id != session_id or self._cancel_scan:
                return
            self._state["scan_status"] = ScanStatus.COMPLETE.value

        self._publish_event(ScannerEvent.SCAN_COMPLETED)

    def _run_simulated_or_stub_scan(self, session_id: int):
        """Fallback scan for environments where Windows COM is not present."""
        with self._lock:
            if self._session_id != session_id or self._cancel_scan:
                return
            self._state["current_path"] = "Internal Storage"
            self._state["scan_status"] = ScanStatus.COMPLETE.value

        self._publish_event(ScannerEvent.SCAN_COMPLETED)


# Global authoritative scanner instance
authoritative_scanner = IPhonePhotoScanner()
