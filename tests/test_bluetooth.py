"""Bluetooth receiver: BlueZ is replaced by fake processes, so everything except the radio is verified."""
import threading
import time

import pytest
from PIL import Image

from backend import bluetooth as bt
from backend.bluetooth import BluetoothReceiver, BluetoothUnavailable, sanitize_name


class FakeStdin:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.extend(text.splitlines())

    def flush(self):
        pass


class FakeProc:
    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.stdin, self.stdout = cmd, kw, FakeStdin(), []
        self.alive = True

    def poll(self):
        return None if self.alive else 1

    def terminate(self):
        self.alive = False

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0


class Spawner:
    def __init__(self):
        self.procs = []

    def __call__(self, cmd, **kw):
        p = FakeProc(cmd, **kw)
        self.procs.append(p)
        return p


def make(tmp_path, tools=True, platform="linux", **kw):
    which = (lambda n: f"/usr/bin/{n}") if tools else (lambda n: None)
    spawn = Spawner()
    rx = BluetoothReceiver(
        str(tmp_path / "bt"), which=which, popen=spawn, platform=platform,
        find_obexd_fn=(lambda: "/usr/libexec/bluetooth/obexd") if tools else (lambda: None), **kw)
    return rx, spawn


def go_live(rx):
    """Mark the receiver as running without starting the polling thread (tests poll by hand)."""
    rx.enabled = True
    rx.inbox.mkdir(parents=True, exist_ok=True)
    rx.sessions_dir.mkdir(parents=True, exist_ok=True)
    rx._stop.clear()
    rx._thread = threading.Thread(target=rx._stop.wait, daemon=True)
    rx._thread.start()


def jpeg(path, size=(40, 30)):
    Image.new("RGB", size, (10, 120, 200)).save(path, "JPEG")


def test_name_is_sanitised_because_it_ends_up_on_a_command_line():
    assert sanitize_name("Foto Kiosk 1") == "Foto Kiosk 1"
    assert sanitize_name("x\nquit; rm -rf /") == "xquit rm -rf"
    assert sanitize_name("") == "PhotoKiosk" and sanitize_name(None) == "PhotoKiosk"
    assert len(sanitize_name("a" * 99)) == 32


def test_diagnostics_explain_why_bluetooth_is_unavailable(tmp_path):
    assert make(tmp_path, platform="win32")[0].diagnostics()["mode"] == "unavailable"
    assert "bluez" in make(tmp_path, tools=False)[0].diagnostics()["reason"]
    rx, _ = make(tmp_path)
    assert rx.diagnostics()["mode"] == "managed"
    ext, _ = make(tmp_path, tools=False, external_inbox=str(tmp_path / "ext"))
    assert ext.diagnostics()["mode"] == "external"
    rx.which = None
    no_obex = BluetoothReceiver(str(tmp_path / "b2"), which=lambda n: "/x" if n == "bluetoothctl" else None,
                                platform="linux", find_obexd_fn=lambda: None)
    assert "bluez-obexd" in no_obex.diagnostics()["reason"]


def test_managed_mode_starts_agent_and_obexd_with_correct_commands(tmp_path, monkeypatch):
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    rx, spawn = make(tmp_path)
    rx.name = "Lab Kiosk"
    rx.enabled = True
    rx._supervise()
    agent, obexd = spawn.procs
    assert agent.cmd == ["/usr/bin/bluetoothctl"]
    assert agent.stdin.lines == ["power on", "system-alias Lab Kiosk", "pairable-timeout 0", "pairable on",
                                 "discoverable-timeout 0", "discoverable on", "agent NoInputNoOutput", "default-agent"]
    # a system service has no D-Bus session: obexd gets its own
    assert obexd.cmd == ["/usr/bin/dbus-run-session", "--", "/usr/libexec/bluetooth/obexd", "-n", "-a", "-r",
                         str(rx.inbox.resolve())]
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/x")
    rx2, spawn2 = make(tmp_path / "other")
    rx2._start_obexd()
    assert spawn2.procs[0].cmd[0] == "/usr/libexec/bluetooth/obexd"


def test_agent_answers_prompts_trusts_phones_and_learns_sender_name(tmp_path):
    rx, spawn = make(tmp_path)
    rx._start_agent()
    agent = spawn.procs[0]
    agent.stdin.lines.clear()
    mac = "AA:BB:CC:DD:EE:FF"
    rx._read_agent(type("P", (), {"stdout": [
        f"\x1b[0;94m[NEW]\x1b[0m Device {mac} Pixel 7\r\n".replace("\x1b[0;94m[NEW]\x1b[0m", "[NEW]"),
        f"[CHG] Device {mac} RSSI: -60\n",
        f"[agent] Authorize service 00001105-0000-1000-8000-00805f9b34fb (yes/no): \n",
        f"[CHG] Device {mac} Paired: yes\n",
        f"[CHG] Device {mac} Connected: yes\n",
    ]})())
    assert agent.stdin.lines == ["yes", f"trust {mac}"]
    assert rx._names[mac] == "Pixel 7" and rx._sender == "Pixel 7"


def test_dead_processes_are_restarted_with_backoff(tmp_path):
    rx, spawn = make(tmp_path)
    rx.enabled = True
    rx._supervise()
    assert len(spawn.procs) == 2
    spawn.procs[1].alive = False
    rx._supervise()
    assert len(spawn.procs) == 2                       # too soon: backoff
    rx._next_start["obexd"] = 0
    rx._supervise()
    assert len(spawn.procs) == 3 and spawn.procs[2].cmd[-3:-1] == ["-a", "-r"]


def test_rename_updates_the_running_adapter(tmp_path):
    rx, spawn = make(tmp_path)
    go_live(rx)
    rx._supervise()
    spawn.procs[0].stdin.lines.clear()
    rx.enabled = True
    rx.configure(True, "New Name")
    assert spawn.procs[0].stdin.lines == ["system-alias New Name"]


def test_received_photo_moves_into_the_customers_session_once_stable(tmp_path):
    rx, _ = make(tmp_path)
    go_live(rx)
    events = []
    rx.publish = lambda e, d: events.append(d)
    session = rx.open_session()["session"]
    jpeg(rx.inbox / "IMG_1.jpg")
    t0 = time.time()
    rx.scan_inbox(now=t0)
    assert [r["name"] for r in rx.status()["session"]["receiving"]] == ["IMG_1.jpg"]
    assert rx.status()["session"]["photos"] == []        # still arriving
    rx.scan_inbox(now=t0 + 0.5)
    assert rx.status()["session"]["photos"] == []        # not stable long enough
    rx.scan_inbox(now=t0 + 2.0)
    st = rx.status()["session"]
    assert [p["name"] for p in st["photos"]] == ["IMG_1.jpg"] and st["receiving"] == []
    assert (rx.sessions_dir / session["id"] / "IMG_1.jpg").exists() and not (rx.inbox / "IMG_1.jpg").exists()
    assert events and events[-1]["session"]["photos"]     # the browser is told live


def test_growing_file_is_not_taken_while_transfer_continues(tmp_path):
    rx, _ = make(tmp_path)
    go_live(rx)
    rx.open_session()
    f = rx.inbox / "big.jpg"
    f.write_bytes(b"\xff\xd8partial")
    t0 = time.time()
    rx.scan_inbox(now=t0)
    f.write_bytes(b"\xff\xd8partial-and-more-data")
    rx.scan_inbox(now=t0 + 2.0)                          # changed since the last look -> still receiving
    assert f.exists() and rx.status()["session"]["receiving"][0]["name"] == "big.jpg"


def test_junk_is_deleted_and_photos_without_a_session_are_dropped(tmp_path):
    rx, _ = make(tmp_path)
    go_live(rx)
    # no customer session open: nothing is kept (it would leak to the next customer)
    jpeg(rx.inbox / "stray.jpg")
    t0 = time.time()
    rx.scan_inbox(now=t0)
    rx.scan_inbox(now=t0 + 2)
    assert not (rx.inbox / "stray.jpg").exists() and list(rx.sessions_dir.iterdir()) == []

    rx.open_session()
    (rx.inbox / "virus.exe").write_bytes(b"MZ")
    (rx.inbox / "fake.jpg").write_text("not an image")
    (rx.inbox / "empty.jpg").write_bytes(b"")
    rx.scan_inbox(now=t0 + 10)
    rx.scan_inbox(now=t0 + 12)
    assert list(rx.inbox.iterdir()) == []
    assert rx.status()["session"]["photos"] == []


def test_same_name_photos_do_not_overwrite_each_other(tmp_path):
    rx, _ = make(tmp_path)
    go_live(rx)
    rx.open_session()
    t = time.time()
    for i in range(2):
        jpeg(rx.inbox / "image.jpg", size=(40 + i, 30))
        rx.scan_inbox(now=t)
        rx.scan_inbox(now=t + 2)
        t += 10
    assert sorted(p["name"] for p in rx.status()["session"]["photos"]) == ["image.jpg", "image_1.jpg"]


def test_sessions_are_isolated_and_cleaned_up(tmp_path):
    rx, _ = make(tmp_path)
    with pytest.raises(BluetoothUnavailable):
        rx.open_session()                                # not enabled/running
    go_live(rx)
    first = rx.open_session()["session"]
    jpeg(rx.inbox / "a.jpg")
    t = time.time()
    rx.scan_inbox(now=t)
    rx.scan_inbox(now=t + 2)
    first_dir = rx.sessions_dir / first["id"]
    assert (first_dir / "a.jpg").exists()
    second = rx.open_session()["session"]                # next customer
    assert not first_dir.exists() and second["photos"] == [] and second["id"] != first["id"]
    assert rx.session_root(first["id"]) is None and rx.session_root(second["id"]).is_dir()
    rx.close_session(second["id"])
    assert rx.status()["session"] is None and not (rx.sessions_dir / second["id"]).exists()


def test_stop_terminates_processes_and_wipes_sessions(tmp_path):
    rx, spawn = make(tmp_path)
    go_live(rx)
    rx._supervise()
    rx.open_session()
    rx.stop()
    assert all(not p.alive for p in spawn.procs) and rx.status()["session"] is None
