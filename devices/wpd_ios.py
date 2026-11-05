"""Windows Portable Devices (WPD) / Windows Shell COM Mobile Phone Detection and Fast Enumeration.

Supports Apple iPhone and Android smartphones connected via USB (MTP/PTP/WPD).
Provides fast recursive enumeration with directory pruning (skipping Android/data,
.thumbnails, app caches), media extension pre-filtering, and automatic source/album categorization.
"""
import os
import sys
from typing import Callable, Dict, Any, Optional, Generator, Tuple, List

# Try dynamic import of Windows COM libraries if on Windows
WIN32_AVAILABLE = False
if sys.platform == "win32":
    try:
        import win32com.client
        import pythoncom
        WIN32_AVAILABLE = True
    except ImportError:
        WIN32_AVAILABLE = False
else:
    win32com = None
    pythoncom = None

from devices.classifier import classify_media

# Common storage folder names found on iOS and Android devices
COMMON_STORAGE_NAMES = {
    "internal storage", "internal shared storage", "pamięć wewnętrzna",
    "phone", "wbudowana pamięć", "storage", "dcim", "sd card", "karta sd",
    "karta pamięci", "external storage"
}

# Android keywords & popular brands
ANDROID_BRAND_KEYWORDS = (
    "android", "galaxy", "pixel", "samsung", "xiaomi", "redmi",
    "motorola", "moto", "oneplus", "huawei", "oppo", "vivo",
    "sony", "xperia", "realme", "poco", "nokia", "lg", "asus",
    "honor", "tcl", "lenovo"
)

# High-yield photo directories to prioritize during scanning
PRIORITY_DIRS = ["dcim", "pictures", "whatsapp", "android", "download", "downloads", "pobrane", "movies"]

# Junk, cache, and system directories to skip entirely during scanning
SKIP_DIRS = {
    "data", "obb", "obj", ".thumbnails", ".trash", ".cache",
    "cache", "lost.dir", "system", "bin", "etc",
    "music", "podcasts", "ringtones", "alarms", "notifications",
    "system volume information", "$recycle.bin", ".git", ".idea"
}


def is_win32_com_available() -> bool:
    """Checks whether Windows Shell COM integration is available on current runtime."""
    return WIN32_AVAILABLE


class WindowsWPDHandler:
    """Handles Windows Portable Device discovery and COM traversal for Apple iPhone and Android."""

    SSFN_MY_COMPUTER = 17  # ShellSpecialFolderConstants: ssfDRIVES (My Computer)

    @classmethod
    def get_shell_computer(cls, shell_dispatch=None):
        """Returns the Shell.Application NameSpace(17) folder object."""
        if shell_dispatch is None:
            if not is_win32_com_available():
                raise RuntimeError("Windows COM is not available on this platform.")
            shell_dispatch = win32com.client.Dispatch("Shell.Application")
        return shell_dispatch.NameSpace(cls.SSFN_MY_COMPUTER)

    @classmethod
    def find_all_mobile_devices(cls, computer_folder=None, shell_dispatch=None) -> List[Dict[str, Any]]:
        """
        Enumerates connected portable devices in My Computer (both iPhone and Android).
        Returns list of dicts: [{'name': str, 'device_type': 'iphone'|'android', 'device': item}]
        """
        if computer_folder is None:
            computer_folder = cls.get_shell_computer(shell_dispatch=shell_dispatch)

        if not computer_folder:
            return []

        devices = []
        try:
            items = computer_folder.Items()
            for device in items:
                try:
                    name = getattr(device, "Name", "") or ""
                    path_str = str(getattr(device, "Path", "") or "")

                    # Check if this is a regular drive letter or network path
                    is_drive_letter = len(path_str) >= 2 and path_str[1] == ":"
                    is_network_path = path_str.startswith(r"\\")
                    if is_drive_letter or is_network_path:
                        continue

                    name_lower = name.lower()
                    if "iphone" in name_lower or "ipad" in name_lower:
                        devices.append({
                            "name": name,
                            "device_type": "iphone",
                            "device": device
                        })
                    elif any(k in name_lower for k in ANDROID_BRAND_KEYWORDS):
                        devices.append({
                            "name": name,
                            "device_type": "android",
                            "device": device
                        })
                    else:
                        # Non-drive shell folder in My Computer (typically WPD MTP device)
                        is_folder = getattr(device, "IsFolder", False)
                        if is_folder and not path_str.lower().endswith((".lnk", ".url", ".zip")):
                            devices.append({
                                "name": name,
                                "device_type": "android",
                                "device": device
                            })
                except Exception:
                    continue
        except Exception:
            return []
        return devices

    @classmethod
    def find_iphone_device(cls, computer_folder=None, shell_dispatch=None) -> Optional[Any]:
        """
        Enumerates connected portable devices in My Computer and finds an Apple iPhone.
        Case-insensitive match for 'iphone' in device name.
        """
        if computer_folder is None:
            computer_folder = cls.get_shell_computer(shell_dispatch=shell_dispatch)

        if not computer_folder:
            return None

        try:
            items = computer_folder.Items()
            for device in items:
                try:
                    name = getattr(device, "Name", "") or ""
                    if "iphone" in name.lower() or "ipad" in name.lower():
                        return device
                except Exception:
                    continue
        except Exception:
            return None
        return None

    @classmethod
    def find_android_device(cls, computer_folder=None, shell_dispatch=None) -> Optional[Any]:
        """Enumerates connected portable devices and finds an Android device."""
        devices = cls.find_all_mobile_devices(computer_folder=computer_folder, shell_dispatch=shell_dispatch)
        for d in devices:
            if d["device_type"] == "android":
                return d["device"]
        return None

    @classmethod
    def find_mobile_device(cls, computer_folder=None, shell_dispatch=None) -> Optional[Any]:
        """Finds any connected mobile device (preferring iPhone if connected, otherwise Android)."""
        devices = cls.find_all_mobile_devices(computer_folder=computer_folder, shell_dispatch=shell_dispatch)
        if not devices:
            return None
        # Return iPhone first if available, else first device
        for d in devices:
            if d["device_type"] == "iphone":
                return d["device"]
        return devices[0]["device"]

    @classmethod
    def find_internal_storage(cls, iphone_device) -> Optional[Any]:
        """
        Finds the 'Internal Storage' folder within a mobile device object.
        Returns the folder object or None.
        """
        if not iphone_device:
            return None

        try:
            device_folder = iphone_device.GetFolder
            if not device_folder:
                return None
            for item in device_folder.Items():
                try:
                    name = getattr(item, "Name", "") or ""
                    if name.lower() in COMMON_STORAGE_NAMES:
                        return item.GetFolder
                except Exception:
                    continue
            # If no common storage folder found, but device contains DCIM directly
            for item in device_folder.Items():
                try:
                    name = getattr(item, "Name", "") or ""
                    if name.lower() == "dcim":
                        return device_folder
                except Exception:
                    continue
        except Exception:
            return None
        return None

    @classmethod
    def find_device_storages(cls, device) -> List[Any]:
        """
        Finds all accessible storage folders on the device (Internal Storage, SD Card, etc.).
        Returns list of folder objects.
        """
        if not device:
            return []

        storages = []
        try:
            device_folder = device.GetFolder
            if not device_folder:
                return []
            for item in device_folder.Items():
                try:
                    name = getattr(item, "Name", "") or ""
                    if name.lower() in COMMON_STORAGE_NAMES:
                        f = item.GetFolder
                        if f:
                            storages.append(f)
                except Exception:
                    continue

            # Fallback if no specific storage found but device folder has items
            if not storages:
                storages.append(device_folder)
        except Exception:
            pass
        return storages

    @classmethod
    def check_iphone_connected(cls, shell_dispatch=None) -> Tuple[bool, Optional[str]]:
        """
        Quick check if an iPhone is currently connected.
        Returns (is_connected, device_name).
        """
        try:
            device = cls.find_iphone_device(shell_dispatch=shell_dispatch)
            if device:
                return True, getattr(device, "Name", "Apple iPhone")
        except Exception:
            pass
        return False, None

    @classmethod
    def check_mobile_connected(cls, shell_dispatch=None) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Quick check if any mobile phone (iPhone or Android) is currently connected.
        Returns (is_connected, device_name, device_type).
        """
        try:
            devices = cls.find_all_mobile_devices(shell_dispatch=shell_dispatch)
            if devices:
                first = devices[0]
                return True, first["name"], first["device_type"]
        except Exception:
            pass
        return False, None, None

    @classmethod
    def scan_storage_generator(
        cls,
        storage_folder,
        is_cancelled: Optional[Callable[[], bool]] = None,
        on_folder_enter: Optional[Callable[[str], None]] = None,
        on_file_found: Optional[Callable[[str, str], None]] = None,
        on_error: Optional[Callable[[str, Exception], None]] = None,
        initial_path: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Recursively traverses mobile device storage folder using Windows Shell COM folder items.
        Includes directory pruning, media extension pre-filtering, and automatic categorization.

        Yields events:
            {"type": "folder_enter", "path": current_path}
            {"type": "file", "path": current_path, "name": item_name, "extension": ext,
             "relative_path": rel_path, "modified_time": mtime, "size_bytes": size,
             "category": cat, "category_name": cat_name, "album": album}
            {"type": "folder_exit", "path": current_path}
            {"type": "error", "path": current_path, "error": str(err)}
        """
        if not storage_folder:
            return

        from devices.scanner import is_image_extension, is_video_extension

        def _traverse(folder, current_path: str):
            if is_cancelled and is_cancelled():
                return

            if on_folder_enter:
                try:
                    on_folder_enter(current_path)
                except Exception:
                    pass

            yield {"type": "folder_enter", "path": current_path}

            try:
                raw_items = folder.Items()
            except Exception as e:
                if on_error:
                    try:
                        on_error(current_path, e)
                    except Exception:
                        pass
                yield {"type": "error", "path": current_path, "error": str(e)}
                return

            # Separate folder items and file items
            folders_to_visit = []
            files_to_process = []

            for item in raw_items:
                if is_cancelled and is_cancelled():
                    return

                try:
                    is_f = item.IsFolder
                    item_name = getattr(item, "Name", "") or ""
                except Exception as e:
                    if on_error:
                        try:
                            on_error(current_path, e)
                        except Exception:
                            pass
                    yield {"type": "error", "path": current_path, "error": str(e)}
                    continue

                if is_f:
                    # Directory Pruning for Performance:
                    name_lower = item_name.lower()
                    # Skip hidden folders (.thumbnails, .trash, .cache, etc.)
                    if item_name.startswith(".") and name_lower not in {".", ".."}:
                        continue
                    # Skip known junk / cache / non-photo folders
                    if name_lower in SKIP_DIRS:
                        continue
                    # If inside 'Android' folder, only traverse 'media' (skip 'data', 'obb')
                    parent_lower = current_path.lower()
                    if parent_lower.endswith("\\android") or parent_lower == "android":
                        if name_lower != "media":
                            continue

                    folders_to_visit.append((item_name, item))
                else:
                    files_to_process.append((item_name, item))

            # Process files in current directory
            for item_name, item in files_to_process:
                if is_cancelled and is_cancelled():
                    return

                ext = os.path.splitext(item_name)[1].lower()
                is_img = is_image_extension(ext)
                is_vid = is_video_extension(ext)

                # Pre-filtering: Fetch COM properties (ModifyDate, Size)
                mtime = 0.0
                size = 0
                if is_img or is_vid or not WIN32_AVAILABLE:
                    try:
                        mdate = getattr(item, "ModifyDate", None)
                        if mdate is not None:
                            if hasattr(mdate, "timestamp"):
                                mtime = mdate.timestamp()
                            else:
                                import datetime
                                if isinstance(mdate, (datetime.datetime, datetime.date)):
                                    mtime = mdate.timestamp()
                                elif isinstance(mdate, (int, float)):
                                    mtime = float(mdate)
                        size = getattr(item, "Size", 0) or 0
                    except Exception:
                        pass

                sub_path = f"{current_path}\\{item_name}" if current_path else item_name
                cat_info = classify_media(item_name, sub_path)

                if on_file_found:
                    try:
                        on_file_found(current_path, item_name)
                    except Exception:
                        pass

                yield {
                    "type": "file",
                    "path": current_path,
                    "name": item_name,
                    "extension": ext,
                    "relative_path": sub_path,
                    "modified_time": mtime,
                    "size_bytes": size,
                    "category": cat_info["category"],
                    "category_name": cat_info["category_name"],
                    "album": cat_info["album"],
                }

            # Prioritize high-yield subfolders (DCIM, Pictures, WhatsApp, etc.)
            def _folder_sort_key(entry):
                fname = entry[0].lower()
                for rank, p in enumerate(PRIORITY_DIRS):
                    if p == fname:
                        return rank
                return len(PRIORITY_DIRS)

            folders_to_visit.sort(key=_folder_sort_key)

            # Traverse subdirectories
            for item_name, item in folders_to_visit:
                sub_path = f"{current_path}\\{item_name}" if current_path else item_name
                try:
                    sub_folder = item.GetFolder
                    if sub_folder:
                        yield from _traverse(sub_folder, sub_path)
                    else:
                        yield {"type": "error", "path": sub_path, "error": "Folder not accessible"}
                except Exception as e:
                    if on_error:
                        try:
                            on_error(sub_path, e)
                        except Exception:
                            pass
                    yield {"type": "error", "path": sub_path, "error": str(e)}

            yield {"type": "folder_exit", "path": current_path}

        root_path = initial_path if initial_path is not None else "Apple iPhone\\Internal Storage"
        yield from _traverse(storage_folder, root_path)
