"""Friendly device names, live source detection, and settings pushed to the kiosk."""
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.app import app
from backend.bluetooth import BluetoothReceiver
from backend.database import SystemSetting, get_db_engine, init_database
from backend.i18n import get_i18n
from backend.kiosk_api import KioskServices
from backend.source_watch import SourceWatcher
from backend.sources import SourceRegistry
from devices import storage
from devices.storage import StorageManager, format_size

# What `lsblk -J -b -o PATH,NAME,TYPE,RM,HOTPLUG,TRAN,FSTYPE,LABEL,MODEL,VENDOR,SIZE,MOUNTPOINTS` prints on Ubuntu 24.04
LSBLK = json.loads("""{"blockdevices": [
 {"path":"/dev/sda","name":"sda","type":"disk","rm":false,"hotplug":false,"tran":"sata","fstype":null,"label":null,
  "model":"Samsung SSD 860","vendor":"ATA     ","size":500107862016,"mountpoints":[null],
  "children":[{"path":"/dev/sda1","name":"sda1","type":"part","rm":false,"hotplug":false,"tran":null,"fstype":"ext4",
   "label":null,"model":null,"vendor":null,"size":500000000000,"mountpoints":["/"]}]},
 {"path":"/dev/sdb","name":"sdb","type":"disk","rm":true,"hotplug":true,"tran":"usb","fstype":null,"label":null,
  "model":"Cruzer Blade","vendor":"SanDisk ","size":32010928128,"mountpoints":[null],
  "children":[{"path":"/dev/sdb1","name":"sdb1","type":"part","rm":true,"hotplug":true,"tran":null,"fstype":"vfat",
   "label":"HOLIDAY","model":null,"vendor":null,"size":32009879552,"mountpoints":["/media/kiosk/HOLIDAY"]}]},
 {"path":"/dev/sdc","name":"sdc","type":"disk","rm":true,"hotplug":true,"tran":"usb","fstype":null,"label":null,
  "model":"Ultra Fit","vendor":"SanDisk ","size":16008609792,"mountpoints":[null],
  "children":[{"path":"/dev/sdc1","name":"sdc1","type":"part","rm":true,"hotplug":true,"tran":null,"fstype":"exfat",
   "label":null,"model":null,"vendor":null,"size":16000000000,"mountpoints":[null]}]},
 {"path":"/dev/sr0","name":"sr0","type":"rom","rm":true,"hotplug":true,"tran":"sata","fstype":"iso9660",
  "label":"WEDDING_2024","model":"DVD-RW","vendor":"HL-DT-ST","size":4700000000,"mountpoints":["/media/kiosk/WEDDING_2024"]}
]}""")


def test_format_size():
    assert format_size(31914983424) == "29.7 GB" and format_size(500) == "500 B" and format_size(0) == ""


def test_linux_names_use_label_or_vendor_model_and_skip_system_disks(monkeypatch, tmp_path):
    (tmp_path / "HOLIDAY").mkdir()
    blocks = storage._flatten_lsblk(LSBLK["blockdevices"])
    next(b for b in blocks if b["path"] == "/dev/sdb1")["mountpoints"] = [str(tmp_path / "HOLIDAY")]  # path must exist
    monkeypatch.setattr(storage.sys, "platform", "linux")
    monkeypatch.setattr(StorageManager, "list_block_devices", staticmethod(lambda: blocks))
    media = StorageManager.get_mounted_media_paths()
    assert len(media) == 1                                          # not the system SSD, not the disc
    assert media[0]["label"] == "HOLIDAY" and media[0]["device"] == "SanDisk Cruzer Blade"
    assert media[0]["name"] == "HOLIDAY (29.8 GB)" and media[0]["size"] == 32009879552


def test_unlabelled_stick_is_named_after_its_vendor_and_model(monkeypatch):
    blocks = storage._flatten_lsblk(LSBLK["blockdevices"])
    sdc1 = next(b for b in blocks if b["path"] == "/dev/sdc1")
    assert sdc1["vendor"] == "SanDisk" and sdc1["model"] == "Ultra Fit" and sdc1["removable"]


def test_optical_disc_uses_its_volume_label(monkeypatch, tmp_path):
    (tmp_path / "disc").mkdir()
    blocks = storage._flatten_lsblk(LSBLK["blockdevices"])
    next(b for b in blocks if b["type"] == "rom")["mountpoints"] = [str(tmp_path / "disc")]
    monkeypatch.setattr(storage.sys, "platform", "linux")
    monkeypatch.setattr(StorageManager, "list_block_devices", staticmethod(lambda: blocks))
    disc = StorageManager.get_optical_media_paths()
    assert [d["label"] for d in disc] == ["WEDDING_2024"]
    assert all(m["label"] != "WEDDING_2024" for m in StorageManager.get_mounted_media_paths())   # a disc is not a USB stick


def test_windows_drive_shows_volume_label_and_size(monkeypatch):
    monkeypatch.setattr(storage.sys, "platform", "win32")
    monkeypatch.setattr(storage.os.path, "exists", lambda p: p in ("E:\\", "F:\\"))
    monkeypatch.setattr(StorageManager, "get_windows_drive_type", staticmethod(lambda d: storage.DRIVE_REMOVABLE))
    info = {"E:\\": {"label": "KINGSTON", "size": 31914983424}, "F:\\": {"size": 8000000000}}
    monkeypatch.setattr(StorageManager, "windows_volume_info", staticmethod(lambda r: info[r]))
    e, f = StorageManager.get_mounted_media_paths()
    assert e["name"] == "KINGSTON (E:)" and e["label"] == "KINGSTON" and e["drive"] == "E:" and e["size"] == 31914983424
    assert f["name"] == "USB Drive (F:)" and f["label"] == ""      # unlabelled: UI substitutes a localised name


def test_unmounted_usb_partitions_are_mounted_automatically_but_not_retried_every_poll(monkeypatch):
    blocks = storage._flatten_lsblk(LSBLK["blockdevices"])
    mounted = []
    monkeypatch.setattr(storage.sys, "platform", "linux")
    monkeypatch.setattr(StorageManager, "is_udisksctl_available", staticmethod(lambda: True))
    monkeypatch.setattr(StorageManager, "list_block_devices", staticmethod(lambda: blocks))
    monkeypatch.setattr(StorageManager, "mount_block_device", staticmethod(lambda n: mounted.append(n) or "/media/x"))
    StorageManager._mount_attempts.clear()
    StorageManager.mount_pending_removables()
    assert mounted == ["/dev/sdc1"]                                  # only the unmounted removable partition
    StorageManager.mount_pending_removables()
    assert mounted == ["/dev/sdc1"]                                  # backoff


def test_source_watcher_publishes_only_when_the_list_changes(monkeypatch, tmp_path):
    present = []
    monkeypatch.setattr(StorageManager, "get_mounted_media_paths", staticmethod(lambda: list(present)))
    monkeypatch.setattr(StorageManager, "get_optical_media_paths", staticmethod(lambda: []))
    reg = SourceRegistry(str(tmp_path / "shared"), str(tmp_path / "tmp"))
    events = []
    watcher = SourceWatcher(reg, lambda e, d: events.append((e, d)), automount=False)
    assert watcher.check_once() is False and events == []            # first look just records
    stick = tmp_path / "stick"
    stick.mkdir()
    present.append({"name": "HOLIDAY (29.8 GB)", "path": str(stick), "type": "removable_storage",
                    "label": "HOLIDAY", "device": "SanDisk Cruzer", "drive": "", "size": 32009879552})
    assert watcher.check_once() is True
    kinds = [s["kind"] for s in events[-1][1]["sources"]]
    assert events[-1][0] == "sources" and kinds == ["shared", "removable"]
    assert events[-1][1]["sources"][1]["label"] == "HOLIDAY" and "path" not in events[-1][1]["sources"][1]
    assert watcher.check_once() is False and len(events) == 1        # unchanged -> silent
    present.clear()
    assert watcher.check_once() is True and [s["kind"] for s in events[-1][1]["sources"]] == ["shared"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    get_i18n()                      # (no chdir: the admin panel loads its templates relative to the repo)
    init_database(str(tmp_path / "live.db"), initial_admin_password="AdminTestPassword123!")
    svc = KioskServices(shared_dir=str(tmp_path / "shared"), temp_base=str(tmp_path / "tmp"),
                        thumbs_dir=str(tmp_path / "thumbs"), orders_dir=str(tmp_path / "orders"))
    svc.bluetooth = BluetoothReceiver(str(tmp_path / "bt"), publish=svc.hub.publish, which=lambda n: None,
                                      platform="linux", find_obexd_fn=lambda: None)
    svc.registry.bluetooth_root = svc.bluetooth.session_root
    events = []
    svc.hub.publish = lambda e, d: events.append((e, d))
    old, app.state.kiosk = app.state.kiosk, svc
    client = TestClient(app)
    cookie = client.post("/admin/login", data={"password": "AdminTestPassword123!"},
                         follow_redirects=False).cookies.get("admin_session")
    yield client, svc, events, {"admin_session": cookie}
    app.state.kiosk = old


def _save(client, cookies, **over):
    form = {"default_dpi": "300", "border_width_mm": "4.0", "auto_orientation_matching": "true",
            "ui_theme": "midnight_blue", "system_language": "en", "source_removable": "true",
            "source_network": "true", "source_cddvd": "true", "source_iphone": "true", "source_android": "true"}
    form.update(over)
    return client.post("/admin/settings", data=form, cookies=cookies, follow_redirects=False)


def test_saving_settings_updates_the_kiosk_immediately(env):
    client, svc, events, cookies = env
    before = client.get("/api/v1/kiosk/bootstrap").json()
    assert before["theme"]["key"] == "midnight_blue" and before["language"]["default"] == "en"

    assert _save(client, cookies, ui_theme="emerald_green", system_language="pl", border_width_mm="6.5").status_code == 303
    assert [e for e, _ in events].count("config") == 1               # the kiosk is told to refresh

    after = client.get("/api/v1/kiosk/bootstrap").json()             # no waiting for the settings cache
    assert after["theme"]["key"] == "emerald_green"
    assert after["theme"]["colors"]["primary"] != before["theme"]["colors"]["primary"]
    assert after["language"]["default"] == "pl"                      # the language dropdown is finally saved
    assert after["settings"]["border_width_mm"] == 6.5


def test_unknown_language_is_ignored_and_catalog_changes_notify(env):
    client, svc, events, cookies = env
    _save(client, cookies, system_language="xx")
    assert client.get("/api/v1/kiosk/bootstrap").json()["language"]["default"] == "en"
    events.clear()
    res = client.post("/admin/paper-finishes", data={"name": "SILK"}, cookies=cookies, follow_redirects=False)
    assert res.status_code < 400 and [e for e, _ in events] == ["config"]
    assert "SILK" in client.get("/api/v1/kiosk/bootstrap").json()["catalog"]["finishes"]


def test_failed_or_read_only_admin_requests_do_not_notify(env):
    client, svc, events, cookies = env
    client.get("/admin/settings", cookies=cookies)
    client.post("/admin/settings", data={"default_dpi": "300"}, cookies=cookies)       # 422: missing fields
    client.post("/admin/login", data={"password": "wrong"})
    assert events == []


def test_source_toggles_and_bluetooth_flag_apply_at_once(env):
    client, svc, events, cookies = env
    assert client.get("/api/v1/kiosk/sources").json()["bluetooth"]["enabled"] is False
    _save(client, cookies, source_network="", source_bluetooth="true", bluetooth_name="Lab Kiosk")
    assert svc.bluetooth.enabled and svc.bluetooth.name == "Lab Kiosk"
    sources = client.get("/api/v1/kiosk/sources").json()
    assert all(s["kind"] != "shared" for s in sources["sources"]) and sources["bluetooth"]["enabled"] is True
    assert sources["bluetooth"]["available"] is False                # no BlueZ here: reported, not faked
    page = client.get("/admin/settings", cookies=cookies).text
    assert "Not available on this machine" in page and 'value="Lab Kiosk"' in page


def test_bluetooth_photo_to_order_end_to_end(env, tmp_path):
    """A 'phone' drops a file into the inbox (external mode stands in for BlueZ)."""
    from PIL import Image
    client, svc, events, cookies = env
    rx = svc.bluetooth = BluetoothReceiver(str(tmp_path / "bt2"), publish=svc.hub.publish,
                                           external_inbox=str(tmp_path / "phone_inbox"))
    svc.registry.bluetooth_root = rx.session_root
    rx.configure(True, "Test Kiosk")
    try:
        assert client.get("/api/v1/kiosk/bluetooth").json()["available"] is True
        session = client.post("/api/v1/kiosk/bluetooth/session").json()["session"]
        sid = session["source_id"]
        Image.new("RGB", (300, 200), (200, 60, 60)).save(rx.inbox / "from_android.jpg", "JPEG")

        photos = []
        for _ in range(60):                                      # wait for "transfer finished"
            photos = client.get("/api/v1/kiosk/bluetooth").json()["session"]["photos"]
            if photos:
                break
            time.sleep(0.2)
        assert [p["name"] for p in photos] == ["from_android.jpg"]
        assert any(e == "bluetooth" and d["session"] and d["session"]["photos"] for e, d in events)

        listed = client.get(f"/api/v1/kiosk/sources/{sid}/photos").json()["photos"]
        assert [p["name"] for p in listed] == ["from_android.jpg"]
        assert client.get(f"/api/v1/kiosk/sources/{sid}/photos/{listed[0]['id']}/thumb").status_code == 200
        assert str(tmp_path) not in client.get("/api/v1/kiosk/bluetooth").text      # no paths leak

        item = {"source_id": sid, "photo_id": listed[0]["id"], "size": "10x15", "finish": "GLOSSY",
                "crop": "fill_in", "qty": 1}
        assert client.post("/api/v1/kiosk/orders", json={"items": [item]}).status_code == 202

        assert client.delete(f"/api/v1/kiosk/bluetooth/session/{session['id']}").status_code == 204
        assert client.get(f"/api/v1/kiosk/sources/{sid}/photos").status_code == 404   # next customer sees nothing
        assert client.get("/api/v1/kiosk/bluetooth").json()["session"] is None
    finally:
        rx.stop()


def test_bluetooth_session_refused_when_disabled(env):
    client, svc, events, cookies = env
    assert client.post("/api/v1/kiosk/bluetooth/session").status_code == 409
