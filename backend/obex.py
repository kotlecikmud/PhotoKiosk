"""A minimal OBEX *Object Push* server - what a phone speaks when you tap Share -> Bluetooth.

Windows has no ready-made service that receives such files: it hands an application a raw
RFCOMM socket and expects the application to speak OBEX itself. This module is that protocol
layer. It knows nothing about Bluetooth or the operating system - bytes in, bytes out - so it
is fully testable anywhere, and ``serve`` connects it to any byte stream.

Supported: CONNECT, PUT (multi-packet), ABORT, DISCONNECT. Everything else is answered with
"not implemented". A file is accepted or refused when its *first* packet arrives, so the phone
learns immediately that e.g. a non-picture or an oversized file is not wanted instead of
uploading it first.
"""
import asyncio
import os
import re
import struct
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

# --- requests ------------------------------------------------------------------------------
OP_CONNECT = 0x80
OP_DISCONNECT = 0x81
OP_PUT = 0x02
OP_GET = 0x03
OP_SETPATH = 0x05
OP_ABORT = 0xFF
FINAL = 0x80

# --- responses (the "final" bit is already set) ---------------------------------------------
CONTINUE = 0x90
SUCCESS = 0xA0
BAD_REQUEST = 0xC0
FORBIDDEN = 0xC3
TOO_LARGE = 0xCD
UNSUPPORTED_MEDIA = 0xCF
INTERNAL_ERROR = 0xD0
NOT_IMPLEMENTED = 0xD1

# --- headers -----------------------------------------------------------------------------------
H_NAME = 0x01
H_TYPE = 0x42
H_LENGTH = 0xC3
H_BODY = 0x48
H_END_OF_BODY = 0x49

OBEX_VERSION = 0x10
MAX_PACKET = 0xFFFF
DEFAULT_MAX_BYTES = 100 * 1024 * 1024
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class ObexError(Exception):
    """Malformed packet."""


def safe_filename(name: Optional[str]) -> str:
    """A harmless file name from whatever the phone sent ('' if nothing usable is left).

    Directory parts, control characters, characters Windows forbids, trailing dots/spaces and
    reserved device names (CON, NUL, COM1 ...) are removed or neutralised.
    """
    if not name:
        return ""
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r'[\x00-\x1f<>:"|?*]', "", base).strip().rstrip(". ")
    if not base or base in (".", ".."):
        return ""
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = stem[:100] or "photo"
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    return f"{stem}.{ext[:10]}" if ext else stem


def parse_headers(data: bytes) -> List[Tuple[int, object]]:
    """Decodes an OBEX header list. The top two bits of a header id give its encoding."""
    out: List[Tuple[int, object]] = []
    i, n = 0, len(data)
    while i < n:
        hid = data[i]
        enc = hid >> 6
        if enc in (0, 1):                       # text (UTF-16BE) / byte sequence, 2-byte length
            if i + 3 > n:
                raise ObexError("truncated header")
            length = struct.unpack(">H", data[i + 1:i + 3])[0]
            if length < 3 or i + length > n:
                raise ObexError("bad header length")
            raw = data[i + 3:i + length]
            value = raw.decode("utf-16-be", errors="replace").rstrip("\x00") if enc == 0 else bytes(raw)
            i += length
        elif enc == 2:                          # 1 byte
            if i + 2 > n:
                raise ObexError("truncated header")
            value, i = data[i + 1], i + 2
        else:                                   # 4 bytes
            if i + 5 > n:
                raise ObexError("truncated header")
            value, i = struct.unpack(">I", data[i + 1:i + 5])[0], i + 5
        out.append((hid, value))
    return out


def response(code: int, body: bytes = b"") -> bytes:
    return bytes([code]) + struct.pack(">H", 3 + len(body)) + body


class _Incoming:
    def __init__(self, name: str, declared: Optional[int], path: Path, handle):
        self.name, self.declared, self.path, self.handle, self.received = name, declared, path, handle, 0


class ObexServer:
    """One connection's worth of OBEX state. Feed it complete packets with :meth:`process`.

    ``accept(name, declared_size, content_type)`` returns ``None`` to take the file or an OBEX
    response code to refuse it. ``on_file(path, name)`` is called with the finished temporary file
    and must return True if it was stored (it may move the file); otherwise the phone is told the
    file was refused. ``on_progress(name, bytes_received, finished)`` is optional.
    """

    def __init__(
        self,
        tmp_dir,
        accept: Callable[[str, Optional[int], Optional[str]], Optional[int]],
        on_file: Callable[[Path, str], bool],
        on_progress: Optional[Callable[[str, int, bool], None]] = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self.tmp_dir = Path(tmp_dir)
        self.accept, self.on_file, self.on_progress = accept, on_file, on_progress
        self.max_bytes = max_bytes
        self._cur: Optional[_Incoming] = None

    # ------------------------------------------------------------------
    def process(self, packet: bytes) -> bytes:
        try:
            if len(packet) < 3 or struct.unpack(">H", packet[1:3])[0] != len(packet):
                raise ObexError("bad packet length")
            op = packet[0]
            if op == OP_CONNECT:
                return response(SUCCESS, bytes([OBEX_VERSION, 0]) + struct.pack(">H", MAX_PACKET))
            if op == OP_DISCONNECT:
                self.abort()
                return response(SUCCESS)
            if op == OP_ABORT:
                self.abort()
                return response(SUCCESS)
            if op & 0x7F == OP_PUT:
                return self._put(bool(op & FINAL), parse_headers(packet[3:]))
            return response(NOT_IMPLEMENTED)            # GET, SETPATH, vendor opcodes ...
        except ObexError:
            self.abort()
            return response(BAD_REQUEST)
        except Exception:
            self.abort()
            return response(INTERNAL_ERROR)

    def abort(self) -> None:
        """Forget a half-received file (ABORT, disconnect or a dropped connection)."""
        cur, self._cur = self._cur, None
        if cur:
            try:
                cur.handle.close()
                cur.path.unlink(missing_ok=True)
            finally:
                if self.on_progress:
                    self.on_progress(cur.name, cur.received, True)

    # ------------------------------------------------------------------
    def _put(self, final: bool, headers: List[Tuple[int, object]]) -> bytes:
        if self._cur is None:
            name, declared, ctype, has_body = "", None, None, False
            for hid, val in headers:
                if hid == H_NAME:
                    name = safe_filename(str(val))
                elif hid == H_LENGTH:
                    declared = int(val)
                elif hid == H_TYPE:
                    ctype = bytes(val).rstrip(b"\x00").decode("ascii", "replace")
                elif hid in (H_BODY, H_END_OF_BODY):
                    has_body = True
            if not has_body and final:
                return response(NOT_IMPLEMENTED)         # "PUT without body" means delete
            if not name:
                return response(BAD_REQUEST)
            if declared is not None and declared > self.max_bytes:
                return response(TOO_LARGE)
            refusal = self.accept(name, declared, ctype)
            if refusal is not None:
                return response(refusal)
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.tmp_dir, suffix=".part")
            self._cur = _Incoming(name, declared, Path(tmp), os.fdopen(fd, "wb"))

        cur = self._cur
        for hid, val in headers:
            if hid in (H_BODY, H_END_OF_BODY):
                cur.received += len(val)
                if cur.received > self.max_bytes:
                    self.abort()
                    return response(TOO_LARGE)
                cur.handle.write(val)
        if self.on_progress and not final:
            self.on_progress(cur.name, cur.received, False)
        if not final:
            return response(CONTINUE)

        cur.handle.close()
        self._cur = None
        try:
            stored = self.on_file(cur.path, cur.name)
        finally:
            cur.path.unlink(missing_ok=True)             # whatever on_file did not move away
            if self.on_progress:
                self.on_progress(cur.name, cur.received, True)
        return response(SUCCESS if stored else UNSUPPORTED_MEDIA)


# ----------------------------------------------------------------------------------------------
ReadExact = Callable[[int], Awaitable[bytes]]
Write = Callable[[bytes], Awaitable[None]]


async def serve(server: ObexServer, read_exact: ReadExact, write: Write, idle_timeout: float = 60.0) -> None:
    """Runs one connection: read a packet, answer it, repeat until the phone disconnects.

    ``read_exact(n)`` must return exactly n bytes or raise EOFError when the link is closed.
    """
    try:
        while True:
            head = await asyncio.wait_for(read_exact(3), idle_timeout)
            length = struct.unpack(">H", head[1:3])[0]
            if length < 3:
                break
            rest = await asyncio.wait_for(read_exact(length - 3), idle_timeout) if length > 3 else b""
            await write(server.process(head + rest))
            if head[0] == OP_DISCONNECT:
                break
    except (EOFError, ConnectionError, asyncio.TimeoutError, OSError):
        pass
    finally:
        server.abort()
