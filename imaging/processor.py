"""Master image processing pipeline.

Loads photographs, applies correct transformations per crop mode,
preserves ICC profiles, embeds DPI metadata, and writes output files.
"""
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from PIL import Image, ImageOps
import io

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
from imaging.color import extract_icc_profile, apply_color_management, get_icc_profile_info
from imaging.crop_modes import (
    CropMode,
    match_paper_orientation,
    process_fill_in,
    process_fit_in,
    process_border,
    process_no_resize,
)


def process_image(
    source_path: str,
    output_path: str,
    crop_mode: str,
    paper_width_mm: float,
    paper_height_mm: float,
    border_width_mm: float = 4.0,
    dpi: float = DEFAULT_DPI,
    auto_orientation: bool = True,
    target_icc_path: Optional[str] = None,
    jpeg_quality: int = 98,
) -> Dict[str, Any]:
    """
    Executes the full image processing pipeline for an order item.
    Guarantees:
    - Original file is never modified (read-only).
    - EXIF transpose applied ONLY to Fill-in, Fit-in, Border.
    - No-resize preserves exact original pixel dimensions and EXIF tags.
    - ICC profile preserved and embedded in output.
    - DPI metadata embedded in output.
    - Output directories created automatically.
    - Returns detailed manifest entry for order.json.
    """
    source_p = Path(source_path)
    output_p = Path(output_path)
    output_p.parent.mkdir(parents=True, exist_ok=True)

    mode_enum = CropMode(crop_mode)

    with Image.open(source_p) as raw_img:
        orig_pixel_w, orig_pixel_h = raw_img.size
        orig_dpi_x, orig_dpi_y, has_valid_dpi = extract_dpi(raw_img)
        source_icc = extract_icc_profile(raw_img)
        icc_info = get_icc_profile_info(source_icc)
        exif_val = get_exif_orientation(raw_img)
        eff_w, eff_h = get_effective_display_dimensions(raw_img)

        warnings = []
        if not has_valid_dpi:
            warnings.append(f"Source file had missing or invalid DPI. Fallback to {DEFAULT_DPI:.0f} DPI used.")

        # Match paper orientation to effective photo orientation (unless No-resize)
        final_paper_w_mm, final_paper_h_mm = match_paper_orientation(
            eff_w, eff_h, paper_width_mm, paper_height_mm, auto_match=auto_orientation
        )

        target_w_px = mm_to_pixels(final_paper_w_mm, dpi)
        target_h_px = mm_to_pixels(final_paper_h_mm, dpi)

        if mode_enum == CropMode.NO_RESIZE:
            # NO-RESIZE: Strictly preserve original pixels and orientation
            processed_img, out_dpi = process_no_resize(raw_img)
            fits_info = check_no_resize_fits(raw_img, final_paper_w_mm, final_paper_h_mm)
            warnings.extend(fits_info["warnings"])
            calc_mm_w, calc_mm_h = fits_info["displayed_physical_mm"]
            out_dpi_tuple = (int(round(out_dpi[0])), int(round(out_dpi[1])))
        else:
            # Resized modes: Apply EXIF orientation first
            oriented_img = ImageOps.exif_transpose(raw_img)
            if oriented_img is None:
                oriented_img = raw_img.copy()

            if oriented_img.mode not in ("RGB", "L"):
                oriented_img = oriented_img.convert("RGB")

            # Stage A/B color management
            oriented_img = apply_color_management(oriented_img, source_icc, target_icc_path)

            if mode_enum == CropMode.FILL_IN:
                processed_img = process_fill_in(oriented_img, target_w_px, target_h_px)
            elif mode_enum == CropMode.FIT_IN:
                processed_img = process_fit_in(oriented_img, target_w_px, target_h_px)
            elif mode_enum == CropMode.BORDER:
                processed_img = process_border(
                    oriented_img,
                    final_paper_w_mm,
                    final_paper_h_mm,
                    border_width_mm=border_width_mm,
                    dpi=dpi
                )
            else:
                raise ValueError(f"Unknown crop mode: {crop_mode}")

            calc_mm_w, calc_mm_h = final_paper_w_mm, final_paper_h_mm
            out_dpi_tuple = (int(round(dpi)), int(round(dpi)))

        out_w_px, out_h_px = processed_img.size

        # Prepare save parameters (preserve ICC profile and embed output DPI)
        save_kwargs: Dict[str, Any] = {
            "dpi": out_dpi_tuple,
            "quality": jpeg_quality,
            "subsampling": 0,  # 4:4:4 high quality chroma
        }
        if source_icc:
            save_kwargs["icc_profile"] = source_icc

        # Ensure RGB mode for JPEG saving
        if processed_img.mode not in ("RGB", "L"):
            processed_img = processed_img.convert("RGB")

        # Save to destination
        processed_img.save(str(output_p), format="JPEG", **save_kwargs)

    return {
        "source_filename": source_p.name,
        "source_path": str(source_p),
        "output_filename": output_p.name,
        "output_path": str(output_p),
        "crop_mode": crop_mode,
        "original_pixels": {"width": orig_pixel_w, "height": orig_pixel_h},
        "original_dpi": {"x": orig_dpi_x, "y": orig_dpi_y, "is_valid": has_valid_dpi},
        "exif_orientation": exif_val,
        "icc_profile": icc_info,
        "paper_size_configured_mm": {"width": paper_width_mm, "height": paper_height_mm},
        "paper_size_applied_mm": {"width": final_paper_w_mm, "height": final_paper_h_mm},
        "output_pixels": {"width": out_w_px, "height": out_h_px},
        "output_dpi": {"x": out_dpi_tuple[0], "y": out_dpi_tuple[1]},
        "calculated_physical_mm": {"width": calc_mm_w, "height": calc_mm_h},
        "warnings": warnings,
    }
