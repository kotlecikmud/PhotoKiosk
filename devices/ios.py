"""Apple iOS (iPhone/iPad) detection, trust verification, and WPD / ifuse mounting."""
import shutil
import subprocess
import sys
from typing import Dict, Any, Optional

from devices.wpd_ios import WindowsWPDHandler, is_win32_com_available


class AppleIOSHandler:
    @staticmethod
    def is_idevice_available() -> bool:
        if sys.platform == "win32":
            return is_win32_com_available()
        return shutil.which("idevicepair") is not None

    @staticmethod
    def inspect_ios_device() -> Dict[str, Any]:
        """
        Inspects connected iOS device.
        On Windows, uses Windows Shell COM (WPD) detection.
        On Linux, uses libimobiledevice (idevicepair).
        """
        if sys.platform == "win32":
            return AppleIOSHandler._inspect_windows_wpd()
        else:
            return AppleIOSHandler._inspect_linux_libimobiledevice()

    @staticmethod
    def _inspect_windows_wpd() -> Dict[str, Any]:
        """Inspects iPhone via Windows Shell COM / WPD."""
        if not is_win32_com_available():
            return {
                "connected": False,
                "accessible": False,
                "status": "UNAVAILABLE",
                "guidance": "Windows COM support (win32com) not available.",
            }

        try:
            connected, name = WindowsWPDHandler.check_iphone_connected()
            if not connected:
                return {
                    "connected": False,
                    "accessible": False,
                    "status": "DISCONNECTED",
                    "guidance": "No iOS device detected via Windows Portable Devices.",
                }

            device = WindowsWPDHandler.find_iphone_device()
            storage = WindowsWPDHandler.find_internal_storage(device) if device else None

            if storage is not None:
                return {
                    "connected": True,
                    "accessible": True,
                    "status": "TRUSTED",
                    "guidance": f"{name} detected and accessible.",
                    "device_name": name,
                }
            else:
                return {
                    "connected": True,
                    "accessible": False,
                    "status": "NEEDS_TRUST",
                    "guidance": f"{name} detected. Please unlock your iPhone and tap 'Trust This Computer'.",
                    "device_name": name,
                }
        except Exception as e:
            return {
                "connected": False,
                "accessible": False,
                "status": "ERROR",
                "guidance": f"Windows WPD inspection error: {e}",
            }

    @staticmethod
    def _inspect_linux_libimobiledevice() -> Dict[str, Any]:
        """Inspects iPhone on Linux using idevicepair."""
        if not shutil.which("idevicepair"):
            return {
                "connected": False,
                "accessible": False,
                "status": "UNAVAILABLE",
                "guidance": "iOS support tools (libimobiledevice) not installed.",
            }

        try:
            res = subprocess.run(["idevicepair", "validate"], capture_output=True, text=True, check=False)
            output = res.stdout.strip().lower() + res.stderr.strip().lower()

            if "success" in output:
                return {
                    "connected": True,
                    "accessible": True,
                    "status": "TRUSTED",
                    "guidance": "Apple device trusted and accessible.",
                }
            elif "not paired" in output or "trust" in output or "passcode" in output:
                subprocess.run(["idevicepair", "pair"], capture_output=True, check=False)
                return {
                    "connected": True,
                    "accessible": False,
                    "status": "NEEDS_TRUST",
                    "guidance": "iPhone/iPad detected: Please unlock your device and tap 'Trust This Computer'.",
                }
            elif "no device found" in output:
                return {
                    "connected": False,
                    "accessible": False,
                    "status": "DISCONNECTED",
                    "guidance": "No iOS device detected.",
                }
        except Exception as e:
            return {
                "connected": False,
                "accessible": False,
                "status": "ERROR",
                "guidance": f"iOS inspection error: {e}",
            }

        return {
            "connected": False,
            "accessible": False,
            "status": "UNKNOWN",
            "guidance": "Status unknown.",
        }
