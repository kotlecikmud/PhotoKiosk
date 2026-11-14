"""Photo sources for the web kiosk, with safe (traversal-proof) path resolution.

The browser never sees or sends file-system paths. Instead:

* a *source* (server photo folder, USB stick, CD/DVD, scanned phone) has an opaque id
  such as ``shared`` or ``usb-3fa9c1d2e0``; the server maps it to a root directory, and
* a *photo id* is the URL-safe base64 of the photo's path **relative to its source root**.

``SourceRegistry.resolve_photo`` decodes a photo id and guarantees the resulting file lives
inside the source root (no ``..`` segments, no absolute paths, no symlink escapes) and is a
supported image, so a crafted id can never read an arbitrary file on the machine.
"""
import base64
import binascii
import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Dict, List, Optional

from backend.library import SUPPORTED_EXTENSIONS, scan_directory_for_photos
from devices.storage import StorageManager

KIND_SHARED = "shared"
KIND_REMOVABLE = "removable"
KIND_OPTICAL = "optical"
KIND_PHONE = "phone"

SHARED_SOURCE_ID = "shared"
# Finished orders are mirrored into <shared>/orders for LAN access. They belong to earlier
# customers, so they must never be browsable (or resolvable) from the kiosk.
SHARED_RESERVED_DIRS = ("orders",)
_ID_CHARS_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PHONE_ID_RE = re.compile(r"^phone-(\d{1,9})$")

DEFAULT_ENABLED = {
    "source_iphone": True,
    "source_android": True,
    "source_removable": True,
    "source_cddvd": True,
    "source_network": True,
}


class SourceError(Exception):
    """Base class for source / photo resolution problems."""


class SourceNotFound(SourceError):
    """The source or photo does not exist (any more)."""


class InvalidPhotoId(SourceError):
    """The photo id is malformed or tries to leave the source root."""


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    label: str
    root: Path


def encode_photo_id(rel_path: str) -> str:
    """URL-safe, padding-free base64 of a '/'-separated relative path."""
    return base64.urlsafe_b64encode(rel_path.encode("utf-8")).decode("ascii").rstrip("=")


def decode_photo_id(photo_id: str) -> str:
    """Inverse of :func:`encode_photo_id`; raises :class:`InvalidPhotoId` on garbage."""
    if not photo_id or len(photo_id) > 2048 or not _ID_CHARS_RE.match(photo_id):
        raise InvalidPhotoId("malformed photo id")
    try:
        padded = photo_id + "=" * (-len(photo_id) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise InvalidPhotoId("malformed photo id") from exc


def _hash_id(prefix: str, path: str) -> str:
    return f"{prefix}-{hashlib.sha1(os.path.normcase(path).encode('utf-8')).hexdigest()[:10]}"


def resolve_inside(root: Path, rel_path: str) -> Path:
    """Join ``rel_path`` onto ``root`` and prove the result stays inside ``root``."""
    if not rel_path or "\x00" in rel_path:
        raise InvalidPhotoId("empty path")
    posix = PurePosixPath(rel_path)
    windows = PureWindowsPath(rel_path)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root:
        raise InvalidPhotoId("absolute paths are not allowed")
    if ".." in posix.parts or ".." in windows.parts:
        raise InvalidPhotoId("parent segments are not allowed")

    resolved_root = root.resolve()
    full = (resolved_root / Path(*posix.parts)).resolve()
    if not full.is_relative_to(resolved_root):
        raise InvalidPhotoId("path escapes the source root")
    return full


class SourceRegistry:
    """Discovers photo sources and resolves their photos safely."""

    def __init__(
        self,
        shared_dir: str = "storage/shared",
        temp_base: str = "storage/temp_sessions",
        enabled_provider: Optional[Callable[[], Dict[str, bool]]] = None,
    ):
        self.shared_dir = Path(shared_dir)
        self.temp_base = Path(temp_base)
        self._enabled_provider = enabled_provider or (lambda: dict(DEFAULT_ENABLED))
        self._lock = threading.RLock()
        self._sources: Dict[str, Source] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def enabled_flags(self) -> Dict[str, bool]:
        flags = dict(DEFAULT_ENABLED)
        try:
            flags.update(self._enabled_provider())
        except Exception:
            pass
        return flags

    def discover(self) -> List[Source]:
        """Re-computes the browsable sources honouring the admin's source toggles.

        Phone sessions are not part of this list: a phone is shown as a device that must
        be scanned first (see ``register_phone_session``).
        """
        flags = self.enabled_flags()
        found: List[Source] = []
        seen_paths = set()

        if flags["source_network"]:
            self.shared_dir.mkdir(parents=True, exist_ok=True)
            root = self.shared_dir.resolve()
            found.append(Source(SHARED_SOURCE_ID, KIND_SHARED, "shared", root))
            seen_paths.add(os.path.normcase(str(root)))

        # Optical first so a /media/cdrom style mount is not also listed as a USB stick.
        optical: List[Source] = []
        if flags["source_cddvd"]:
            for dev in StorageManager.get_optical_media_paths():
                path = str(Path(dev["path"]))
                if os.path.normcase(path) in seen_paths:
                    continue
                seen_paths.add(os.path.normcase(path))
                optical.append(Source(_hash_id("disc", path), KIND_OPTICAL, dev["name"], Path(path)))

        removable: List[Source] = []
        if flags["source_removable"]:
            for dev in StorageManager.get_mounted_media_paths():
                path = str(Path(dev["path"]))
                if os.path.normcase(path) in seen_paths:
                    continue
                seen_paths.add(os.path.normcase(path))
                removable.append(Source(_hash_id("usb", path), KIND_REMOVABLE, dev["name"], Path(path)))

        found.extend(removable)
        found.extend(optical)

        with self._lock:
            # Keep phone sessions registered; replace everything else with the fresh view.
            kept = {k: v for k, v in self._sources.items() if v.kind == KIND_PHONE}
            self._sources = {s.id: s for s in found}
            self._sources.update(kept)
        return found

    def register_phone_session(self, session_id: int, label: str) -> Optional[Source]:
        """Registers the staged temp directory of a scanned phone as a browsable source."""
        root = self.temp_base / f"session_{int(session_id)}"
        if not root.is_dir():
            return None
        src = Source(f"phone-{int(session_id)}", KIND_PHONE, label, root.resolve())
        with self._lock:
            self._sources[src.id] = src
        return src

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, source_id: str) -> Source:
        with self._lock:
            src = self._sources.get(source_id)
        if src is None:
            m = _PHONE_ID_RE.match(source_id or "")
            if m:
                src = self.register_phone_session(int(m.group(1)), "")
            else:
                self.discover()  # e.g. after a restart, or a stick plugged in meanwhile
                with self._lock:
                    src = self._sources.get(source_id)
        if src is None or not src.root.exists():
            raise SourceNotFound(f"unknown or unavailable source: {source_id!r}")
        return src

    def resolve_photo(self, source_id: str, photo_id: str) -> Path:
        """Return the absolute path of a photo; raises SourceError subclasses on any problem."""
        src = self.get(source_id)
        rel = decode_photo_id(photo_id)
        full = resolve_inside(src.root, rel)
        if src.kind == KIND_SHARED:
            parts = full.relative_to(src.root.resolve()).parts
            if parts and parts[0].lower() in SHARED_RESERVED_DIRS:
                raise InvalidPhotoId("reserved folder")
        if full.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise InvalidPhotoId("unsupported file type")
        if not full.is_file():
            raise SourceNotFound("photo no longer exists")
        return full

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------
    def list_photos(self, source: Source) -> List[Dict[str, Any]]:
        """Scans ``source`` and returns browser-safe photo records (no absolute paths)."""
        root = str(source.root.resolve())
        out: List[Dict[str, Any]] = []
        skip = SHARED_RESERVED_DIRS if source.kind == KIND_SHARED else ()
        for p in scan_directory_for_photos(root, skip_top_level=skip):
            rel = os.path.relpath(p["filepath"], root).replace(os.sep, "/")
            out.append({
                "id": encode_photo_id(rel),
                "name": p["filename"],
                "size": p["size_bytes"],
                "mtime": p["modified_time"],
                "category": p["category"],
                "album": p["album"],
                "_abs": p["filepath"],  # internal: stripped before it reaches the client
            })
        return out
