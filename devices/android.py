"""Android MTP smartphone detection, access guidance, and jmtpfs/gvfs mounting."""
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional


class AndroidHandler:
    @staticmethod
    def is_mtp_available() -> bool:
        return shutil.which("jmtpfs") is not None or shutil.which("gio") is not None

    @staticmethod
    def inspect_mtp_device(usb_path: str = "") -> Dict[str, Any]:
        """
        Inspects connected Android device.
        Detects whether phone is unlocked with File Transfer (MTP) enabled,
        or locked in Charge Only mode.
        """
        # Checks if jmtpfs or simplemtp sees storage
        if shutil.which("jmtpfs"):
            res = subprocess.run(["jmtpfs", "-l"], capture_output=True, text=True, check=False)
            if "Device 0" in res.stdout or "Android" in res.stdout:
                return {
                    "connected": True,
                    "accessible": True,
                    "status": "READY",
                    "guidance": "Android device ready.",
                }
            elif "No mtp devices found" not in res.stdout.lower() and res.stdout.strip():
                return {
                    "connected": True,
                    "accessible": False,
                    "status": "NEEDS_UNLOCK",
                    "guidance": "Android device detected. Please unlock phone and select 'File Transfer / MTP'.",
                }

        return {
            "connected": False,
            "accessible": False,
            "status": "DISCONNECTED",
            "guidance": "No Android device detected.",
        }
