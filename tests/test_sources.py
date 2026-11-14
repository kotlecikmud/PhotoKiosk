"""Safety tests for opaque photo ids: a crafted id must never reach a file outside its source."""
import pytest
from PIL import Image

from backend.sources import (
    InvalidPhotoId,
    SourceNotFound,
    SourceRegistry,
    decode_photo_id,
    encode_photo_id,
)


@pytest.fixture
def registry(tmp_path):
    reg = SourceRegistry(shared_dir=str(tmp_path / "shared"), temp_base=str(tmp_path / "tmp"))
    root = tmp_path / "shared"
    root.mkdir()
    (root / "sub").mkdir()
    Image.new("RGB", (30, 20)).save(root / "a.jpg")
    Image.new("RGB", (30, 20)).save(root / "sub" / "b.png")
    (root / "notes.txt").write_text("secret")
    (tmp_path / "outside.jpg").write_bytes(b"x")
    return reg


def test_id_roundtrip_unicode():
    rel = "DCIM/Zdjęcia 2026/IMG ąę.jpg"
    assert decode_photo_id(encode_photo_id(rel)) == rel


def test_resolves_files_inside_root(registry, tmp_path):
    assert registry.resolve_photo("shared", encode_photo_id("a.jpg")) == (tmp_path / "shared" / "a.jpg").resolve()
    assert registry.resolve_photo("shared", encode_photo_id("sub/b.png")).name == "b.png"


@pytest.mark.parametrize("evil", [
    "../outside.jpg", "sub/../../outside.jpg", "/etc/passwd", "C:\\Windows\\win.ini",
    "..\\outside.jpg", "a.jpg\x00.txt", "",
])
def test_rejects_traversal_and_absolute(registry, evil):
    with pytest.raises(InvalidPhotoId):
        registry.resolve_photo("shared", encode_photo_id(evil))


def test_rejects_non_images_and_missing(registry):
    with pytest.raises(InvalidPhotoId):
        registry.resolve_photo("shared", encode_photo_id("notes.txt"))
    with pytest.raises(SourceNotFound):
        registry.resolve_photo("shared", encode_photo_id("gone.jpg"))


def test_rejects_symlink_escape(registry, tmp_path):
    link = tmp_path / "shared" / "link.jpg"
    try:
        link.symlink_to(tmp_path / "outside.jpg")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(InvalidPhotoId):
        registry.resolve_photo("shared", encode_photo_id("link.jpg"))


def test_garbage_ids_and_unknown_source(registry):
    for bad in ("!!!", "", "a" * 3000):
        with pytest.raises(InvalidPhotoId):
            decode_photo_id(bad)
    with pytest.raises(SourceNotFound):
        registry.get("usb-doesnotexist")


def test_list_photos_hides_absolute_paths_by_default_fields(registry):
    photos = registry.list_photos(registry.get("shared"))
    assert {p["name"] for p in photos} == {"a.jpg", "b.png"}
    assert all(decode_photo_id(p["id"]) in ("a.jpg", "sub/b.png") for p in photos)


def test_shared_orders_folder_of_previous_customers_is_hidden(registry, tmp_path):
    orders = tmp_path / "shared" / "orders" / "ORD-1"
    orders.mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(orders / "someone_elses.jpg")
    listed = {p["name"] for p in registry.list_photos(registry.get("shared"))}
    assert "someone_elses.jpg" not in listed and {"a.jpg", "b.png"} <= listed
    with pytest.raises(InvalidPhotoId):
        registry.resolve_photo("shared", encode_photo_id("orders/ORD-1/someone_elses.jpg"))
    with pytest.raises(InvalidPhotoId):
        registry.resolve_photo("shared", encode_photo_id("sub/../Orders/ORD-1/someone_elses.jpg"))
