"""Tests for order creation, directory hierarchy, manifests, and crash recovery."""
import pytest
import os
import json
import shutil
from pathlib import Path
from PIL import Image

from backend.database import init_database, Order, OrderItem, get_db_engine
from backend.orders import OrderProcessor, recover_incomplete_orders
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def test_env(tmp_path):
    db_file = tmp_path / "test_kiosk.db"
    orders_dir = tmp_path / "orders"
    orders_dir.mkdir(parents=True, exist_ok=True)

    engine = init_database(str(db_file), initial_admin_password="TestPassword123!")
    Session = sessionmaker(bind=engine)
    db = Session()

    # Create dummy source images
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    img1 = Image.new("RGB", (1200, 800), (200, 50, 50))
    img1_path = sources_dir / "photo_a.jpg"
    img1.save(img1_path, dpi=(300, 300))

    img2 = Image.new("RGB", (800, 1200), (50, 200, 50))
    img2_path = sources_dir / "photo_b.jpg"
    img2.save(img2_path, dpi=(300, 300))

    # Collision test source with same name in different directory
    sub_dir = sources_dir / "sub"
    sub_dir.mkdir()
    img3 = Image.new("RGB", (600, 600), (50, 50, 200))
    img3_path = sub_dir / "photo_a.jpg"
    img3.save(img3_path, dpi=(300, 300))

    yield {
        "db": db,
        "orders_dir": orders_dir,
        "img1": str(img1_path),
        "img2": str(img2_path),
        "img3": str(img3_path),
    }

    db.close()


def test_order_processing_and_directory_hierarchy(test_env):
    db = test_env["db"]
    orders_dir = test_env["orders_dir"]
    processor = OrderProcessor(str(orders_dir))

    items = [
        {
            "source_filepath": test_env["img1"],
            "print_size_name": "10x15",
            "paper_finish": "MATTE",
            "crop_mode": "fill_in",
            "quantity": 1,
        },
        {
            "source_filepath": test_env["img2"],
            "print_size_name": "10x15",
            "paper_finish": "MATTE",
            "crop_mode": "fit_in",
            "quantity": 3,
        },
        {
            "source_filepath": test_env["img3"],  # Colliding name "photo_a.jpg"
            "print_size_name": "13x18",
            "paper_finish": "GLOSSY",
            "crop_mode": "border",
            "quantity": 2,
        },
    ]

    order_id = processor.process_order(db, items)
    assert order_id.startswith("ORD-")

    order_path = orders_dir / order_id
    assert order_path.exists()

    # 1. Check ORIGINALS directory
    orig_dir = order_path / "ORIGINALS"
    assert orig_dir.exists()
    assert (orig_dir / "photo_a.jpg").exists()
    assert (orig_dir / "photo_b.jpg").exists()
    # Colliding photo_a.jpg from subfolder should be resolved safely
    assert (orig_dir / "photo_a_1.jpg").exists()

    # 2. Check print directory grouping: [size]/[finish]/x[qty]/
    assert (order_path / "10x15" / "MATTE" / "x1" / "photo_a.jpg").exists()
    assert (order_path / "10x15" / "MATTE" / "x3" / "photo_b.jpg").exists()
    assert (order_path / "13x18" / "GLOSSY" / "x2" / "photo_a_1.jpg").exists()

    # 3. Check order.json manifest
    manifest_path = order_path / "order.json"
    assert manifest_path.exists()
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["order_id"] == order_id
    assert manifest["status"] == "COMPLETED"
    assert manifest["total_items"] == 3
    assert manifest["total_prints"] == 6  # 1 + 3 + 2
    assert len(manifest["items"]) == 3

    # 4. Check DB state
    db_order = db.query(Order).filter_by(order_id=order_id).first()
    assert db_order is not None
    assert db_order.status == "COMPLETED"
    assert db_order.total_prints == 6


def test_order_failure_preservation_and_recovery(test_env):
    db = test_env["db"]
    orders_dir = test_env["orders_dir"]
    processor = OrderProcessor(str(orders_dir))

    # Intentionally provide a non-existent file to simulate failure
    items = [
        {
            "source_filepath": "/non/existent/file.jpg",
            "print_size_name": "10x15",
            "paper_finish": "MATTE",
            "crop_mode": "fill_in",
            "quantity": 1,
        }
    ]

    with pytest.raises(FileNotFoundError):
        processor.process_order(db, items)

    # Verify order is recorded as FAILED in DB and temporary folder preserved as .failed_
    failed_orders = db.query(Order).filter_by(status="FAILED").all()
    assert len(failed_orders) >= 1
    failed_id = failed_orders[0].order_id

    failed_folder = orders_dir / f".failed_{failed_id}"
    assert failed_folder.exists()
    assert (failed_folder / "order_failure_diagnosis.json").exists()


def test_recovery_of_interrupted_orders(test_env):
    db = test_env["db"]
    orders_dir = test_env["orders_dir"]

    # Simulate an interrupted order left in PROCESSING status
    interrupted_id = "ORD-20261102-120000-TEST"
    db_order = Order(
        order_id=interrupted_id,
        status="PROCESSING",
        total_items=1,
        total_prints=1,
    )
    db.add(db_order)
    db.commit()

    # Create leftover .tmp folder
    tmp_folder = orders_dir / f".tmp_{interrupted_id}"
    tmp_folder.mkdir()

    recovered = recover_incomplete_orders(db, str(orders_dir))
    assert interrupted_id in recovered

    db.refresh(db_order)
    assert db_order.status == "FAILED"
    assert "interrupted" in db_order.error_message.lower()

    # Folder should be renamed to .failed_ to avoid dangling .tmp
    assert not tmp_folder.exists()
    assert (orders_dir / f".failed_{interrupted_id}").exists()
