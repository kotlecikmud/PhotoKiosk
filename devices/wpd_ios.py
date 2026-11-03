"""Windows Portable Devices (WPD) / Windows Shell COM iPhone detection and storage enumeration.

This module provides Windows-specific integration using Windows Shell COM objects
(Shell.Application NameSpace 17) to enumerate portable devices, detect connected iPhones,
locate 'Internal Storage', and recursively traverse directories to enumerate files and folders.
"""
import os
import sys
from typing import Callable, Dict, Any, Optional, Generator, Tuple

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


def is_win32_com_available() -> bool:
    """Checks whether Windows Shell COM integration is available on current runtime."""
    return WIN32_AVAILABLE


class WindowsWPDHandler:
    """Handles Windows Portable Device discovery and COM traversal for Apple iPhone."""

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
                    if "iphone" in name.lower():
                        return device
                except Exception:
                    continue
        except Exception:
            return None
        return None

    @classmethod
    def find_internal_storage(cls, iphone_device) -> Optional[Any]:
        """
        Finds the 'Internal Storage' folder within an iPhone device object.
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
                    if name.lower() == "internal storage":
                        return item.GetFolder
                except Exception:
                    continue
        except Exception:
            return None
        return None

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
    def scan_storage_generator(
        cls,
        storage_folder,
        is_cancelled: Optional[Callable[[], bool]] = None,
        on_folder_enter: Optional[Callable[[str], None]] = None,
        on_file_found: Optional[Callable[[str, str], None]] = None,
        on_error: Optional[Callable[[str, Exception], None]] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Recursively traverses iPhone storage folder using Windows Shell COM folder items.

        Yields events:
            {"type": "folder_enter", "path": current_path}
            {"type": "file", "path": current_path, "name": item_name, "extension": ext}
            {"type": "folder_exit", "path": current_path}
            {"type": "error", "path": current_path, "error": str(err)}
        """
        if not storage_folder:
            return

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
                items = folder.Items()
            except Exception as e:
                if on_error:
                    try:
                        on_error(current_path, e)
                    except Exception:
                        pass
                yield {"type": "error", "path": current_path, "error": str(e)}
                return

            for item in items:
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
                else:
                    ext = os.path.splitext(item_name)[1].lower()
                    mtime = 0.0
                    size = 0
                    try:
                        # Extract ModifyDate or Size if available from COM item
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
                        "relative_path": f"{current_path}\\{item_name}",
                        "modified_time": mtime,
                        "size_bytes": size,
                    }

            yield {"type": "folder_exit", "path": current_path}

        yield from _traverse(storage_folder, "Apple iPhone\\Internal Storage")
