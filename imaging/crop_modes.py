"""Crop modes implementation: Fill-in, Fit-in, Border (4mm), No-resize.

Reference: crop_modes.jpg specification.
Standard output resolution: 300 DPI.
"""
from typing import Tuple, Optional, Dict, Any
from enum import Enum
from PIL import Image, ImageOps

from imaging.dpi import (
    DEFAULT_DPI,
    mm_to_pixels,
    get_target_print_pixels,
    get_exif_orientation,
    is_orientation_transposed,
    get_effective_display_dimensions,
    extract_dpi,
    calculate_physical_dimensions_mm,
    check_no_resize_fits,
)
from imaging.color import extract_icc_profile, apply_color_management


class CropMode(str, Enum):
    FILL_IN = "fill_in"
    FIT_IN = "fit_in"
    BORDER = "border"
    NO_RESIZE = "no_resize"


def match_paper_orientation(
    effective_img_w: int,
    effective_img_h: int,
    paper_w_mm: float,
    paper_h_mm: float,
    auto_match: bool = True
) -> Tuple[float, float]:
    """
    Adjusts paper dimensions (orientation) to match image orientation.
    If image is landscape (w >= h) and paper is portrait (pw < ph), swaps paper w & h.
    If image is portrait (w < h) and paper is landscape (pw > ph), swaps paper w & h.
    """
    if not auto_match:
        return paper_w_mm, paper_h_mm

    img_is_landscape = effective_img_w >= effective_img_h
    paper_is_landscape = paper_w_mm >= paper_h_mm

    if img_is_landscape != paper_is_landscape:
        return paper_h_mm, paper_w_mm

    return paper_w_mm, paper_h_mm


def process_fill_in(
    image: Image.Image,
    target_w_px: int,
    target_h_px: int,
) -> Image.Image:
    """
    8.1 Fill-in:
    - Fill the entire selected paper format.
    - Preserve original image proportions.
    - Crop excess image content centered.
    - Output dimensions correspond exactly to target_w_px × target_h_px.
    """
    # Image is already orientation-normalized before calling this function
    src_w, src_h = image.size
    target_ratio = target_w_px / target_h_px
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # Source is wider than target: crop left & right
        crop_w = int(round(src_h * target_ratio))
        offset_x = (src_w - crop_w) // 2
        box = (offset_x, 0, offset_x + crop_w, src_h)
    else:
        # Source is taller than target: crop top & bottom
        crop_h = int(round(src_w / target_ratio))
        offset_y = (src_h - crop_h) // 2
        box = (0, offset_y, src_w, offset_y + crop_h)

    cropped = image.crop(box)
    resized = cropped.resize((target_w_px, target_h_px), Image.Resampling.LANCZOS)
    return resized


def process_fit_in(
    image: Image.Image,
    target_w_px: int,
    target_h_px: int,
    bg_color: Tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """
    8.2 Fit-in:
    - Preserve complete photograph.
    - Scale proportionally to fit within paper dimensions.
    - Do not crop image content.
    - Fill unused space with pure white.
    - Output dimensions correspond exactly to paper size at 300 DPI.
    """
    src_w, src_h = image.size
    ratio = min(target_w_px / src_w, target_h_px / src_h)
    new_w = max(1, int(round(src_w * ratio)))
    new_h = max(1, int(round(src_h * ratio)))

    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_w_px, target_h_px), bg_color)
    offset_x = (target_w_px - new_w) // 2
    offset_y = (target_h_px - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas


def process_border(
    image: Image.Image,
    paper_w_mm: float,
    paper_h_mm: float,
    border_width_mm: float = 4.0,
    dpi: float = DEFAULT_DPI,
    bg_color: Tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """
    8.3 Border (4 mm):
    - Create a white border measuring exactly 4 mm on each side.
    - Fit photograph into remaining printable area proportionally.
    - Center the image.
    - Output dimensions correspond exactly to selected paper size at 300 DPI.
    """
    target_w_px = mm_to_pixels(paper_w_mm, dpi)
    target_h_px = mm_to_pixels(paper_h_mm, dpi)

    border_x_px = mm_to_pixels(border_width_mm, dpi)
    border_y_px = mm_to_pixels(border_width_mm, dpi)

    inner_w_px = max(1, target_w_px - (2 * border_x_px))
    inner_h_px = max(1, target_h_px - (2 * border_y_px))

    src_w, src_h = image.size
    ratio = min(inner_w_px / src_w, inner_h_px / src_h)
    new_w = max(1, int(round(src_w * ratio)))
    new_h = max(1, int(round(src_h * ratio)))

    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (target_w_px, target_h_px), bg_color)
    offset_x = (target_w_px - new_w) // 2
    offset_y = (target_h_px - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas


def process_no_resize(
    source_image: Image.Image
) -> Tuple[Image.Image, Tuple[float, float]]:
    """
    8.4 No-resize:
    - Must NOT be resized or cropped.
    - Preserve original pixel dimensions and original pixel data.
    - Do NOT apply exif_transpose to the pixel data.
    - Use embedded horizontal and vertical DPI metadata when available (fallback 300).
    - Returns (image_copy, (dpi_x, dpi_y)).
    """
    dpi_x, dpi_y, _ = extract_dpi(source_image)
    # Ensure mode is RGB/RGBA/etc. without resizing
    if source_image.mode not in ("RGB", "L"):
        img = source_image.convert("RGB")
    else:
        img = source_image.copy()
    return img, (dpi_x, dpi_y)
