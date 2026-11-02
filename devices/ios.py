"""Apple iOS (iPhone/iPad) detection, trust verification, and ifuse mounting."""
import shutil
import subprocess
from typing import Dict, Any, Optional


class AppleIOSHandler:
    @staticmethod
    def is_idevice_available() -> bool:
        return shutil.which("idevicepair") is not None

    @staticmethod
    def inspect_ios_device() -> Dict[str, Any]:
        """
        Inspects connected iOS device.
        Detects whether device is paired/trusted or requires user to tap 'Trust'.
        """
        if not AppleIOSHandler.is_idevice_available():
            return {
                "connected": False,
                "accessible": False,
                "status": "UNAVAILABLE",
                "guidance": "iOS support tools (libimobiledevice) not installed.",
            }

        try:
            # Check pairing status
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
                # Trigger pair prompt
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
