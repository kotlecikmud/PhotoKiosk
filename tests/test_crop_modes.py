"""Comprehensive tests for imaging and DPI calculations."""
import pytest
from pathlib import Path
from PIL import Image, ImageOps, ExifTags
import io

from imaging.dpi import (
    DEFAULT_DPI,
    mm_to_pixels,
    extract_dpi,
    calculate_physical_dimensions_mm,
    get_target_print_pixels,
    get_exif_orientation,
    is_orientation_transposed,
    get_effective_display_dimensions,
    check_no_resize_fits,
)
from imaging.crop_modes import (
    process_fill_in,
    process_fit_in,
    process_border,
    process_no_resize,
    match_paper_orientation,
)
from imaging.processor import process_image
from imaging.color import extract_icc_profile, get_icc_profile_info


def create_test_image(
    width: int,
    height: int,
    color=(100, 150, 200),
    dpi=None,
    orientation=None,
    icc_profile=None
) -> Image.Image:
    """Helper to generate PIL images with specific metadata."""
    img = Image.new("RGB", (width, height), color)
    info = {}
    if dpi:
        info["dpi"] = dpi
    if icc_profile:
        info["icc_profile"] = icc_profile

    # Apply EXIF if orientation provided
    if orientation:
        exif = img.getexif()
        # Find orientation tag id
        tag_id = 0x0112  # Standard Orientation tag ID
        exif[tag_id] = orientation
        img._exif = exif

    img.info.update(info)
    return img


def test_mm_to_pixels():
    # 102 mm at 300 DPI: (102 / 25.4) * 300 = 1204.72 -> 1205 px
    px = mm_to_pixels(102.0, 300.0)
    assert px == 1205

    # 152 mm at 300 DPI: (152 / 25.4) * 300 = 1795.27 -> 1795 px
    px2 = mm_to_pixels(152.0, 300.0)
    assert px2 == 1795

    # 4 mm border at 300 DPI: (4 / 25.4) * 300 = 47.24 -> 47 px
    border_px = mm_to_pixels(4.0, 300.0)
    assert border_px == 47


def test_extract_dpi():
    # Default fallback when missing
    img = Image.new("RGB", (100, 100))
    dx, dy, is_valid = extract_dpi(img)
    assert dx == 300.0
    assert dy == 300.0
    assert is_valid is False

    # Standard square DPI
    img.info["dpi"] = (600, 600)
    dx, dy, is_valid = extract_dpi(img)
    assert dx == 600.0
    assert dy == 600.0
    assert is_valid is True

    # Differing horizontal and vertical DPI
    img.info["dpi"] = (300, 600)
    dx, dy, is_valid = extract_dpi(img)
    assert dx == 300.0
    assert dy == 600.0
    assert is_valid is True


def test_calculate_physical_dimensions():
    # 3000 x 4500 px at 300 DPI:
    # 3000 / 300 * 25.4 = 254.0 mm
    # 4500 / 300 * 25.4 = 381.0 mm
    w_mm, h_mm = calculate_physical_dimensions_mm(3000, 4500, 300, 300)
    assert abs(w_mm - 254.0) < 0.1
    assert abs(h_mm - 381.0) < 0.1


def test_match_paper_orientation():
    # Landscape photo on portrait 10x15 paper -> auto matches to 15x10
    pw, ph = match_paper_orientation(1800, 1200, 102.0, 152.0, auto_match=True)
    assert pw == 152.0
    assert ph == 102.0

    # Portrait photo on portrait 10x15 paper -> stays 10x15
    pw2, ph2 = match_paper_orientation(1200, 1800, 102.0, 152.0, auto_match=True)
    assert pw2 == 102.0
    assert ph2 == 152.0

    # Auto match disabled
    pw3, ph3 = match_paper_orientation(1800, 1200, 102.0, 152.0, auto_match=False)
    assert pw3 == 102.0
    assert ph3 == 152.0


def test_fill_in_crop():
    # Source: 1000 x 1000 square
    # Target: 1000 x 500 (landscape 2:1)
    img = Image.new("RGB", (1000, 1000), (255, 0, 0))
    res = process_fill_in(img, 1000, 500)
    assert res.size == (1000, 500)


def test_fit_in():
    # Source: 1000 x 500 (2:1)
    # Target: 1000 x 1000 square paper
    img = Image.new("RGB", (1000, 500), (0, 0, 255))
    res = process_fit_in(img, 1000, 1000)
    assert res.size == (1000, 1000)
    # Top and bottom bars should be pure white (255, 255, 255)
    assert res.getpixel((500, 50)) == (255, 255, 255)
    # Center should be original blue (0, 0, 255)
    assert res.getpixel((500, 500)) == (0, 0, 255)


def test_border_4mm():
    img = Image.new("RGB", (1000, 500), (0, 255, 0))
    res = process_border(img, 102.0, 152.0, border_width_mm=4.0, dpi=300.0)
    target_w = mm_to_pixels(102.0, 300.0)
    target_h = mm_to_pixels(152.0, 300.0)
    assert res.size == (target_w, target_h)
    # Corners and border edges should be pure white
    assert res.getpixel((2, 2)) == (255, 255, 255)


def test_no_resize_strictly_preserves_pixels():
    img = Image.new("RGB", (1234, 567), (12, 34, 56))
    img.info["dpi"] = (150, 150)
    processed, dpi = process_no_resize(img)
    assert processed.size == (1234, 567)
    assert dpi == (150.0, 150.0)


def test_exif_orientations_1_to_8():
    for orientation in range(1, 9):
        img = create_test_image(800, 600, orientation=orientation)
        detected = get_exif_orientation(img)
        assert detected == orientation
        is_trans = is_orientation_transposed(detected)
        if orientation in (5, 6, 7, 8):
            assert is_trans is True
            eff_w, eff_h = get_effective_display_dimensions(img)
            assert (eff_w, eff_h) == (600, 800)
        else:
            assert is_trans is False
            eff_w, eff_h = get_effective_display_dimensions(img)
            assert (eff_w, eff_h) == (800, 600)
