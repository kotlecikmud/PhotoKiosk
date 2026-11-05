"""Hardware device monitoring daemon with debouncing for udev block and usb events.

Supports both Linux (pyudev) and Windows (WPD polling) device monitoring,
automatically notifying the authoritative mobile (iPhone/Android) scanner.
"""
import sys
import threading
import time
from typing import Callable, Optional, Dict, Any, List

try:
    import pyudev
    PYUDEV_AVAILABLE = True
except ImportError:
    PYUDEV_AVAILABLE = False

from devices.storage import StorageManager
from devices.android import AndroidHandler
from devices.ios import AppleIOSHandler
from devices.scanner import authoritative_scanner
from devices.wpd_ios import WindowsWPDHandler, is_win32_com_available


class DeviceMonitorDaemon:
    def __init__(self, callback: Optional[Callable[[str, Dict[str, Any]], None]] = None, debounce_seconds: float = 2.0):
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._last_event_time: Dict[str, float] = {}
        self._iphone_connected: bool = False
        self._connected_device_name: str = ""
        self._connected_device_type: str = ""

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _notify(self, action: str, details: Dict[str, Any]):
        if self.callback:
            try:
                self.callback(action, details)
            except Exception:
                pass

    def _run(self):
        if sys.platform == "win32":
            self._run_windows_monitor()
        else:
            self._run_linux_monitor()

    def _run_windows_monitor(self):
        """Polls Windows WPD device presence periodically in background thread."""
        if not is_win32_com_available():
            while self.running:
                time.sleep(2.0)
            return

        import pythoncom
        pythoncom.CoInitialize()
        try:
            while self.running:
                try:
                    is_conn, dev_name, dev_type = WindowsWPDHandler.check_mobile_connected()
                    if is_conn and not self._iphone_connected:
                        self._iphone_connected = True
                        self._connected_device_name = dev_name or "Mobile Phone"
                        self._connected_device_type = dev_type or "mobile"
                        authoritative_scanner.on_device_connected(
                            device_name=self._connected_device_name,
                            auto_scan=False,
                            device_type=self._connected_device_type
                        )
                        self._notify("connected", {
                            "device": self._connected_device_name,
                            "platform": "win32",
                            "type": self._connected_device_type
                        })
                    elif not is_conn and self._iphone_connected:
                        self._iphone_connected = False
                        name = self._connected_device_name or "Mobile Phone"
                        dtype = self._connected_device_type or "mobile"
                        self._connected_device_name = ""
                        self._connected_device_type = ""
                        authoritative_scanner.on_device_disconnected()
                        self._notify("disconnected", {
                            "device": name,
                            "platform": "win32",
                            "type": dtype
                        })
                except Exception:
                    pass

                time.sleep(1.0)
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _run_linux_monitor(self):
        if not PYUDEV_AVAILABLE:
            # Poll mounted media periodically as fallback
            while self.running:
                time.sleep(3.0)
            return

        try:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="block")
            monitor.filter_by(subsystem="usb")

            for action, device in monitor:
                if not self.running:
                    break

                dev_path = device.device_node or device.device_path
                now = time.time()
                last_time = self._last_event_time.get(dev_path, 0.0)

                if now - last_time < self.debounce_seconds:
                    continue

                self._last_event_time[dev_path] = now

                if action in ("add", "change"):
                    self._handle_device_added(device)
                elif action == "remove":
                    self._handle_device_removed(device)

        except Exception:
            pass

    def _handle_device_added(self, device):
        dev_type = device.get("DEVTYPE")
        subsystem = device.subsystem

        if subsystem == "block" and dev_type in ("disk", "partition"):
            node = device.device_node
            mount_path = StorageManager.mount_block_device(node)
            if mount_path:
                self._notify("mounted", {"node": node, "mount_path": mount_path, "type": "usb_storage"})

        elif subsystem == "usb":
            # Check for smartphone MTP / iOS
            ios_status = AppleIOSHandler.inspect_ios_device()
            if ios_status.get("connected"):
                self._notify("connected", {"type": "ios", "status": ios_status})
                return

            android_status = AndroidHandler.inspect_mtp_device()
            if android_status.get("connected"):
                self._notify("connected", {"type": "android", "status": android_status})

    def _handle_device_removed(self, device):
        dev_path = device.device_node or device.device_path
        self._notify("removed", {"path": dev_path})
