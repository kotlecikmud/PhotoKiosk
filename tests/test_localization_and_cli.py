"""Automated test suite verifying localization, integrity checking, CLI commands, and order breakdown."""
import os
import io
import json
import zipfile
import pytest
import subprocess
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient

from backend.app import app
from backend.i18n import I18nManager, get_i18n, t
from backend.database import init_database, get_db_engine, Order, OrderItem, PrintServicePrice
from gui.language_toggle import LanguageToggle
from gui.app import PhotoKioskApp
from sqlalchemy.orm import sessionmaker


def test_i18n_loading_and_translation():
    """Verifies that English and Polish locales load correctly and provide accurate translations."""
    i18n = get_i18n()
    i18n.reload_locales()

    assert "en" in i18n.get_available_languages()
    assert "pl" in i18n.get_available_languages()
    assert len(i18n.get_integrity_errors()) == 0

    i18n.set_language("en")
    assert t("app.title") == "PHOTO KIOSK"
    assert t("order_summary.btn_submit") == "Submit Order →"
    # Ensure "submit and print" is not present
    assert "print" not in t("order_summary.btn_submit").lower()

    i18n.set_language("pl")
    assert t("app.title") == "FOTOKIOSK"
    assert t("order_summary.btn_submit") == "Złóż zamówienie →"


def test_i18n_integrity_validation_rejects_incomplete_locale(tmp_path):
    """Verifies that dropping in an incomplete JSON locale triggers integrity rejection."""
    locales_dir = Path("config/locales")
    broken_file = locales_dir / "incomplete.json"
    try:
        with open(broken_file, "w", encoding="utf-8") as f:
            json.dump({"_locale_name": "Incomplete", "app": {"title": "Only One Key"}}, f)

        mgr = I18nManager(locales_dir=locales_dir)
        errors = mgr.get_integrity_errors()

        assert "incomplete" in errors
        assert len(errors["incomplete"]) > 10  # Missing dozens of required base keys
        assert "incomplete" not in mgr.get_available_languages()
    finally:
        if broken_file.exists():
            broken_file.unlink()


def test_language_toggle_widget_persistence():
    """Verifies LanguageToggle switches active language and does not reset."""
    if not os.environ.get("DISPLAY"):
        pytest.skip("Requires X11/Xvfb display")

    import customtkinter as ctk
    root = ctk.CTk()

    i18n = get_i18n()
    i18n.set_language("en")

    toggle = LanguageToggle(root)
    toggle.pack()

    # Switch to Polish
    toggle._select_language("pl")
    assert i18n.current_lang == "pl"

    # Simulate navigation / recreation: new toggle must stay on PL
    toggle2 = LanguageToggle(root)
    assert i18n.current_lang == "pl"

    toggle.destroy()
    toggle2.destroy()
    root.destroy()


def test_admin_settings_displays_languages_and_integrity_warnings():
    """Verifies that the admin settings page renders the language section and any integrity warnings."""
    init_database("storage/photokiosk.db", initial_admin_password="AdminPassword123!")

    locales_dir = Path("config/locales")
    broken_file = locales_dir / "test_err.json"
    try:
        with open(broken_file, "w", encoding="utf-8") as f:
            json.dump({"_locale_name": "Broken", "app": {"title": "Broken"}}, f)

        client = TestClient(app)
        login_res = client.post("/admin/login", data={"password": "AdminPassword123!"}, follow_redirects=False)
        assert login_res.status_code == 303
        cookie = login_res.cookies.get("admin_session")

        res = client.get("/admin/settings", cookies={"admin_session": cookie})
        assert res.status_code == 200
        assert "Language &amp; Localization" in res.text or "Language & Localization" in res.text
        assert "TEST_ERR" in res.text
        assert "missing keys" in res.text
    finally:
        if broken_file.exists():
            broken_file.unlink()


def test_services_import_omits_fotokopia():
    """Verifies that importing services.json omits items containing '(fotokopia)'."""
    init_database("storage/photokiosk.db", initial_admin_password="AdminPassword123!")
    engine = get_db_engine("storage/photokiosk.db")
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    items = [
        {"id": "ODBITK_FOTO_1", "name": "10x15 GLOSSY (fotokopia)", "sale_price": 4.5},
        {"id": "ODBITK_STD_1", "name": "10x15 GLOSSY", "sale_price": 1.2},
    ]

    client = TestClient(app)
    login_res = client.post("/admin/login", data={"password": "AdminPassword123!"}, follow_redirects=False)
    cookie = login_res.cookies.get("admin_session")

    res = client.post(
        "/admin/services/import",
        files={"services_file": ("services.json", json.dumps(items).encode("utf-8"), "application/json")},
        cookies={"admin_session": cookie},
        follow_redirects=True,
    )
    assert res.status_code == 200

    foto_item = session.query(PrintServicePrice).filter_by(service_id="ODBITK_FOTO_1").first()
    assert foto_item is None
    std_item = session.query(PrintServicePrice).filter_by(service_id="ODBITK_STD_1").first()
    assert std_item is not None
    assert std_item.sale_price == 1.2
    session.close()


def test_order_zip_download():
    """Verifies that the admin order inspection endpoint provides a downloadable zip of order files."""
    init_database("storage/photokiosk.db", initial_admin_password="AdminPassword123!")
    engine = get_db_engine("storage/photokiosk.db")
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    from backend.orders import generate_order_id
    order_id = generate_order_id()
    db_order = Order(
        order_id=order_id,
        status="COMPLETED",
        total_items=1,
        total_prints=2,
    )
    session.add(db_order)
    session.commit()
    session.close()

    order_folder = Path("storage/shared/orders") / order_id
    order_folder.mkdir(parents=True, exist_ok=True)
    orig_folder = order_folder / "ORIGINALS"
    orig_folder.mkdir(exist_ok=True)
    (order_folder / "order.json").write_text(json.dumps({"order_id": order_id}))
    (orig_folder / "photo1.jpg").write_bytes(b"dummy image data")

    try:
        client = TestClient(app)
        login_res = client.post("/admin/login", data={"password": "AdminPassword123!"}, follow_redirects=False)
        cookie = login_res.cookies.get("admin_session")

        res = client.get(f"/admin/orders/{order_id}/download", cookies={"admin_session": cookie})
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            names = zf.namelist()
            assert "order.json" in names
            assert "ORIGINALS/photo1.jpg" in names
    finally:
        import shutil
        if order_folder.exists():
            shutil.rmtree(order_folder)


def test_gallery_config_persists_across_summary_navigation(tmp_path):
    """Verifies photo configurations and counts persist when navigating from Gallery to Summary and back."""
    if not os.environ.get("DISPLAY"):
        pytest.skip("Requires X11/Xvfb display")

    photos_dir = tmp_path / "photos_persist"
    photos_dir.mkdir()
    p1 = photos_dir / "img1.jpg"
    p2 = photos_dir / "img2.jpg"
    for p in [p1, p2]:
        img = Image.new("RGB", (300, 300), color=(120, 150, 180))
        img.save(str(p), "JPEG")

    app = PhotoKioskApp(fullscreen=False)
    app._on_source_selected("Persist Source", str(photos_dir))

    found_photos = [
        {"filename": "img1.jpg", "filepath": str(p1), "size_bytes": 1024, "modified_time": 1000.0},
        {"filename": "img2.jpg", "filepath": str(p2), "size_bytes": 2048, "modified_time": 2000.0},
    ]
    gallery = [w for w in app.container.winfo_children() if w.__class__.__name__ == "GalleryScreen"][0]
    gallery._on_photos_loaded(found_photos)

    # Configure photo 1 with count 3
    gallery.active_photo_idx = 0
    gallery._increment_card_count(0)
    gallery._increment_card_count(0)
    gallery._increment_card_count(0)
    assert gallery.photo_configs[str(p1)]["cards"][0]["count"] == 3
    assert 0 in gallery.selected_indices

    # Proceed to summary
    gallery._proceed_to_summary()

    # Verify Summary screen is open
    summary = [w for w in app.container.winfo_children() if w.__class__.__name__ == "OrderSummaryScreen"][0]
    assert summary is not None

    # Navigate back to gallery
    summary.on_back()

    # Re-rendered gallery screen must preserve photo_configs and selected_indices
    gallery2 = [w for w in app.container.winfo_children() if w.__class__.__name__ == "GalleryScreen"][0]
    gallery2._on_photos_loaded(found_photos)

    assert gallery2.photo_configs[str(p1)]["cards"][0]["count"] == 3
    assert 0 in gallery2.selected_indices

    app.destroy()


def test_cli_order_create_and_show(tmp_path):
    """Verifies creating an order from a directory via CLI and viewing detailed breakdown."""
    photos_dir = tmp_path / "cli_sample_photos"
    photos_dir.mkdir()

    for i in range(2):
        img = Image.new("RGB", (300, 200), color=(100, 100 + i * 20, 150))
        img.save(str(photos_dir / f"sample_{i}.jpg"))

    # Execute order create
    create_proc = subprocess.run(
        ["python3", "main.py", "order", "create", "--dir", str(photos_dir), "--size", "10x15", "--finish", "MATTE", "--copies", "3"],
        capture_output=True,
        text=True
    )
    assert create_proc.returncode == 0
    assert "Successfully Created New Order: ORD-" in create_proc.stdout
    assert "Total Photos: 2" in create_proc.stdout
    assert "Total Prints: 6" in create_proc.stdout

    import re
    order_id = re.search(r"ORD-\d+-\d+-\w+", create_proc.stdout).group(0)

    # Execute order show
    show_proc = subprocess.run(
        ["python3", "main.py", "order", "show", order_id, "-v"],
        capture_output=True,
        text=True
    )
    assert show_proc.returncode == 0
    assert f"Order Inspection: {order_id}" in show_proc.stdout
    assert "10x15" in show_proc.stdout
    assert "MATTE" in show_proc.stdout
    assert "sample_0.jpg" in show_proc.stdout
    assert "sample_1.jpg" in show_proc.stdout
