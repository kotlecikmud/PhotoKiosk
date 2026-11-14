"""Integration tests for the web kiosk API (/api/v1/kiosk) and the served single-page app."""
import json
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import sessionmaker

from backend.app import app
from backend.database import Order, OrderItem, PrintServicePrice, get_db_engine, init_database
from backend.i18n import get_i18n
from backend.kiosk_api import KioskServices
from backend.sources import encode_photo_id
from devices.scanner import authoritative_scanner

API = "/api/v1/kiosk"


@pytest.fixture
def env(tmp_path, monkeypatch):
    get_i18n()  # load locales (relative to the repo) before we change directory
    monkeypatch.chdir(tmp_path)
    init_database(str(tmp_path / "kiosk.db"), initial_admin_password="AdminTestPassword123!")
    shared = tmp_path / "shared"
    shared.mkdir()
    Image.new("RGB", (600, 400), (200, 30, 30)).save(shared / "land.jpg", dpi=(300, 300))
    Image.new("RGB", (300, 450), (30, 200, 30)).save(shared / "port.jpg")
    with sessionmaker(bind=get_db_engine())() as db:
        db.add(PrintServicePrice(service_id="T1", name="10x15 glossy", size_name="10x15",
                                 paper_type="glossy", sale_price=1.5))
        db.commit()
    svc = KioskServices(
        shared_dir=str(shared), temp_base=str(tmp_path / "tmp"),
        thumbs_dir=str(tmp_path / "thumbs"), orders_dir=str(tmp_path / "orders"),
    )
    old = app.state.kiosk
    app.state.kiosk = svc
    yield TestClient(app), svc
    app.state.kiosk = old


def _photo_ids(client):
    photos = client.get(f"{API}/sources/shared/photos").json()["photos"]
    return {p["name"]: p for p in photos}


def test_bootstrap_has_catalog_theme_language(env):
    client, _ = env
    data = client.get(f"{API}/bootstrap").json()
    assert "10x15" in [s["name"] for s in data["catalog"]["sizes"]]
    assert data["catalog"]["prices"]["10x15|glossy"] == 1.5
    assert data["theme"]["colors"]["primary"].startswith("#")
    assert {"en", "pl"} <= {l["code"] for l in data["language"]["available"]}
    assert "auto_reset_seconds" in data["kiosk"]


def test_i18n_returns_full_dictionary_and_404(env):
    client, _ = env
    pl = client.get(f"{API}/i18n/pl").json()["strings"]
    en = client.get(f"{API}/i18n/en").json()["strings"]
    assert pl["order_summary.btn_submit"] == "Złóż zamówienie →"
    assert set(pl) >= set(en)
    assert client.get(f"{API}/i18n/xx").status_code == 404


def test_sources_and_photo_listing_never_leak_paths(env, tmp_path):
    client, _ = env
    sources = client.get(f"{API}/sources").json()
    assert any(s["id"] == "shared" for s in sources["sources"])
    body = client.get(f"{API}/sources/shared/photos").text
    assert str(tmp_path) not in body
    assert set(_photo_ids(client)) == {"land.jpg", "port.jpg"}


def test_dims_and_cache_fill_listing(env):
    client, _ = env
    photos = _photo_ids(client)
    assert photos["land.jpg"]["w"] is None  # not probed yet
    ids = [p["id"] for p in photos.values()] + [encode_photo_id("../x.jpg"), "!!"]
    res = client.post(f"{API}/sources/shared/dims", json={"ids": ids[:3]})
    assert res.status_code == 400  # a crafted id is rejected outright
    res = client.post(f"{API}/sources/shared/dims", json={"ids": ids[:2]}).json()["dims"]
    assert res[photos["land.jpg"]["id"]] == [600, 400]
    assert res[photos["port.jpg"]["id"]] == [300, 450]
    assert _photo_ids(client)["land.jpg"]["w"] == 600


def test_thumbnail_is_cacheable_jpeg_and_traversal_blocked(env):
    client, _ = env
    pid = _photo_ids(client)["land.jpg"]["id"]
    res = client.get(f"{API}/sources/shared/photos/{pid}/thumb?v=1")
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert "immutable" in res.headers["cache-control"]
    evil = encode_photo_id("../../etc/passwd")
    assert client.get(f"{API}/sources/shared/photos/{evil}/thumb").status_code == 400
    assert client.get(f"{API}/sources/nope/photos/{pid}/thumb").status_code == 404


def test_preview_matches_paper_and_reports_meta(env):
    client, _ = env
    pid = _photo_ids(client)["land.jpg"]["id"]
    res = client.get(f"{API}/sources/shared/photos/{pid}/preview?size=10x15&crop=fill_in&w=600&h=500")
    assert res.status_code == 200
    meta = json.loads(res.headers["x-preview-meta"])
    # landscape photo on a portrait 10x15 paper -> paper orientation is swapped to landscape
    assert meta["paper_mm"] == [152.0, 102.0]
    img = Image.open(__import__("io").BytesIO(res.content))
    assert abs(img.width / img.height - 152 / 102) < 0.02
    assert client.get(f"{API}/sources/shared/photos/{pid}/preview?size=99x99").status_code == 400
    assert client.get(f"{API}/sources/shared/photos/{pid}/preview?size=10x15&crop=bad").status_code == 400
    # no_resize works against the original file
    res = client.get(f"{API}/sources/shared/photos/{pid}/preview?size=10x15&crop=no_resize")
    assert res.status_code == 200


def _wait_job(client, job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"{API}/orders/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("order job did not finish")


def test_order_flow_staging_pricing_and_cleanup(env, tmp_path):
    client, svc = env
    photos = _photo_ids(client)
    land, port = photos["land.jpg"]["id"], photos["port.jpg"]["id"]
    cart = "cart-abcdef123456"

    assert client.put(f"{API}/carts/{cart}/staged",
                      json={"source_id": "shared", "add": [land, port]}).status_code == 202
    for _ in range(100):  # staging is asynchronous
        if svc.stager.staged_path(cart, "shared", land) and svc.stager.staged_path(cart, "shared", port):
            break
        time.sleep(0.05)
    staged = svc.stager.staged_path(cart, "shared", land)
    assert staged and staged.read_bytes() == (tmp_path / "shared" / "land.jpg").read_bytes()

    # deleting the originals must not break the order: staged copies are used
    (tmp_path / "shared" / "land.jpg").unlink()

    items = [
        {"source_id": "shared", "photo_id": land, "size": "10x15", "finish": "glossy", "crop": "fill_in", "qty": 2},
        {"source_id": "shared", "photo_id": port, "size": "10x15", "finish": "GLOSSY", "crop": "fit_in", "qty": 1},
    ]
    res = client.post(f"{API}/orders", json={"items": items, "cart_id": cart, "request_id": "req-1"})
    assert res.status_code == 202
    again = client.post(f"{API}/orders", json={"items": items, "cart_id": cart, "request_id": "req-1"})
    assert again.json()["job_id"] == res.json()["job_id"]  # double tap -> one order

    job = _wait_job(client, res.json()["job_id"])
    assert job["status"] == "completed", job
    assert job["result"]["total_prints"] == 3
    assert job["result"]["total_price"] == 4.5  # server-side pricing: 3 x 1.50

    with sessionmaker(bind=get_db_engine())() as db:
        order = db.query(Order).filter_by(order_id=job["result"]["order_id"]).one()
        assert order.status == "COMPLETED" and order.total_prints == 3
        assert {(i.print_size_name, i.paper_finish, i.quantity) for i in db.query(OrderItem).all()} >= {
            ("10x15", "GLOSSY", 2), ("10x15", "GLOSSY", 1)}
    assert not (tmp_path / "tmp" / f"cart_{cart}").exists()  # staging cleaned after success


def test_order_validation(env):
    client, _ = env
    pid = _photo_ids(client)["land.jpg"]["id"]
    base = {"source_id": "shared", "photo_id": pid, "size": "10x15", "finish": "GLOSSY", "crop": "fill_in", "qty": 1}
    post = lambda **kw: client.post(f"{API}/orders", json={"items": [{**base, **kw}]})
    assert post(size="1x1").status_code == 400
    assert post(finish="GOLD").status_code == 400
    assert post(crop="stretch").status_code == 422
    assert post(qty=0).status_code == 422
    assert post(photo_id=encode_photo_id("../../etc/passwd")).status_code == 400
    assert post(photo_id=encode_photo_id("missing.jpg")).status_code == 404
    assert client.post(f"{API}/orders", json={"items": []}).status_code == 422


def test_invalid_cart_ids_rejected(env):
    client, _ = env
    assert client.put(f"{API}/carts/..%2Fx/staged", json={"source_id": "shared"}).status_code in (400, 404)
    assert client.put(f"{API}/carts/short/staged", json={"source_id": "shared"}).status_code == 400


def test_phone_flow_open_registers_session_source(env, tmp_path):
    client, svc = env
    authoritative_scanner.on_device_disconnected()
    assert client.get(f"{API}/sources").json()["phone"] is None
    assert client.post(f"{API}/phone/open", json={"range_key": "all"}).status_code == 409

    authoritative_scanner.on_device_connected("Apple iPhone", auto_scan=False, device_type="iphone")
    try:
        with authoritative_scanner._lock:
            authoritative_scanner._state["discovered_photos"] = [
                {"filename": "IMG_1.JPG", "relative_path": "DCIM/IMG_1.JPG", "modified_time": time.time(),
                 "size_bytes": 1, "category": "camera", "category_name": "Camera", "album": "Camera"},
                {"filename": "IMG_2.JPG", "relative_path": "DCIM/IMG_2.JPG", "modified_time": 1.0,
                 "size_bytes": 1, "category": "camera", "category_name": "Camera", "album": "Camera"},
            ]
        phone = client.get(f"{API}/sources").json()["phone"]
        assert phone["device"] == "Apple iPhone" and "discovered_photos" not in phone
        ranges = {r["key"]: r["count"] for r in client.get(f"{API}/phone/date-ranges").json()["ranges"]}
        assert ranges["1_month"] == 1 and ranges["all"] == 2
        src = client.post(f"{API}/phone/open", json={"range_key": "1_month"}).json()["source"]
        assert src["kind"] == "phone"
        names = [p["name"] for p in client.get(f"{API}/sources/{src['id']}/photos").json()["photos"]]
        assert names == ["IMG_1.JPG"]
        assert client.post(f"{API}/phone/open", json={"range_key": "bogus"}).status_code == 422
    finally:
        authoritative_scanner.on_device_disconnected()


def test_scanner_state_slimming_and_event_hub():
    import asyncio
    from backend.events import EventHub, slim_scanner_state

    slim = slim_scanner_state({"device": "x", "discovered_photos": [1, 2, 3]})
    assert slim == {"device": "x", "discovered_count": 3}

    async def run():
        hub = EventHub()
        hub.bind_loop(asyncio.get_running_loop())
        q = hub.subscribe()
        await asyncio.get_running_loop().run_in_executor(None, hub.publish, "device", {"action": "mounted"})
        return await asyncio.wait_for(q.get(), 2)

    assert asyncio.run(run()) == ("device", {"action": "mounted"})


def test_index_is_served_with_theme_variables_and_static_revalidates(env):
    client, _ = env
    res = client.get("/")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]
    assert "--primary" in res.text
    assert client.get("/static/js/main.js").headers.get("cache-control") == "no-cache"
