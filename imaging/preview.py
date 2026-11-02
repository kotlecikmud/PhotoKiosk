"""Preview generator for Touchscreen GUI.

Guarantees 100% visual consistency between GUI preview and final processed JPEG
by sharing the exact same transformation math.
"""
from typing import Dict, Any, Tuple
from PIL import Image, ImageOps, ImageDraw

from imaging.dpi import (
    DEFAULT_DPI,
    mm_to_pixels,
    get_target_print_pixels,
    get_exif_orientation,
    get_effective_display_dimensions,
    extract_dpi,
    check_no_resize_fits,
)
from imaging.crop_modes import (
    CropMode,
    match_paper_orientation,
    process_fill_in,
    process_fit_in,
    process_border,
)


def generate_preview(
    source_image: Image.Image,
    crop_mode: str,
    paper_width_mm: float,
    paper_height_mm: float,
    border_width_mm: float = 4.0,
    preview_box_size: Tuple[int, int] = (600, 600),
    auto_orientation: bool = True,
) -> Dict[str, Any]:
    """
    Renders an in-memory preview of the final print for the GUI.
    Returns:
    {
        "preview_image": PIL.Image,
        "displayed_physical_mm": (width_mm, height_mm),
        "dpi": (dpi_x, dpi_y),
        "fits_paper": bool,
        "warnings": list[str]
    }
    """
    eff_w, eff_h = get_effective_display_dimensions(source_image)
    orig_w, orig_h = source_image.size
    dpi_x, dpi_y, has_valid = extract_dpi(source_image)

    # Match paper orientation
    final_paper_w_mm, final_paper_h_mm = match_paper_orientation(
        eff_w, eff_h, paper_width_mm, paper_height_mm, auto_match=auto_orientation
    )

    mode = CropMode(crop_mode)
    warnings = []
    if not has_valid:
        warnings.append("Using fallback 300 DPI.")

    # We simulate preview at 150 DPI or scaled box to keep preview generation snappy
    preview_dpi = 150.0
    preview_w_px = mm_to_pixels(final_paper_w_mm, preview_dpi)
    preview_h_px = mm_to_pixels(final_paper_h_mm, preview_dpi)

    if mode == CropMode.NO_RESIZE:
        fits_info = check_no_resize_fits(source_image, final_paper_w_mm, final_paper_h_mm)
        warnings.extend(fits_info["warnings"])
        disp_phys_w, disp_phys_h = fits_info["displayed_physical_mm"]
        fits_paper = fits_info["fits_paper"]

        # For visual preview of No-resize, render paper boundary and show the photo relative to paper
        # Paper dimensions in preview pixels:
        paper_canvas = Image.new("RGB", (preview_w_px, preview_h_px), (240, 240, 240))
        # Draw paper border outline
        draw = ImageDraw.Draw(paper_canvas)
        draw.rectangle([0, 0, preview_w_px - 1, preview_h_px - 1], outline=(180, 180, 180), width=2)

        # Oriented preview of photo
        oriented = ImageOps.exif_transpose(source_image)
        if oriented is None:
            oriented = source_image.copy()
        if oriented.mode not in ("RGB", "L"):
            oriented = oriented.convert("RGB")

        photo_px_w = mm_to_pixels(disp_phys_w, preview_dpi)
        photo_px_h = mm_to_pixels(disp_phys_h, preview_dpi)
        photo_scaled = oriented.resize((photo_px_w, photo_px_h), Image.Resampling.BILINEAR)

        # Center on paper canvas
        off_x = (preview_w_px - photo_px_w) // 2
        off_y = (preview_h_px - photo_px_h) // 2
        paper_canvas.paste(photo_scaled, (off_x, off_y))
        rendered_preview = paper_canvas
    else:
        disp_phys_w, disp_phys_h = final_paper_w_mm, final_paper_h_mm
        fits_paper = True

        oriented = ImageOps.exif_transpose(source_image)
        if oriented is None:
            oriented = source_image.copy()
        if oriented.mode not in ("RGB", "L"):
            oriented = oriented.convert("RGB")

        if mode == CropMode.FILL_IN:
            rendered_preview = process_fill_in(oriented, preview_w_px, preview_h_px)
        elif mode == CropMode.FIT_IN:
            rendered_preview = process_fit_in(oriented, preview_w_px, preview_h_px)
        elif mode == CropMode.BORDER:
            rendered_preview = process_border(
                oriented,
                final_paper_w_mm,
                final_paper_h_mm,
                border_width_mm=border_width_mm,
                dpi=preview_dpi
            )

    # Scale final preview to fit preview_box_size
    box_w, box_h = preview_box_size
    rendered_preview.thumbnail((box_w, box_h), Image.Resampling.BILINEAR)

    return {
        "preview_image": rendered_preview,
        "displayed_physical_mm": (disp_phys_w, disp_phys_h),
        "paper_physical_mm": (final_paper_w_mm, final_paper_h_mm),
        "dpi": (dpi_x, dpi_y),
        "fits_paper": fits_paper,
        "warnings": warnings,
    }
