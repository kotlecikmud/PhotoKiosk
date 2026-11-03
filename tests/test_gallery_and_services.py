"""Unit and integration tests for gallery refactor, services.json price binding, and admin paper settings."""
import os
import json
import pytest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient

from backend.app import app
from backend.database import (
    init_database,
    set_db_path,
    get_db_engine,
    PaperFinish,
    PrintSize,
    PrintServicePrice,
    get_service_price,
    get_all_service_prices_map,
)
from devices.session_manager import get_temp_session_dir, clean_temp_session_dir
from gui.screens.gallery import GalleryScreen
from gui.screens.order_summary import OrderSummaryScreen
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_refactor.db"
    init_database(str(db_file), initial_admin_password="AdminTestPassword123!")
    engine = get_db_engine(str(db_file))
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def test_services_json_parsing_and_price_binding(test_db, tmp_path):
    """Verifies standard ODBITK_ items are correctly parsed, bound, and exclude slides."""
    client = TestClient(app)

    # 1. Login to admin
    login_res = client.post("/admin/login", data={"password": "AdminTestPassword123!"}, follow_redirects=False)
    assert login_res.status_code == 303
    cookie = login_res.cookies.get("admin_session")

    # 2. Upload services.json
    with open("services.json", "rb") as f:
        res = client.post(
            "/admin/services/import",
            files={"services_file": ("services.json", f, "application/json")},
            cookies={"admin_session": cookie},
            follow_redirects=True,
        )
    assert res.status_code == 200

    # 3. Verify prices stored in database
    p_9x13_g = get_service_price("9x13", "glossy", db=test_db)
    assert p_9x13_g == 1.0

    p_10x15_m = get_service_price("10x15", "matte", db=test_db)
    assert p_10x15_m == 1.1

    p_9x13_silk = get_service_price("9x13", "silk", db=test_db)
    assert p_9x13_silk == 1.4

    # Verify slide prints were NOT stored
    slide_items = test_db.query(PrintServicePrice).filter(PrintServicePrice.name.contains("(ze slajdów)")).all()
    assert len(slide_items) == 0

    # Total standard prints stored should be 90
    total_prices = test_db.query(PrintServicePrice).count()
    assert total_prices == 90


def test_admin_paper_finishes_management(test_db):
    """Verifies adding, toggling, and deleting paper finishes in the admin panel."""
    client = TestClient(app)
    login_res = client.post("/admin/login", data={"password": "AdminTestPassword123!"}, follow_redirects=False)
    cookie = login_res.cookies.get("admin_session")

    # Add SILK finish
    res = client.post("/admin/paper-finishes", data={"name": "SILK"}, cookies={"admin_session": cookie}, follow_redirects=True)
    assert res.status_code == 200
    silk = test_db.query(PaperFinish).filter_by(name="SILK").first()
    assert silk is not None
    assert silk.is_active is True

    # Toggle SILK
    res = client.post(f"/admin/paper-finishes/{silk.id}/toggle", cookies={"admin_session": cookie}, follow_redirects=True)
    assert res.status_code == 200
    test_db.refresh(silk)
    assert silk.is_active is False

    # Delete SILK
    res = client.post(f"/admin/paper-finishes/{silk.id}/delete", cookies={"admin_session": cookie}, follow_redirects=True)
    assert res.status_code == 200
    assert test_db.query(PaperFinish).filter_by(name="SILK").first() is None


def test_gallery_screen_split_and_card_behavior(tmp_path):
    """Verifies split-screen gallery card additions, removals, auto-selection, and cleanup."""
    if not os.environ.get("DISPLAY"):
        pytest.skip("Requires X11/Xvfb display")

    # Prepare dummy photos
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    p1 = photos_dir / "img1.jpg"
    p2 = photos_dir / "img2.jpg"
    for p in [p1, p2]:
        img = Image.new("RGB", (600, 400), color=(100, 150, 200))
        img.save(str(p), "JPEG")

    db_file = tmp_path / "kiosk_test_gal.db"
    init_database(str(db_file), initial_admin_password="AdminTestPassword123!")

    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1024x768")

    proceed_items = []
    def on_proceed(items):
        proceed_items.extend(items)

    session_id = 99
    gallery = GalleryScreen(
        parent=root,
        source_name="Test Source",
        source_path=str(photos_dir),
        session_id=session_id,
        on_proceed_to_summary=on_proceed
    )

    # Directly feed loaded photos for sync testing
    found_photos = [
        {"filename": "img1.jpg", "filepath": str(p1), "size_bytes": 1024, "modified_time": 1000.0},
        {"filename": "img2.jpg", "filepath": str(p2), "size_bytes": 2048, "modified_time": 2000.0},
    ]
    gallery._on_photos_loaded(found_photos)

    # Verify initial density and cards
    assert gallery.density_var.get() == 4
    cfg1 = gallery.photo_configs[str(p1)]
    assert len(cfg1["cards"]) == 2  # Two default sizes
    assert cfg1["cards"][0]["count"] == 0

    # 1. Density change
    gallery._set_density(8)
    assert gallery.density_var.get() == 8
    gallery._set_density(16)
    assert gallery.density_var.get() == 16

    # 2. Increment count on photo 1 card 0 -> Auto-selects photo 1 for order
    gallery.active_photo_idx = 0
    gallery._increment_card_count(0)
    assert cfg1["cards"][0]["count"] == 1
    assert 0 in gallery.selected_indices
    assert len(gallery.selected_indices) == 1

    # 3. Add additional size format -> Added across all photos with count 0 on others
    gallery._add_size_to_all("15x21")
    assert any(c["size_name"] == "15x21" for c in cfg1["cards"])
    cfg2 = gallery.photo_configs[str(p2)]
    assert any(c["size_name"] == "15x21" and c["count"] == 0 for c in cfg2["cards"])

    # 4. Remove a size on photo 1 -> Removes only on photo 1, photo 2 keeps it
    card_idx_to_remove = next(i for i, c in enumerate(cfg1["cards"]) if c["size_name"] == "15x21")
    gallery._remove_size_card(card_idx_to_remove)
    assert not any(c["size_name"] == "15x21" for c in cfg1["cards"])
    assert any(c["size_name"] == "15x21" for c in cfg2["cards"])

    # 5. Decrement count back to 0 -> Auto-deselects photo 1
    gallery._decrement_card_count(0)
    assert cfg1["cards"][0]["count"] == 0
    assert 0 not in gallery.selected_indices

    # 6. Select photo 1 again with 2 counts of 9x13
    gallery._increment_card_count(0)
    gallery._increment_card_count(0)
    assert cfg1["cards"][0]["count"] == 2
    assert 0 in gallery.selected_indices

    # Test proceed to summary: only selected items included
    gallery._proceed_to_summary()
    assert len(proceed_items) == 1
    assert proceed_items[0]["filename"] == "img1.jpg"
    assert proceed_items[0]["quantity"] == 2

    # Cleanup temp session
    clean_temp_session_dir(session_id)
    root.destroy()


def test_order_summary_grouping_and_pricing(tmp_path):
    """Verifies grouped subtotals and grand order total in OrderSummaryScreen."""
    if not os.environ.get("DISPLAY"):
        pytest.skip("Requires X11/Xvfb display")

    import customtkinter as ctk
    root = ctk.CTk()

    items = [
        {
            "source_filepath": "/tmp/a.jpg",
            "filename": "a.jpg",
            "print_size_name": "10x15",
            "paper_finish": "GLOSSY",
            "crop_mode": "fill_in",
            "quantity": 3,
            "unit_price": 1.1,
            "subtotal": 3.3,
        },
        {
            "source_filepath": "/tmp/b.jpg",
            "filename": "b.jpg",
            "print_size_name": "10x15",
            "paper_finish": "GLOSSY",
            "crop_mode": "fill_in",
            "quantity": 2,
            "unit_price": 1.1,
            "subtotal": 2.2,
        },
        {
            "source_filepath": "/tmp/c.jpg",
            "filename": "c.jpg",
            "print_size_name": "9x13",
            "paper_finish": "MATTE",
            "crop_mode": "fit_in",
            "quantity": 1,
            "unit_price": 1.0,
            "subtotal": 1.0,
        },
    ]

    confirmed = []
    summary = OrderSummaryScreen(
        parent=root,
        items=items,
        on_back=lambda: None,
        on_confirm_order=lambda itms: confirmed.extend(itms)
    )

    # 3 items, 6 prints total
    assert "6 prints" in summary.total_lbl.cget("text")
    # Total price: 3.3 + 2.2 + 1.0 = 6.50 PLN
    assert "6.50 PLN" in summary.price_total_lbl.cget("text")

    summary._submit()
    assert len(confirmed) == 3

    root.destroy()


def test_gallery_ordering_stage_bigger_preview_canvas_and_removal(tmp_path):
    """Verifies that:
    1. Image preview is displayed bigger (not restricted to 320x240).
    2. Canvas size adjusts dynamically based on the active chosen print size card.
    3. Print size cards can be removed with a button.
    """
    if not os.environ.get("DISPLAY"):
        pytest.skip("Requires display")

    photos_dir = tmp_path / "order_refactor_photos"
    photos_dir.mkdir()
    p1 = photos_dir / "photo_test.jpg"
    img = Image.new("RGB", (1200, 800), color=(50, 100, 200))
    img.save(str(p1), "JPEG")

    db_file = tmp_path / "order_refactor.db"
    init_database(str(db_file), initial_admin_password="AdminTestPassword123!")

    import customtkinter as ctk
    root = ctk.CTk()
    root.geometry("1024x768")

    gallery = GalleryScreen(
        parent=root,
        source_name="Refactor Test",
        source_path=str(photos_dir),
        session_id=101,
        on_proceed_to_summary=lambda items: None
    )

    found_photos = [
        {"filename": "photo_test.jpg", "filepath": str(p1), "size_bytes": 4096, "modified_time": 1000.0},
    ]
    gallery._on_photos_loaded(found_photos)

    photo_key = str(p1)
    cfg = gallery.photo_configs[photo_key]
    assert len(cfg["cards"]) >= 2

    # 1. Verify initial active card is 0
    assert gallery._get_active_card_idx(photo_key) == 0
    card0_size = cfg["cards"][0]["size_name"]
    card1_size = cfg["cards"][1]["size_name"]

    # 2. Select card 1 as active chosen print size card
    gallery._select_active_card(1)
    assert gallery._get_active_card_idx(photo_key) == 1

    # Verify canvas preview info label reflects card 1 size
    gallery._apply_preview_ui(
        ctk_img=ctk.CTkImage(light_image=img, dark_image=img, size=(480, 320)),
        size_name=card1_size,
        disp_w=152.0,
        disp_h=102.0,
        paper_finish="GLOSSY",
        crop_mode_label="Fill-in"
    )
    assert card1_size in gallery.canvas_info_lbl.cget("text")
    assert "152×102" in gallery.canvas_info_lbl.cget("text")

    # 3. Verify preview size is bigger (480x320 vs old 320x240 limit)
    assert gallery.cards_row.cget("height") >= 270

    # 4. Remove a print size card using _remove_size_card
    initial_card_count = len(cfg["cards"])
    gallery._remove_size_card(0)
    assert len(cfg["cards"]) == initial_card_count - 1
    # Card 0 (which was card 1 before pop) is now active
    assert gallery._get_active_card_idx(photo_key) == 0
    assert cfg["cards"][0]["size_name"] == card1_size

    # 5. Remove remaining card down to 0
    gallery._remove_size_card(0)
    assert len(cfg["cards"]) == 0
    # No error, renders empty message
    gallery._render_size_cards()

    # 6. Add new size back
    gallery._add_size_to_all("13x18")
    assert any(c["size_name"] == "13x18" for c in cfg["cards"])
    assert gallery._get_active_card_idx(photo_key) == 0

    clean_temp_session_dir(101)
    root.destroy()
