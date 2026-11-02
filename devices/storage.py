"""Storage management and mounting via udisks2 / udisksctl for mass storage devices."""
import subprocess
import shutil
import os
from pathlib import Path
from typing import Optional, Dict, Any, List


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
    def get_mounted_media_paths() -> List[Dict[str, str]]:
        """Scans standard Linux mount points for removable media (/media, /mnt, /run/media)."""
        detected = []
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
