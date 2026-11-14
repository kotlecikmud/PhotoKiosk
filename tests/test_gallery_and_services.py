"""Integration tests for services.json price binding and admin paper settings."""
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
