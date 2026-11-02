"""Detailed tests for DPI, ICC profile preservation, and physical calculations."""
import pytest
from PIL import Image, ImageCms
import io

from imaging.dpi import (
    DEFAULT_DPI,
    extract_dpi,
    calculate_physical_dimensions_mm,
    mm_to_pixels,
    check_no_resize_fits,
)
from imaging.processor import process_image


def test_differing_horizontal_vertical_dpi(tmp_path):
    # Non-square pixels (e.g., anamorphic or specialized scanner: 200 x 400 DPI)
    source_img = Image.new("RGB", (1000, 1000), (80, 120, 160))
    source_file = tmp_path / "diff_dpi.jpg"
    source_img.save(source_file, dpi=(200, 400))

    out_file = tmp_path / "diff_dpi_out.jpg"
    result = process_image(
        source_path=str(source_file),
        output_path=str(out_file),
        crop_mode="no_resize",
        paper_width_mm=102.0,
        paper_height_mm=152.0,
    )

    # In no-resize, pixel dimensions should remain exactly (1000, 1000)
    assert result["output_pixels"] == {"width": 1000, "height": 1000}
    assert result["output_dpi"] == {"x": 200, "y": 400}

    # Physical dimensions:
    # w = (1000 / 200) * 25.4 = 127.0 mm
    # h = (1000 / 400) * 25.4 = 63.5 mm
    assert abs(result["calculated_physical_mm"]["width"] - 127.0) < 0.1
    assert abs(result["calculated_physical_mm"]["height"] - 63.5) < 0.1


def test_icc_profile_preservation(tmp_path):
    # Create an sRGB ICC profile using ImageCms
    srgb_profile = ImageCms.createProfile("sRGB")
    icc_bytes = ImageCms.ImageCmsProfile(srgb_profile).tobytes()

    source_img = Image.new("RGB", (600, 400), (255, 128, 64))
    source_file = tmp_path / "with_icc.jpg"
    source_img.save(source_file, icc_profile=icc_bytes)

    out_file = tmp_path / "with_icc_out.jpg"
    result = process_image(
        source_path=str(source_file),
        output_path=str(out_file),
        crop_mode="fill_in",
        paper_width_mm=102.0,
        paper_height_mm=152.0,
    )

    assert result["icc_profile"]["has_profile"] is True

    # Re-open saved output and confirm ICC profile is physically present in output file
    with Image.open(out_file) as saved_img:
        saved_icc = saved_img.info.get("icc_profile")
        assert saved_icc is not None
        assert len(saved_icc) > 0
