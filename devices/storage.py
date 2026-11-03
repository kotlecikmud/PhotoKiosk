"""Storage management and mounting via udisks2 / udisksctl for mass storage devices."""
import subprocess
import shutil
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any, List


# Windows GetDriveType constants
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6


class StorageManager:
    """Handles mounting and unmounting of block storage devices (USB drives, SD cards, Optical)."""

    @staticmethod
    def is_udisksctl_available() -> bool:
        return shutil.which("udisksctl") is not None

    @staticmethod
    def mount_block_device(device_node: str) -> Optional[str]:
        """Mounts block device via udisksctl and returns mount path."""
        if not StorageManager.is_udisksctl_available():
            # Fallback or simulation for container / dev environment
            return None

        try:
            res = subprocess.run(
                ["udisksctl", "mount", "-b", device_node, "--no-user-interaction"],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                # Output format: "Mounted /dev/sdb1 at /media/..."
                parts = res.stdout.strip().split(" at ")
                if len(parts) >= 2:
                    return parts[1].strip()
        except Exception:
            pass
        return None

    @staticmethod
    def unmount_block_device(device_node: str) -> bool:
        if not StorageManager.is_udisksctl_available():
            return False

        try:
            res = subprocess.run(
                ["udisksctl", "unmount", "-b", device_node, "--no-user-interaction"],
                capture_output=True,
                text=True,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    @staticmethod
    def get_windows_drive_type(drive_letter: str) -> int:
        """Returns Windows GetDriveType integer for a drive letter (e.g. 'E:\\')."""
        if sys.platform != "win32":
            return DRIVE_UNKNOWN
        try:
            import ctypes
            root_path = f"{drive_letter.rstrip('\\')}\\"
            return ctypes.windll.kernel32.GetDriveTypeW(root_path)
        except Exception:
            return DRIVE_UNKNOWN

    @staticmethod
    def get_mounted_media_paths() -> List[Dict[str, str]]:
        """Scans available removable media paths on Windows and Linux."""
        detected = []

        if sys.platform == "win32":
            # Scan drive letters A-Z for removable drives
            import string
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root):
                    dtype = StorageManager.get_windows_drive_type(root)
                    if dtype == DRIVE_REMOVABLE:
                        detected.append({
                            "name": f"USB Drive ({letter}:)",
                            "path": root,
                            "type": "removable_storage",
                        })
        else:
            # Linux search paths
            search_roots = ["/media", "/run/media", "/mnt"]
            for root in search_roots:
                p = Path(root)
                if p.exists() and p.is_dir():
                    for sub in p.iterdir():
                        if sub.is_dir() and not sub.name.startswith("."):
                            detected.append({
                                "name": sub.name,
                                "path": str(sub.resolve()),
                                "type": "removable_storage",
                            })

        return detected

    @staticmethod
    def get_optical_media_paths() -> List[Dict[str, str]]:
        """Scans optical disc drives (CD/DVD) on Windows and Linux."""
        detected = []

        if sys.platform == "win32":
            import string
            for letter in string.ascii_uppercase:
                root = f"{letter}:\\"
                if os.path.exists(root):
                    dtype = StorageManager.get_windows_drive_type(root)
                    if dtype == DRIVE_CDROM:
                        detected.append({
                            "name": f"CD/DVD Drive ({letter}:)",
                            "path": root,
                            "type": "optical_media",
                        })
        else:
            search_roots = ["/media/cdrom", "/mnt/cdrom"]
            for r in search_roots:
                if os.path.exists(r) and os.path.isdir(r):
                    detected.append({
                        "name": "Disc (CD / DVD Drive)",
                        "path": r,
                        "type": "optical_media",
                    })

        return detected
