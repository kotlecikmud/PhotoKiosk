"""Automated tests for FastAPI endpoints, admin authentication, and filesystem watcher."""
import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from PIL import Image

from backend.app import create_app
from backend.database import init_database, get_db_engine, SystemSetting, PrintSize
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def api_client(tmp_path):
    db_file = tmp_path / "test_api.db"
    shared_dir = tmp_path / "storage" / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)

    # Initialize DB with known password
    init_database(str(db_file), initial_admin_password="AdminPassword123!")

    app = create_app()
    client = TestClient(app)
    return client


def test_api_health(api_client):
    res = api_client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_get_print_sizes(api_client):
    res = api_client.get("/api/v1/print-sizes")
    assert res.status_code == 200
    sizes = res.json()
    assert len(sizes) >= 6
    names = [s["name"] for s in sizes]
    assert "10x15" in names
    assert "13x18" in names


def test_admin_login_and_protection(api_client):
    # Test invalid password fails
    res_bad = api_client.post(
        "/admin/login",
        data={"password": "WrongPassword"},
        follow_redirects=False
    )
    assert res_bad.status_code == 400

    # Test valid password succeeds and sets session cookie
    res_ok = api_client.post(
        "/admin/login",
        data={"password": "AdminPassword123!"},
        follow_redirects=False
    )
    assert res_ok.status_code == 303
    assert "admin_session" in res_ok.cookies

    # Access protected admin dashboard using session cookie
    cookie = {"admin_session": res_ok.cookies["admin_session"]}
    dash = api_client.get("/admin/", cookies=cookie)
    assert dash.status_code == 200
    assert "System Overview" in dash.text

    # Test settings update with source toggles
    settings_res = api_client.post(
        "/admin/settings",
        data={
            "default_dpi": "300",
            "border_width_mm": "4.0",
            "auto_orientation_matching": "true",
            "source_iphone": "true",
            "source_network": "true",
            # Android, Removable, CD/DVD unchecked
        },
        cookies=cookie,
        follow_redirects=False
    )
    assert settings_res.status_code == 303

    # Test file manager browsing and download
    shared_dir = Path("storage/shared")
    sub_dir = shared_dir / "holiday_trip"
    sub_dir.mkdir(parents=True, exist_ok=True)
    sample_file = sub_dir / "beach.jpg"
    sample_file.write_text("dummy-image-content")

    # Browse subdirectory
    files_view = api_client.get("/admin/files?path=holiday_trip", cookies=cookie)
    assert files_view.status_code == 200
    assert "beach.jpg" in files_view.text

    # Download file
    dl_res = api_client.get("/admin/files/download?filepath=holiday_trip/beach.jpg", cookies=cookie)
    assert dl_res.status_code == 200
    assert dl_res.content == b"dummy-image-content"

    # Download folder as zip
    dl_zip = api_client.get("/admin/files/download?filepath=holiday_trip", cookies=cookie)
    assert dl_zip.status_code == 200
    assert dl_zip.headers["content-type"] == "application/zip"

    # Test changelog presence and version 0.0.2
    changelog_p = Path("changelog.log")
    assert changelog_p.exists()
    assert "0.0.2" in changelog_p.read_text()

    # Test theme switching
    from gui.theme import THEMES, set_active_theme, COLORS
    set_active_theme("emerald_green")
    assert COLORS["primary"] == THEMES["emerald_green"]["primary"]
    set_active_theme("midnight_blue")
