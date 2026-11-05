"""Media Source & Album Classifier for Smartphones (iPhone & Android) and Storage Media.

Classifies photographs and videos by source and album using:
1. Application naming schemes (e.g. WhatsApp 'WA', Facebook Messenger 'received_', 'FB_IMG_',
   Screenshots, Pixel 'PXL_', Camera timestamps, Instagram, Telegram).
2. Directory / folder hierarchy on device (e.g. DCIM/Camera, WhatsApp/Media, Pictures/Messenger).
3. Custom album folders created by users on Android / iOS / SD cards.
"""
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional

# Canonical category identifiers
CATEGORY_CAMERA = "camera"
CATEGORY_WHATSAPP = "whatsapp"
CATEGORY_MESSENGER = "messenger"
CATEGORY_SCREENSHOTS = "screenshots"
CATEGORY_INSTAGRAM = "instagram"
CATEGORY_TELEGRAM = "telegram"
CATEGORY_DOWNLOADS = "downloads"
CATEGORY_SNAPCHAT = "snapchat"
CATEGORY_TWITTER = "twitter"
CATEGORY_OTHER = "other"

# Human-readable fallback names (English)
CATEGORY_NAMES = {
    CATEGORY_CAMERA: "Camera",
    CATEGORY_WHATSAPP: "WhatsApp",
    CATEGORY_MESSENGER: "Messenger",
    CATEGORY_SCREENSHOTS: "Screenshots",
    CATEGORY_INSTAGRAM: "Instagram",
    CATEGORY_TELEGRAM: "Telegram",
    CATEGORY_DOWNLOADS: "Downloads",
    CATEGORY_SNAPCHAT: "Snapchat",
    CATEGORY_TWITTER: "Twitter",
    CATEGORY_OTHER: "Other",
}

# Generic container folder names that do not represent custom user albums
GENERIC_CONTAINERS = {
    "dcim", "pictures", "photos", "images", "media", "internal storage",
    "internal shared storage", "phone", "storage", "sd card", "karta sd",
    "pamięć wewnętrzna", "wbudowana pamięć", "apple iphone", "android",
    "100apple", "101apple", "102apple", "103apple", "104apple", "105apple",
    "100andro", "100media", "camera", "temp_sessions", "shared", "orders"
}

# Regex patterns for app naming schemes
RE_WHATSAPP_FILENAME = re.compile(
    r"(?:^IMG-\d{8}-WA\d+|^VID-\d{8}-WA\d+|[-_]WA\d+|WA\d{4,}|WhatsApp\s*(?:Image|Video|Animated|Audio)|whatsapp)",
    re.IGNORECASE
)

RE_MESSENGER_FILENAME = re.compile(
    r"(?:^received_\d+|^FB_IMG_\d+|^(?:messenger|facebook)[-_]|\d+_\d+_\d+_[no]\.(?:jpg|jpeg|png)$)",
    re.IGNORECASE
)

RE_SCREENSHOT_FILENAME = re.compile(
    r"(?:^(?:Screenshot|Screen_Shot|ScreenShot|Screen-Shot|Screencap)[-_]|screenshot|zrzut[ _-]?ekranu|^Capture_|^Screen_)",
    re.IGNORECASE
)

RE_INSTAGRAM_FILENAME = re.compile(
    r"(?:^Instagram[-_]|^IG[-_]|_ig_|^instagram)",
    re.IGNORECASE
)

RE_TELEGRAM_FILENAME = re.compile(
    r"(?:^Telegram[-_]|^photo_\d+@|^photo_\d{4}-\d{2}-\d{2}|^telegram)",
    re.IGNORECASE
)

RE_DOWNLOADS_FILENAME = re.compile(
    r"(?:^(?:download|pobrane)[-_]|^save_)",
    re.IGNORECASE
)

RE_SNAPCHAT_FILENAME = re.compile(
    r"(?:^Snapchat[-_]|snapchat)",
    re.IGNORECASE
)

RE_TWITTER_FILENAME = re.compile(
    r"(?:^Twitter[-_]|twitter)",
    re.IGNORECASE
)

# Standard Camera Filename Patterns
RE_CAMERA_FILENAME = re.compile(
    r"(?:^IMG_\d+|^PXL_\d+|^DSC[NF0]?[-_]?\d+|^SAM[-_]?\d+|^MVIMG_\d+|^(?:PHOTO|PICT)[-_]?\d+|^\d{8}[-_]\d{6})",
    re.IGNORECASE
)


def extract_custom_album_name(relative_path: str) -> Optional[str]:
    """
    Extracts custom album name from parent folder if it's not a generic system folder.
    e.g. 'Pictures/Wakacje 2024/img.jpg' -> 'Wakacje 2024'
    """
    if not relative_path:
        return None

    # Normalize slashes
    clean_path = relative_path.replace("/", "\\")
    parts = [p.strip() for p in clean_path.split("\\") if p.strip()]

    # We want the immediate parent folder of the file (or second to last if file is at end)
    # Check if last item is a file with extension
    if len(parts) >= 2 and "." in parts[-1]:
        parent = parts[-2]
    elif len(parts) >= 1 and "." not in parts[-1]:
        parent = parts[-1]
    else:
        return None

    if parent.lower() not in GENERIC_CONTAINERS and not parent.startswith("."):
        # Format album name nicely
        return parent

    return None


def classify_media(filename: str, relative_path: str = "") -> Dict[str, Any]:
    """
    Classifies an image or video by app source and album structure.

    Args:
        filename: Base name of the file (e.g. 'IMG-20230815-WA0004.jpg', 'IMG_1234.HEIC').
        relative_path: Full or relative path on device or filesystem.

    Returns:
        Dict with keys:
            - 'category': Canonical string identifier (e.g. 'whatsapp', 'camera', 'messenger').
            - 'category_name': Human-readable English name (e.g. 'WhatsApp', 'Camera').
            - 'album': Album name (category name or specific custom user album name).
            - 'is_screenshot': Boolean flag if classified as a screenshot.
    """
    fname = (filename or "").strip()
    ext = os.path.splitext(fname)[1].lower()
    path_lower = (relative_path or "").lower().replace("/", "\\")

    # 1. WhatsApp check (Filenames & Path)
    if RE_WHATSAPP_FILENAME.search(fname) or "whatsapp" in path_lower or "com.whatsapp" in path_lower:
        return {
            "category": CATEGORY_WHATSAPP,
            "category_name": CATEGORY_NAMES[CATEGORY_WHATSAPP],
            "album": CATEGORY_NAMES[CATEGORY_WHATSAPP],
            "is_screenshot": False,
        }

    # 2. Messenger / Facebook check
    if RE_MESSENGER_FILENAME.search(fname) or "messenger" in path_lower or "com.facebook.orca" in path_lower or "pictures\\facebook" in path_lower:
        return {
            "category": CATEGORY_MESSENGER,
            "category_name": CATEGORY_NAMES[CATEGORY_MESSENGER],
            "album": CATEGORY_NAMES[CATEGORY_MESSENGER],
            "is_screenshot": False,
        }

    # 3. Screenshot check
    # Note: On iPhone, camera photos are ALWAYS JPEG, HEIF, or DNG, never PNG.
    # An 'IMG_xxxx.png' in an iPhone DCIM/xxxAPPLE folder is a screenshot!
    is_iphone_png_screenshot = (
        ext == ".png" and
        fname.upper().startswith("IMG_") and
        ("apple" in path_lower or "dcim" in path_lower or not path_lower)
    )
    if (
        RE_SCREENSHOT_FILENAME.search(fname) or
        "screenshot" in path_lower or
        "zrzuty ekranu" in path_lower or
        "screencapture" in path_lower or
        is_iphone_png_screenshot
    ):
        return {
            "category": CATEGORY_SCREENSHOTS,
            "category_name": CATEGORY_NAMES[CATEGORY_SCREENSHOTS],
            "album": CATEGORY_NAMES[CATEGORY_SCREENSHOTS],
            "is_screenshot": True,
        }

    # 4. Instagram check
    if RE_INSTAGRAM_FILENAME.search(fname) or "instagram" in path_lower:
        return {
            "category": CATEGORY_INSTAGRAM,
            "category_name": CATEGORY_NAMES[CATEGORY_INSTAGRAM],
            "album": CATEGORY_NAMES[CATEGORY_INSTAGRAM],
            "is_screenshot": False,
        }

    # 5. Telegram check
    if RE_TELEGRAM_FILENAME.search(fname) or "telegram" in path_lower:
        return {
            "category": CATEGORY_TELEGRAM,
            "category_name": CATEGORY_NAMES[CATEGORY_TELEGRAM],
            "album": CATEGORY_NAMES[CATEGORY_TELEGRAM],
            "is_screenshot": False,
        }

    # 6. Downloads check
    if RE_DOWNLOADS_FILENAME.search(fname) or "download" in path_lower or "pobrane" in path_lower:
        return {
            "category": CATEGORY_DOWNLOADS,
            "category_name": CATEGORY_NAMES[CATEGORY_DOWNLOADS],
            "album": CATEGORY_NAMES[CATEGORY_DOWNLOADS],
            "is_screenshot": False,
        }

    # 7. Snapchat check
    if RE_SNAPCHAT_FILENAME.search(fname) or "snapchat" in path_lower:
        return {
            "category": CATEGORY_SNAPCHAT,
            "category_name": CATEGORY_NAMES[CATEGORY_SNAPCHAT],
            "album": CATEGORY_NAMES[CATEGORY_SNAPCHAT],
            "is_screenshot": False,
        }

    # 8. Twitter / X check
    if RE_TWITTER_FILENAME.search(fname) or "twitter" in path_lower or "\\pictures\\x\\" in path_lower:
        return {
            "category": CATEGORY_TWITTER,
            "category_name": CATEGORY_NAMES[CATEGORY_TWITTER],
            "album": CATEGORY_NAMES[CATEGORY_TWITTER],
            "is_screenshot": False,
        }

    # 9. Camera check (Filenames & Standard Camera Paths)
    is_camera_file = bool(RE_CAMERA_FILENAME.search(fname))
    is_camera_path = (
        "\\dcim\\camera" in path_lower or
        "\\camera" in path_lower or
        "100apple" in path_lower or
        "101apple" in path_lower or
        "102apple" in path_lower or
        "100andro" in path_lower or
        "100media" in path_lower
    )
    if is_camera_file or is_camera_path:
        # Check if it is inside a custom user sub-album under DCIM or Pictures
        custom_album = extract_custom_album_name(relative_path)
        return {
            "category": CATEGORY_CAMERA,
            "category_name": CATEGORY_NAMES[CATEGORY_CAMERA],
            "album": custom_album or CATEGORY_NAMES[CATEGORY_CAMERA],
            "is_screenshot": False,
        }

    # 10. Custom user album from path if not generic
    custom_album = extract_custom_album_name(relative_path)
    if custom_album:
        return {
            "category": CATEGORY_OTHER,
            "category_name": custom_album,
            "album": custom_album,
            "is_screenshot": False,
        }

    # 11. Fallback to Other
    return {
        "category": CATEGORY_OTHER,
        "category_name": CATEGORY_NAMES[CATEGORY_OTHER],
        "album": CATEGORY_NAMES[CATEGORY_OTHER],
        "is_screenshot": False,
    }
