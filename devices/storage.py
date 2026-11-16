"""Storage management and mounting via udisks2 / udisksctl for mass storage devices."""
import json
import subprocess
import shutil
import os
import sys
import time
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


# Filesystems that must never be auto-mounted or offered to customers
_NON_DATA_FSTYPES = {"swap", "crypto_LUKS", "LVM2_member", "linux_raid_member", "zfs_member", "bitlocker"}
_MOUNT_RETRY_SECONDS = 30.0


def format_size(num_bytes: int) -> str:
    """Human friendly size, e.g. 31914983424 -> '29.7 GB' (binary units, like Windows Explorer)."""
    if not num_bytes or num_bytes < 0:
        return ""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes")


def _flatten_lsblk(nodes: List[Dict[str, Any]], parent: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Flattens lsblk's tree; partitions inherit vendor/model/transport of their disk."""
    out: List[Dict[str, Any]] = []
    for n in nodes:
        mounts = n.get("mountpoints")
        if mounts is None:
            mounts = [n.get("mountpoint")]
        entry = {
            "path": n.get("path") or ("/dev/" + (n.get("name") or "")),
            "type": n.get("type") or "",
            "fstype": n.get("fstype") or "",
            "label": (n.get("label") or "").strip(),
            "vendor": (n.get("vendor") or (parent or {}).get("vendor") or "").strip(),
            "model": (n.get("model") or (parent or {}).get("model") or "").strip(),
            "tran": (n.get("tran") or (parent or {}).get("tran") or "").strip(),
            "removable": _truthy(n.get("rm")) or _truthy(n.get("hotplug"))
                         or bool(parent and parent.get("removable")),
            "size": int(n.get("size") or 0),
            "mountpoints": [m for m in mounts if m and not str(m).startswith("[")],
            "has_children": bool(n.get("children")),
        }
        if entry["tran"] == "usb":
            entry["removable"] = True
        out.append(entry)
        out.extend(_flatten_lsblk(n.get("children") or [], entry))
    return out


class StorageManager:
    """Handles mounting and unmounting of block storage devices (USB drives, SD cards, Optical)."""

    _mount_attempts: Dict[str, float] = {}

    @staticmethod
    def list_block_devices() -> List[Dict[str, Any]]:
        """Block devices from ``lsblk`` (Linux). Returns [] when lsblk is unavailable or fails."""
        if sys.platform == "win32" or not shutil.which("lsblk"):
            return []
        for mount_col in ("MOUNTPOINTS", "MOUNTPOINT"):          # older util-linux only has the latter
            try:
                res = subprocess.run(
                    ["lsblk", "-J", "-b", "-o",
                     f"PATH,NAME,TYPE,RM,HOTPLUG,TRAN,FSTYPE,LABEL,MODEL,VENDOR,SIZE,{mount_col}"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                if res.returncode == 0:
                    return _flatten_lsblk(json.loads(res.stdout).get("blockdevices", []))
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
        return []

    @staticmethod
    def mount_pending_removables() -> List[str]:
        """Mounts plugged-in removable partitions / discs that nobody mounted yet.

        A headless server has no desktop to auto-mount a USB stick, so without this the stick
        would never appear. Failed attempts are retried only every 30 s.
        """
        if sys.platform == "win32" or not StorageManager.is_udisksctl_available():
            return []
        mounted: List[str] = []
        now = time.time()
        for dev in StorageManager.list_block_devices():
            if dev["mountpoints"] or not dev["fstype"] or dev["fstype"] in _NON_DATA_FSTYPES:
                continue
            is_optical = dev["type"] == "rom"
            if not (is_optical or (dev["removable"] and dev["type"] in ("part", "disk"))):
                continue
            if dev["type"] == "disk" and dev["has_children"]:
                continue
            if now - StorageManager._mount_attempts.get(dev["path"], 0.0) < _MOUNT_RETRY_SECONDS:
                continue
            StorageManager._mount_attempts[dev["path"]] = now
            path = StorageManager.mount_block_device(dev["path"])
            if path:
                mounted.append(path)
        return mounted

    @staticmethod
    def windows_volume_info(root: str) -> Dict[str, Any]:
        """Volume label and total size of a Windows drive root such as 'E:\\' ({} if unreadable)."""
        info: Dict[str, Any] = {}
        try:
            import ctypes
            label = ctypes.create_unicode_buffer(261)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), label, 261, None, None, None, None, 0)
            if ok:
                info["label"] = label.value.strip()
        except Exception:
            pass
        try:
            info["size"] = shutil.disk_usage(root).total
        except Exception:
            pass
        return info

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
                        vol = StorageManager.windows_volume_info(root)
                        label = vol.get("label", "")
                        detected.append({
                            "name": f"{label} ({letter}:)" if label else f"USB Drive ({letter}:)",
                            "path": root,
                            "type": "removable_storage",
                            "label": label, "device": "", "drive": f"{letter}:",
                            "size": int(vol.get("size", 0)),
                        })
        else:
            blocks = StorageManager.list_block_devices()
            if blocks:
                # Real mounts of removable devices, with the device's own name
                for dev in blocks:
                    if dev["type"] == "rom" or not dev["removable"] or not dev["mountpoints"]:
                        continue
                    mount = dev["mountpoints"][0]
                    device = " ".join(x for x in (dev["vendor"], dev["model"]) if x)
                    base = dev["label"] or device or Path(mount).name
                    size = format_size(dev["size"])
                    detected.append({
                        "name": f"{base} ({size})" if size else base,
                        "path": str(Path(mount).resolve()),
                        "type": "removable_storage",
                        "label": dev["label"], "device": device, "drive": "", "size": dev["size"],
                    })
            else:
                # No lsblk (minimal container etc.): look for mounted folders instead
                for root in ["/media", "/run/media", "/mnt"]:
                    p = Path(root)
                    if p.exists() and p.is_dir():
                        for sub in p.iterdir():
                            if sub.is_dir() and not sub.name.startswith("."):
                                detected.append({
                                    "name": sub.name,
                                    "path": str(sub.resolve()),
                                    "type": "removable_storage",
                                    "label": sub.name, "device": "", "drive": "", "size": 0,
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
                        vol = StorageManager.windows_volume_info(root)
                        label = vol.get("label", "")
                        detected.append({
                            "name": f"{label} ({letter}:)" if label else f"CD/DVD Drive ({letter}:)",
                            "path": root,
                            "type": "optical_media",
                            "label": label, "device": "", "drive": f"{letter}:",
                            "size": int(vol.get("size", 0)),
                        })
        else:
            blocks = StorageManager.list_block_devices()
            for dev in blocks:
                if dev["type"] == "rom" and dev["mountpoints"]:
                    detected.append({
                        "name": dev["label"] or "Disc (CD / DVD Drive)",
                        "path": str(Path(dev["mountpoints"][0]).resolve()),
                        "type": "optical_media",
                        "label": dev["label"], "device": "", "drive": "", "size": dev["size"],
                    })
            if not blocks:
                for r in ["/media/cdrom", "/mnt/cdrom"]:
                    if os.path.exists(r) and os.path.isdir(r):
                        detected.append({
                            "name": "Disc (CD / DVD Drive)", "path": r, "type": "optical_media",
                            "label": "", "device": "", "drive": "", "size": 0,
                        })

        return detected
