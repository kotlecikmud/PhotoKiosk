"""DPI calculations, metadata extraction, and physical dimension helpers."""
from typing import Tuple, Optional, Dict, Any
from PIL import Image, ExifTags

DEFAULT_DPI: float = 300.0
MM_PER_INCH: float = 25.4

# Map EXIF orientation tag key
ORIENTATION_TAG: Optional[int] = None
for k, v in getattr(ExifTags, 'TAGS', {}).items():
    if v == 'Orientation':
        ORIENTATION_TAG = k
        break


def get_exif_orientation(image: Image.Image) -> int:
    """Return EXIF orientation value (1-8), defaulting to 1 (normal)."""
    try:
        exif = image.getexif()
        if exif and ORIENTATION_TAG in exif:
            val = exif[ORIENTATION_TAG]
            if isinstance(val, int) and 1 <= val <= 8:
                return val
    except Exception:
        pass
    return 1


def is_orientation_transposed(orientation: int) -> bool:
    """Returns True if orientation swaps width and height (5, 6, 7, 8)."""
    return orientation in (5, 6, 7, 8)


def get_effective_display_dimensions(image: Image.Image) -> Tuple[int, int]:
    """
    Returns (effective_width, effective_height) taking EXIF orientation into account.
    Does not modify or resample the underlying image.
    """
    w, h = image.size
    orientation = get_exif_orientation(image)
    if is_orientation_transposed(orientation):
        return h, w
    return w, h


def extract_dpi(image: Image.Image) -> Tuple[float, float, bool]:
    """
    Extracts horizontal and vertical DPI from image metadata.
    Returns:
        (dpi_x, dpi_y, is_valid_dpi)
        If DPI is missing or <= 0, returns (DEFAULT_DPI, DEFAULT_DPI, False).
    """
    dpi_raw = image.info.get('dpi')
    if dpi_raw is not None:
        try:
            if isinstance(dpi_raw, (tuple, list)) and len(dpi_raw) >= 2:
                dx = float(dpi_raw[0])
                dy = float(dpi_raw[1])
                if dx > 0 and dy > 0:
                    return dx, dy, True
            elif isinstance(dpi_raw, (int, float)):
                d = float(dpi_raw)
                if d > 0:
                    return d, d, True
        except (ValueError, TypeError):
            pass

    # Check EXIF Resolution tags if present
    try:
        exif = image.getexif()
        if exif:
            # 282 is XResolution, 283 is YResolution
            x_res = exif.get(282)
            y_res = exif.get(283)
            if x_res and y_res:
                dx = float(x_res)
                dy = float(y_res)
                if dx > 0 and dy > 0:
                    return dx, dy, True
    except Exception:
        pass

    return DEFAULT_DPI, DEFAULT_DPI, False


def calculate_physical_dimensions_mm(
    pixel_width: int,
    pixel_height: int,
    dpi_x: float,
    dpi_y: float
) -> Tuple[float, float]:
    """
    Calculates physical dimensions in millimeters from pixel dimensions and DPI.
    width_mm = (pixel_width / dpi_x) * 25.4
    height_mm = (pixel_height / dpi_y) * 25.4
    """
    width_mm = (float(pixel_width) / float(dpi_x)) * MM_PER_INCH
    height_mm = (float(pixel_height) / float(dpi_y)) * MM_PER_INCH
    return round(width_mm, 2), round(height_mm, 2)


def mm_to_pixels(mm: float, dpi: float = DEFAULT_DPI) -> int:
    """Converts millimeters to exact pixel dimension at specified DPI."""
    return int(round((float(mm) / MM_PER_INCH) * float(dpi)))


def get_target_print_pixels(
    paper_width_mm: float,
    paper_height_mm: float,
    dpi: float = DEFAULT_DPI
) -> Tuple[int, int]:
    """Returns exact pixel dimensions (target_width_px, target_height_px) for paper size at DPI."""
    return mm_to_pixels(paper_width_mm, dpi), mm_to_pixels(paper_height_mm, dpi)


def check_no_resize_fits(
    image: Image.Image,
    paper_width_mm: float,
    paper_height_mm: float
) -> Dict[str, Any]:
    """
    Analyzes physical size of an image in No-resize mode against paper size.
    Takes EXIF orientation into account for displayed physical dimensions,
    while leaving original pixels unchanged.
    """
    orig_w, orig_h = image.size
    orientation = get_exif_orientation(image)
    dpi_x, dpi_y, is_valid_dpi = extract_dpi(image)

    # Calculate raw physical dimensions
    raw_phys_w, raw_phys_h = calculate_physical_dimensions_mm(orig_w, orig_h, dpi_x, dpi_y)

    # Displayed physical dimensions swap if transposed
    if is_orientation_transposed(orientation):
        disp_phys_w, disp_phys_h = raw_phys_h, raw_phys_w
    else:
        disp_phys_w, disp_phys_h = raw_phys_w, raw_phys_h

    # Check against both standard and rotated paper orientations
    fits_standard = disp_phys_w <= paper_width_mm and disp_phys_h <= paper_height_mm
    fits_rotated = disp_phys_w <= paper_height_mm and disp_phys_h <= paper_width_mm
    fits = fits_standard or fits_rotated

    warnings = []
    if not is_valid_dpi:
        warnings.append(f"Image lacks valid embedded DPI metadata. Fallback to {DEFAULT_DPI:.0f} DPI used.")
    if not fits:
        warnings.append(
            f"Physical dimensions ({disp_phys_w} × {disp_phys_h} mm) exceed selected paper size "
            f"({paper_width_mm} × {paper_height_mm} mm)."
        )

    return {
        "original_pixels": (orig_w, orig_h),
        "exif_orientation": orientation,
        "dpi": (dpi_x, dpi_y),
        "is_valid_dpi": is_valid_dpi,
        "displayed_physical_mm": (disp_phys_w, disp_phys_h),
        "paper_mm": (paper_width_mm, paper_height_mm),
        "fits_paper": fits,
        "warnings": warnings,
    }
