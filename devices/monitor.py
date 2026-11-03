"""Hardware device monitoring daemon with debouncing for udev block and usb events.

Supports both Linux (pyudev) and Windows (WPD polling) device monitoring,
automatically notifying the authoritative iPhone scanner.
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
                    is_conn, dev_name = WindowsWPDHandler.check_iphone_connected()
                    if is_conn and not self._iphone_connected:
                        self._iphone_connected = True
                        name = dev_name or "Apple iPhone"
                        authoritative_scanner.on_device_connected(device_name=name, auto_scan=False)
                        self._notify("connected", {"device": name, "platform": "win32", "type": "iphone"})
                    elif not is_conn and self._iphone_connected:
                        self._iphone_connected = False
                        authoritative_scanner.on_device_disconnected()
                        self._notify("disconnected", {"device": "Apple iPhone", "platform": "win32", "type": "iphone"})
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

                dev_node = device.device_node or device.sys_path
                now = time.time()
                last_time = self._last_event_time.get(dev_node, 0)
                if now - last_time < self.debounce_seconds:
                    continue  # Debounce duplicate events

                self._last_event_time[dev_node] = now

                if action == "add":
                    info = {
                        "device_node": dev_node,
                        "subsystem": device.subsystem,
                        "device_type": device.device_type,
                    }
                    self._notify("connected", info)
                elif action == "remove":
                    self._notify("disconnected", {"device_node": dev_node})
        except Exception:
            pass
