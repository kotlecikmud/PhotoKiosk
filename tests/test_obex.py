"""OBEX Object Push server, the Windows stream glue, and the receiver's Windows mode.

A scripted client plays what an Android phone sends for Share -> Bluetooth, so the protocol is
verified without any Bluetooth hardware. (The WinRT socket calls themselves need Windows.)
"""
import asyncio
import io
import struct
import time
from pathlib import Path

import pytest
from PIL import Image

from backend import obex
from backend.bluetooth import BluetoothReceiver
from backend.bluetooth_windows import RfcommStream
from backend.obex import ObexServer, parse_headers, safe_filename, serve


# ---- packet builders (what a phone sends) -------------------------------------------------------
def h_text(hid, s):
    raw = (s + "\0").encode("utf-16-be")
    return bytes([hid]) + struct.pack(">H", 3 + len(raw)) + raw


def h_bytes(hid, b):
    return bytes([hid]) + struct.pack(">H", 3 + len(b)) + b


def h_u32(hid, v):
    return bytes([hid]) + struct.pack(">I", v)


def pkt(op, *parts):
    data = b"".join(parts)
    return bytes([op]) + struct.pack(">H", 3 + len(data)) + data


CONNECT = bytes([0x80]) + struct.pack(">H", 7) + bytes([0x10, 0x00]) + struct.pack(">H", 0x2000)
DISCONNECT = pkt(0x81)


def code(resp):
    return resp[0]


def jpeg_bytes(size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 80, 40)).save(buf, "JPEG")
    return buf.getvalue()


class Sink:
    def __init__(self, tmp_path, refuse=None, keep=True):
        self.files, self.progress, self.refuse, self.keep = [], [], refuse, keep
        self.tmp = tmp_path / "tmp"

    def accept(self, name, size, ctype):
        self.accepted = (name, size, ctype)
        return self.refuse

    def on_file(self, path, name):
        self.files.append((name, Path(path).read_bytes()))
        return self.keep

    def server(self, **kw):
        return ObexServer(self.tmp, self.accept, self.on_file, lambda n, r, f: self.progress.append((n, r, f)), **kw)


def leftovers(sink):
    return list(sink.tmp.glob("*")) if sink.tmp.exists() else []


# ---- protocol ----------------------------------------------------------------------------------------
def test_connect_negotiates_version_and_packet_size(tmp_path):
    resp = Sink(tmp_path).server().process(CONNECT)
    assert code(resp) == 0xA0 and resp[3] == 0x10 and struct.unpack(">H", resp[5:7])[0] == obex.MAX_PACKET
    assert struct.unpack(">H", resp[1:3])[0] == len(resp)


def test_multi_packet_photo_like_android_sends_it(tmp_path):
    sink, photo = Sink(tmp_path), jpeg_bytes((400, 300))
    srv = sink.server()
    assert code(srv.process(CONNECT)) == 0xA0
    a, b, c = photo[: len(photo) // 3], photo[len(photo) // 3: 2 * len(photo) // 3], photo[2 * len(photo) // 3:]
    first = pkt(0x02, h_text(0x01, "IMG_20260115_101500.jpg"), h_u32(0xC3, len(photo)),
                h_bytes(0x42, b"image/jpeg\0"), h_bytes(0x48, a))
    assert code(srv.process(first)) == 0x90                         # Continue
    assert code(srv.process(pkt(0x02, h_bytes(0x48, b)))) == 0x90
    assert code(srv.process(pkt(0x82, h_bytes(0x49, c)))) == 0xA0    # final + End-of-Body -> Success
    assert sink.files == [("IMG_20260115_101500.jpg", photo)]
    assert sink.accepted == ("IMG_20260115_101500.jpg", len(photo), "image/jpeg")
    assert code(srv.process(DISCONNECT)) == 0xA0
    assert leftovers(sink) == []
    assert sink.progress[-1] == ("IMG_20260115_101500.jpg", len(photo), True)
    assert [p[1] for p in sink.progress[:2]] == [len(a), len(a) + len(b)]


def test_small_photo_in_one_final_packet(tmp_path):
    sink = Sink(tmp_path)
    srv = sink.server()
    assert code(srv.process(pkt(0x82, h_text(0x01, "tiny.png"), h_bytes(0x49, b"data")))) == 0xA0
    assert sink.files == [("tiny.png", b"data")]


def test_two_photos_over_one_connection(tmp_path):
    sink = Sink(tmp_path)
    srv = sink.server()
    srv.process(CONNECT)
    for n in ("a.jpg", "b.jpg"):
        assert code(srv.process(pkt(0x82, h_text(0x01, n), h_bytes(0x49, n.encode())))) == 0xA0
    assert [f[0] for f in sink.files] == ["a.jpg", "b.jpg"]


def test_refusal_comes_with_the_first_packet_and_leaves_nothing_behind(tmp_path):
    sink = Sink(tmp_path, refuse=obex.FORBIDDEN)
    srv = sink.server()
    resp = srv.process(pkt(0x02, h_text(0x01, "x.jpg"), h_u32(0xC3, 5_000_000), h_bytes(0x48, b"xx")))
    assert code(resp) == 0xC3 and sink.files == [] and leftovers(sink) == []


def test_oversized_files_are_refused_up_front_or_cut_off(tmp_path):
    sink = Sink(tmp_path)
    srv = sink.server(max_bytes=100)
    assert code(srv.process(pkt(0x02, h_text(0x01, "big.jpg"), h_u32(0xC3, 101), h_bytes(0x48, b"x")))) == 0xCD
    # a phone that lies about the size is cut off as soon as the limit is crossed
    assert code(srv.process(pkt(0x02, h_text(0x01, "liar.jpg"), h_u32(0xC3, 10), h_bytes(0x48, b"x" * 60)))) == 0x90
    assert code(srv.process(pkt(0x02, h_bytes(0x48, b"x" * 60)))) == 0xCD
    assert sink.files == [] and leftovers(sink) == []


def test_file_that_is_not_stored_is_reported_and_cleaned_up(tmp_path):
    sink = Sink(tmp_path, keep=False)
    srv = sink.server()
    assert code(srv.process(pkt(0x82, h_text(0x01, "fake.jpg"), h_bytes(0x49, b"not an image")))) == 0xCF
    assert leftovers(sink) == []


@pytest.mark.parametrize("sent,expected", [
    ("..\\..\\Windows\\evil.jpg", "evil.jpg"), ("C:\\Users\\x\\a.jpg", "a.jpg"), ("../../etc/passwd.jpg", "passwd.jpg"),
    ("a/b/c.png", "c.png"), ("CON.jpg", "_CON.jpg"), ("nul", "_nul"), ("bad<>:name?.jpg", "badname.jpg"),
    ("zdjęcie z wakacji.jpg", "zdjęcie z wakacji.jpg"), ("  trailing.  ", "trailing"), ("", ""), ("..", ""), ("...", ""),
])
def test_file_names_are_neutralised(sent, expected):
    assert safe_filename(sent) == expected


def test_hostile_name_arrives_harmless_through_the_protocol(tmp_path):
    sink = Sink(tmp_path)
    srv = sink.server()
    assert code(srv.process(pkt(0x82, h_text(0x01, "..\\..\\startup\\evil.jpg"), h_bytes(0x49, b"x")))) == 0xA0
    assert sink.files[0][0] == "evil.jpg"
    assert code(srv.process(pkt(0x82, h_text(0x01, "..."), h_bytes(0x49, b"x")))) == 0xC0     # nothing usable left


def test_abort_and_disconnect_mid_transfer_discard_the_partial_file(tmp_path):
    for end in (pkt(0xFF), DISCONNECT):
        sink = Sink(tmp_path)
        srv = sink.server()
        srv.process(pkt(0x02, h_text(0x01, "half.jpg"), h_bytes(0x48, b"abc")))
        assert len(leftovers(sink)) == 1
        assert code(srv.process(end)) == 0xA0
        assert leftovers(sink) == [] and sink.files == [] and sink.progress[-1][2] is True


def test_malformed_and_unsupported_requests_never_crash(tmp_path):
    srv = Sink(tmp_path).server()
    assert code(srv.process(b"\x82\x00\x20abc")) == 0xC0                             # length field lies
    assert code(srv.process(b"\x82")) == 0xC0
    assert code(srv.process(pkt(0x82, bytes([0x01]) + struct.pack(">H", 999)))) == 0xC0  # header overruns packet
    assert code(srv.process(pkt(0x83, h_text(0x01, "x.vcf")))) == 0xD1                # GET
    assert code(srv.process(pkt(0x85))) == 0xD1                                       # SETPATH
    assert code(srv.process(pkt(0x82, h_text(0x01, "del.jpg")))) == 0xD1             # PUT w/o body = delete
    assert code(srv.process(pkt(0x42))) == 0xD1                                       # unknown opcode
    assert code(srv.process(pkt(0x82, h_bytes(0x49, b"x")))) == 0xC0                  # no name at all


def test_parse_headers_handles_all_four_encodings():
    data = h_text(0x01, "é.jpg") + h_bytes(0x42, b"image/jpeg\0") + bytes([0x93, 7]) + h_u32(0xC3, 123456)
    assert parse_headers(data) == [(0x01, "é.jpg"), (0x42, b"image/jpeg\0"), (0x93, 7), (0xC3, 123456)]


# ---- the connection loop -----------------------------------------------------------------------------------
class Wire:
    """In-memory byte pipe: what the phone sends, and what the server answers."""

    def __init__(self, incoming: bytes, hang_after=False):
        self.rx, self.tx, self.hang_after = io.BytesIO(incoming), [], hang_after

    async def read_exact(self, n):
        data = self.rx.read(n)
        if len(data) < n:
            if self.hang_after:
                await asyncio.sleep(3600)
            raise EOFError
        return data

    async def write(self, data):
        self.tx.append(data)


def test_serve_runs_a_whole_session_and_stops_at_disconnect(tmp_path):
    sink, photo = Sink(tmp_path), jpeg_bytes()
    conv = CONNECT + pkt(0x82, h_text(0x01, "p.jpg"), h_bytes(0x49, photo)) + DISCONNECT + b"ignored"
    wire = Wire(conv)
    asyncio.run(serve(sink.server(), wire.read_exact, wire.write))
    assert [code(r) for r in wire.tx] == [0xA0, 0xA0, 0xA0] and sink.files == [("p.jpg", photo)]


def test_serve_cleans_up_when_the_link_drops_mid_file(tmp_path):
    sink = Sink(tmp_path)
    wire = Wire(CONNECT + pkt(0x02, h_text(0x01, "cut.jpg"), h_bytes(0x48, b"abc")) + b"\x02\x00")   # then EOF
    asyncio.run(serve(sink.server(), wire.read_exact, wire.write))
    assert leftovers(sink) == [] and sink.files == []


def test_serve_gives_up_on_a_silent_phone(tmp_path):
    sink = Sink(tmp_path)
    wire = Wire(CONNECT + pkt(0x02, h_text(0x01, "slow.jpg"), h_bytes(0x48, b"abc")), hang_after=True)
    t0 = time.time()
    asyncio.run(serve(sink.server(), wire.read_exact, wire.write, idle_timeout=0.3))
    assert time.time() - t0 < 3 and leftovers(sink) == []


# ---- WinRT stream adapter (fake DataReader / DataWriter) ----------------------------------------------------
class FakeReader:
    """Delivers bytes in awkward pieces, like a Bluetooth link does."""

    def __init__(self, data, piece=2):
        self.data, self.piece, self.buffer = bytearray(data), piece, bytearray()

    @property
    def unconsumed_buffer_length(self):
        return len(self.buffer)

    async def load_async(self, count):
        n = min(count, self.piece, len(self.data))
        self.buffer += self.data[:n]
        del self.data[:n]
        return n

    def read_bytes(self, target):
        n = len(target)
        assert n <= len(self.buffer)
        target[:] = self.buffer[:n]
        del self.buffer[:n]


class FakeWriter:
    def __init__(self):
        self.sent = bytearray()
        self.stored = 0

    def write_bytes(self, b):
        self.sent += b

    async def store_async(self):
        self.stored += 1


def test_rfcomm_stream_assembles_exact_reads_from_small_pieces_and_reports_eof():
    async def run():
        stream = RfcommStream(FakeReader(b"abcdefghij", piece=3), FakeWriter())
        assert await stream.read_exact(4) == b"abcd"
        assert await stream.read_exact(0) == b""
        assert await stream.read_exact(5) == b"efghi"
        with pytest.raises(EOFError):
            await stream.read_exact(5)                      # only "j" left
        await stream.write(b"hello")
        return stream.writer
    w = asyncio.run(run())
    assert bytes(w.sent) == b"hello" and w.stored == 1


def test_a_complete_obex_conversation_over_the_stream_adapter(tmp_path):
    sink, photo = Sink(tmp_path), jpeg_bytes()
    conv = CONNECT + pkt(0x02, h_text(0x01, "x.jpg"), h_bytes(0x48, photo[:50])) + pkt(0x82, h_bytes(0x49, photo[50:])) + DISCONNECT
    stream = RfcommStream(FakeReader(conv, piece=7), FakeWriter())
    asyncio.run(serve(sink.server(), stream.read_exact, stream.write))
    assert sink.files == [("x.jpg", photo)] and stream.writer.stored == 4


# ---- the receiver in Windows mode -----------------------------------------------------------------------------
class FakeTransport:
    def __init__(self, **kw):
        self.kw, self.ready, self.error, self.started, self.stopped = kw, False, "", False, False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def win_receiver(tmp_path, winrt=True):
    made = []

    def factory(**kw):
        made.append(FakeTransport(**kw))
        return made[0]
    rx = BluetoothReceiver(str(tmp_path / "bt"), platform="win32", which=lambda n: None,
                           winrt_available=lambda: winrt, windows_transport_factory=factory)
    return rx, made


def test_windows_mode_needs_the_winrt_packages_and_says_so(tmp_path):
    rx, _ = win_receiver(tmp_path, winrt=False)
    d = rx.diagnostics()
    assert d["mode"] == "unavailable" and "pip install -r requirements.txt" in d["reason"]
    assert win_receiver(tmp_path)[0].diagnostics()["mode"] == "windows"


def test_windows_status_shows_the_pc_name_and_waits_for_the_service(tmp_path, monkeypatch):
    from backend import bluetooth
    monkeypatch.setattr(bluetooth._platform, "node", lambda: "KIOSK-PC")
    rx, made = win_receiver(tmp_path)
    try:
        rx.configure(True, "Ignored Alias")
        assert made[0].started and made[0].kw["accept"] == rx.accept_incoming
        st = rx.status()
        assert st["mode"] == "windows" and st["name"] == "KIOSK-PC" and st["available"] is False
        made[0].ready = True
        assert rx.status()["available"] is True
        made[0].ready, made[0].error = False, "RuntimeError: no Bluetooth adapter found"
        st = rx.status()
        assert st["available"] is False and "no Bluetooth adapter" in st["reason"]
    finally:
        rx.stop()
    assert made[0].stopped


def test_phone_to_session_through_the_windows_hooks(tmp_path):
    rx, made = win_receiver(tmp_path)
    try:
        rx.configure(True)
        made[0].ready = True
        srv = ObexServer(tmp_path / "incoming", rx.accept_incoming, rx.deliver_file, rx.report_progress)
        photo = jpeg_bytes()
        put = lambda name, body: srv.process(pkt(0x82, h_text(0x01, name), h_bytes(0x49, body)))

        assert code(put("early.jpg", photo)) == 0xC3                # no customer session yet: phone is told so
        session = rx.open_session()["session"]
        assert code(srv.process(pkt(0x02, h_text(0x01, "IMG_1.jpg"), h_u32(0xC3, len(photo)), h_bytes(0x48, photo[:40])))) == 0x90
        assert rx.status()["session"]["receiving"] == [{"name": "IMG_1.jpg", "size": 40}]   # live progress
        assert code(srv.process(pkt(0x82, h_bytes(0x49, photo[40:])))) == 0xA0
        st = rx.status()["session"]
        assert [p["name"] for p in st["photos"]] == ["IMG_1.jpg"] and st["receiving"] == []
        assert (rx.sessions_dir / session["id"] / "IMG_1.jpg").read_bytes() == photo

        assert code(put("notes.pdf", b"%PDF")) == 0xCF               # not a picture: refused before upload
        assert code(put("fake.jpg", b"definitely not a jpeg")) == 0xCF   # extension lies: rejected after
        assert len(rx.status()["session"]["photos"]) == 1
        assert code(put("IMG_1.jpg", photo)) == 0xA0                 # same name again -> kept as IMG_1_1.jpg
        assert sorted(p["name"] for p in rx.status()["session"]["photos"]) == ["IMG_1.jpg", "IMG_1_1.jpg"]

        rx.close_session()
        assert code(put("late.jpg", photo)) == 0xC3                  # session over: nothing is kept
        assert list((tmp_path / "incoming").glob("*")) == []
    finally:
        rx.stop()


# ---- the Windows transport itself, against a fake `winrt` package ------------------------------------------------
import sys
import threading
import types

from backend import bluetooth_windows as bw


def install_fake_winrt(monkeypatch, adapter_present=True, create_error=None):
    """A stand-in for the winrt-* packages, shaped like the real ones (checked against their stubs)."""
    log = types.SimpleNamespace(advertised=None, stopped=False, listener=None, closed=False, bound=None)

    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, m)
        return m

    class Provider:
        service_id = types.SimpleNamespace(as_string=lambda: "00001105-0000-1000-8000-00805f9b34fb")

        def start_advertising(self, listener, radio_discoverable):
            log.advertised = (listener, radio_discoverable)

        def stop_advertising(self):
            log.stopped = True

    class RfcommServiceProvider:
        @staticmethod
        async def create_async(service_id):
            if create_error:
                raise create_error
            log.service = service_id
            return Provider()

    class Listener:
        def __init__(self):
            self.handlers = []
            log.listener = self

        def add_connection_received(self, handler):
            self.handlers.append(handler)
            return "token"

        def remove_connection_received(self, token):
            self.handlers.clear()

        async def bind_service_name_async(self, name, level):
            log.bound = (name, level)

        def close(self):
            log.closed = True

    class FakeDataReader(FakeReader):
        def __init__(self, input_stream):
            super().__init__(input_stream.data, piece=9)

    class FakeDataWriter(FakeWriter):
        def __init__(self, output_stream):
            super().__init__()
            output_stream.writer = self

    class BluetoothAdapter:
        @staticmethod
        async def get_default_async():
            return object() if adapter_present else None

    for pkg in ("winrt", "winrt.windows", "winrt.windows.devices", "winrt.windows.networking", "winrt.windows.storage"):
        mod(pkg)
    mod("winrt.windows.devices.bluetooth", BluetoothAdapter=BluetoothAdapter)
    mod("winrt.windows.devices.bluetooth.rfcomm", RfcommServiceProvider=RfcommServiceProvider,
        RfcommServiceId=types.SimpleNamespace(obex_object_push="OBEX_OBJECT_PUSH"))
    mod("winrt.windows.networking.sockets", StreamSocketListener=Listener,
        SocketProtectionLevel=types.SimpleNamespace(BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION=3))
    mod("winrt.windows.storage.streams", DataReader=FakeDataReader, DataWriter=FakeDataWriter,
        InputStreamOptions=types.SimpleNamespace(PARTIAL=1))
    return log


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_windows_transport_advertises_serves_a_phone_and_shuts_down(tmp_path, monkeypatch):
    log = install_fake_winrt(monkeypatch)
    got = []
    t = bw.WindowsObexTransport(tmp_path / "inc", lambda n, s, c: None,
                                lambda p, n: got.append((n, Path(p).read_bytes())) or True)
    t.start()
    assert wait_for(lambda: t.ready)
    assert log.service == "OBEX_OBJECT_PUSH"                         # the standard Object Push service
    assert log.bound[0].startswith("00001105") and log.bound[1] == 3  # encrypted, no pairing PIN required
    assert log.advertised[0] is log.listener and log.advertised[1] is True   # radio made discoverable

    photo = jpeg_bytes()
    conv = CONNECT + pkt(0x02, h_text(0x01, "IMG_1.jpg"), h_bytes(0x48, photo[:30])) + pkt(0x82, h_bytes(0x49, photo[30:])) + DISCONNECT
    socket = types.SimpleNamespace(input_stream=types.SimpleNamespace(data=conv),
                                   output_stream=types.SimpleNamespace(writer=None), closed=False)
    socket.close = lambda: setattr(socket, "closed", True)
    log.listener.handlers[0](None, types.SimpleNamespace(socket=socket))      # a phone connects (WinRT thread)
    assert wait_for(lambda: socket.closed)
    assert got == [("IMG_1.jpg", photo)]
    assert socket.output_stream.writer.sent[0] == 0xA0               # first answer: CONNECT -> Success
    assert socket.output_stream.writer.stored == 4                   # connect, continue, final, disconnect answered

    t.stop()
    assert log.stopped and log.closed and not t.ready and not log.listener.handlers


def test_windows_transport_reports_why_it_cannot_start_and_keeps_retrying(tmp_path, monkeypatch):
    install_fake_winrt(monkeypatch, adapter_present=False)
    monkeypatch.setattr(bw, "RETRY_SECONDS", 0.2)
    t = bw.WindowsObexTransport(tmp_path / "inc", lambda *a: None, lambda *a: True)
    t.start()
    assert wait_for(lambda: "no Bluetooth adapter" in t.error) and not t.ready
    install_fake_winrt(monkeypatch, adapter_present=True)           # the user switches Bluetooth on
    assert wait_for(lambda: t.ready and t.error == "")
    t.stop()


def test_windows_transport_surfaces_windows_refusing_the_service(tmp_path, monkeypatch):
    install_fake_winrt(monkeypatch, create_error=PermissionError("service id is reserved"))
    t = bw.WindowsObexTransport(tmp_path / "inc", lambda *a: None, lambda *a: True)
    t.start()
    assert wait_for(lambda: "service id is reserved" in t.error) and not t.ready
    t.stop()
