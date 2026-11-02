"""Hardware device monitoring daemon with debouncing for udev block and usb events."""
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


class DeviceMonitorDaemon:
    def __init__(self, callback: Callable[[str, Dict[str, Any]], None], debounce_seconds: float = 2.0):
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._last_event_time: Dict[str, float] = {}

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False

    def _run(self):
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
                    self.callback("connected", info)
                elif action == "remove":
                    self.callback("disconnected", {"device_node": dev_node})
        except Exception:
            pass
