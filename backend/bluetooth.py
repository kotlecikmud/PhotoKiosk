"""Receive photos over Bluetooth (OBEX Object Push) - e.g. Android: Share -> Bluetooth -> kiosk.

How it works (Linux / BlueZ, the kiosk's target platform)
---------------------------------------------------------
* ``bluetoothctl`` is kept running as a pairing agent ("NoInputNoOutput" = Just Works), the
  adapter is made discoverable under the kiosk's name, and every paired phone is trusted so the
  transfer does not stall on an authorisation prompt nobody can answer.
* BlueZ's ``obexd`` (package ``bluez-obexd``) accepts pushed files into an *inbox* folder.
* A poller waits until a file stops growing, checks it really is a picture, and moves it into the
  folder of the **current customer session**. Photos that arrive while no session is open - and
  everything that is not a picture - are deleted, so one customer never sees another's photos.

On **Windows** there is no ready-made receiver, so the kiosk hosts the OBEX Object Push service itself
(``backend.bluetooth_windows``: Windows RFCOMM sockets + our own OBEX server in ``backend.obex``). The
phone then sees the PC under its computer name. Files are accepted or refused as soon as the phone starts
sending, and a refused file (no customer session, not a picture, too big) is reported to the phone.

``KIOSK_BLUETOOTH_INBOX`` selects *external* mode: some other receiver writes into that folder and
this class only watches it (useful on systems where BlueZ is not available).
"""
import importlib.util
import logging
import os
import platform as _platform
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PIL import Image

from backend.library import SUPPORTED_EXTENSIONS
from backend.obex import FORBIDDEN, TOO_LARGE, UNSUPPORTED_MEDIA
from backend.sources import encode_photo_id

logger = logging.getLogger(__name__)

OBEXD_CANDIDATES = ("/usr/libexec/bluetooth/obexd", "/usr/lib/bluetooth/obexd", "/usr/lib/bluez/obexd")
DEFAULT_NAME = "PhotoKiosk"
STABLE_SECONDS = 1.5          # a file must not change for this long before it counts as received
POLL_SECONDS = 0.7
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_SESSION_PHOTOS = 500
SESSION_TTL_SECONDS = 3 * 3600
RESTART_BACKOFF_SECONDS = 5.0

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\r")
_MAC = r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})"
_PROMPTS = ("authorize service", "request authorization", "accept pairing", "confirm passkey",
            "request confirmation", "(yes/no)")


def _winrt_installed() -> bool:
    """True if the Windows Runtime bindings for RFCOMM sockets are installed."""
    try:
        return importlib.util.find_spec("winrt.windows.devices.bluetooth.rfcomm") is not None
    except (ImportError, ValueError):
        return False


def _default_transport_factory(**kwargs):
    from backend.bluetooth_windows import WindowsObexTransport
    return WindowsObexTransport(**kwargs)


class BluetoothUnavailable(Exception):
    """Bluetooth receiving is disabled or cannot run on this machine."""


def sanitize_name(name: Optional[str]) -> str:
    """Adapter name as shown on phones. Becomes part of a command line: letters, digits, space . _ -"""
    cleaned = re.sub(r"[^\w .\-]", "", name or "", flags=re.UNICODE).strip()[:32]
    return cleaned or DEFAULT_NAME


def find_obexd(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    for path in OBEXD_CANDIDATES:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return which("obexd")


class BluetoothReceiver:
    def __init__(
        self,
        base_dir: str = "storage/bluetooth",
        publish: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        which: Callable[[str], Optional[str]] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        platform: str = sys.platform,
        external_inbox: Optional[str] = None,
        find_obexd_fn: Callable[[], Optional[str]] = None,
        winrt_available: Optional[Callable[[], bool]] = None,
        windows_transport_factory: Optional[Callable[..., Any]] = None,
    ):
        self.base = Path(base_dir)
        self.sessions_dir = self.base / "sessions"
        self.external_inbox = external_inbox or os.environ.get("KIOSK_BLUETOOTH_INBOX") or None
        self.inbox = Path(self.external_inbox) if self.external_inbox else self.base / "inbox"
        self.publish = publish or (lambda event, data: None)
        self._which = which
        self._popen = popen
        self._platform = platform
        self._find_obexd = find_obexd_fn or (lambda: find_obexd(which))
        self._winrt_available = winrt_available or _winrt_installed
        self._transport_factory = windows_transport_factory or _default_transport_factory
        self._win: Any = None                         # Windows transport while running
        self._incoming: Dict[str, Dict[str, Any]] = {}  # files being received right now (Windows)
        self._progress_emit_at = 0.0

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.enabled = False
        self.name = DEFAULT_NAME
        self._active: Optional[str] = None            # session id
        self._seen: Dict[str, Any] = {}               # inbox file -> (signature, since)
        self._receiving: List[Dict[str, Any]] = []
        self._sender = ""
        self._names: Dict[str, str] = {}              # MAC -> device name
        self._procs: Dict[str, Any] = {}              # "agent" / "obexd"
        self._next_start: Dict[str, float] = {}
        self.last_error = ""
        self._last_signature: Any = None

    # ------------------------------------------------------------------
    # Capability
    # ------------------------------------------------------------------
    def diagnostics(self) -> Dict[str, Any]:
        """What this machine can do; shown to the administrator."""
        bluetoothctl = self._which("bluetoothctl")
        obexd = self._find_obexd()
        info: Dict[str, Any] = {
            "bluetoothctl": bluetoothctl, "obexd": obexd,
            "dbus_run_session": self._which("dbus-run-session"),
            "mode": "unavailable", "reason": "",
        }
        if self.external_inbox:
            info["mode"] = "external"
        elif self._platform == "win32":
            if self._winrt_available():
                info["mode"] = "windows"
            else:
                info["reason"] = ("The Windows Bluetooth components are missing. "
                                  "Run: pip install -r requirements.txt (needs Python 3.11 or newer).")
        elif self._platform != "linux":
            info["reason"] = "Bluetooth receiving needs Linux with BlueZ, or Windows (or KIOSK_BLUETOOTH_INBOX)."
        elif not bluetoothctl:
            info["reason"] = "Package 'bluez' is not installed (bluetoothctl not found)."
        elif not obexd:
            info["reason"] = "Package 'bluez-obexd' is not installed (obexd not found)."
        else:
            info["mode"] = "managed"
        return info

    @property
    def mode(self) -> str:
        return self.diagnostics()["mode"]

    # ------------------------------------------------------------------
    # Configuration / lifecycle
    # ------------------------------------------------------------------
    def configure(self, enabled: bool, name: Optional[str] = None) -> None:
        """Applies the administrator's settings: starts, stops or renames the receiver."""
        new_name = sanitize_name(name)
        with self._lock:
            renamed = new_name != self.name
            self.name = new_name
            was = self.enabled
            self.enabled = bool(enabled)
        if self.enabled and not was:
            self.start()
        elif was and not self.enabled:
            self.stop()
        elif self.enabled and renamed:
            self._send_agent(f"system-alias {self.name}")
        self._emit(force=True)

    def start(self) -> None:
        if self.mode == "unavailable":
            logger.warning("Bluetooth receiver not started: %s", self.diagnostics()["reason"])
            return
        if self._thread and self._thread.is_alive():
            return
        self.inbox.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.sessions_dir, ignore_errors=True)       # nothing survives a restart
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        if self.mode == "windows":
            self._win = self._transport_factory(
                tmp_dir=self.base / "incoming", accept=self.accept_incoming,
                on_file=self.deliver_file, on_progress=self.report_progress)
            self._win.start()
        self._thread = threading.Thread(target=self._run, name="bluetooth", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None
        self._stop_processes()
        if self._win is not None:
            try:
                self._win.stop()
            finally:
                self._win = None
        self._incoming.clear()
        self.close_session()

    # ------------------------------------------------------------------
    # Sessions (one customer at a time)
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def open_session(self) -> Dict[str, Any]:
        with self._lock:
            if not (self.enabled and self.running):
                raise BluetoothUnavailable("Bluetooth is not available.")
            self._drop_session_dir(self._active)
            self._active = secrets.token_hex(6)
            (self.sessions_dir / self._active).mkdir(parents=True, exist_ok=True)
            self._sender = ""
            # whatever is lying in the inbox belongs to nobody
            for f in self._inbox_files():
                f.unlink(missing_ok=True)
            self._seen.clear()
        self._emit(force=True)
        return self.status()

    def close_session(self, session_id: Optional[str] = None) -> None:
        with self._lock:
            if session_id is not None and session_id != self._active:
                return
            self._drop_session_dir(self._active)
            self._active = None
            self._sender = ""
        self._emit(force=True)

    def session_root(self, session_id: str) -> Optional[Path]:
        with self._lock:
            if session_id and session_id == self._active:
                return self.sessions_dir / session_id
        return None

    def _drop_session_dir(self, session_id: Optional[str]) -> None:
        if session_id:
            shutil.rmtree(self.sessions_dir / session_id, ignore_errors=True)

    # ------------------------------------------------------------------
    # Status for the browser
    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        diag = self.diagnostics()
        with self._lock:
            active = self._active
            session = None
            if active:
                root = self.sessions_dir / active
                photos = []
                if root.is_dir():
                    for f in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime):
                        if f.is_file():
                            photos.append({"id": encode_photo_id(f.name), "name": f.name,
                                           "size": f.stat().st_size, "mtime": f.stat().st_mtime})
                session = {"id": active, "source_id": f"bt-{active}", "photos": photos,
                           "receiving": list(self._receiving) + list(self._incoming.values()),
                           "sender": self._sender}
            ready = self.enabled and self.running and diag["mode"] != "unavailable"
            reason = diag["reason"] or self.last_error
            name = self.name
            if diag["mode"] == "windows":
                # Windows shows the PC under its computer name; an application cannot change that
                name = _platform.node() or self.name
                ready = ready and bool(self._win is not None and self._win.ready)
                reason = reason or (self._win.error if self._win is not None else "")
            return {
                "enabled": self.enabled, "available": ready,
                "mode": diag["mode"], "reason": reason, "name": name, "session": session,
            }

    def _emit(self, force: bool = False) -> None:
        st = self.status()
        sess = st["session"]
        sig = (st["enabled"], st["available"], st["name"],
               sess and (sess["id"], len(sess["photos"]), tuple((r["name"], r["size"]) for r in sess["receiving"]), sess["sender"]))
        if force or sig != self._last_signature:
            self._last_signature = sig
            self.publish("bluetooth", st)

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------
    def _run(self) -> None:
        last_sweep = time.time()
        while not self._stop.is_set():
            try:
                if self.mode == "managed":
                    self._supervise()
                self.scan_inbox()
                if time.time() - last_sweep > 300:
                    last_sweep = time.time()
                    self._sweep_old_sessions()
            except Exception:
                logger.warning("bluetooth worker error", exc_info=True)
            self._stop.wait(POLL_SECONDS)

    def _inbox_files(self) -> List[Path]:
        try:
            return [f for f in self.inbox.iterdir() if f.is_file()]
        except OSError:
            return []

    def scan_inbox(self, now: Optional[float] = None) -> None:
        """One poll of the inbox: accept files that finished arriving, report those still arriving."""
        now = time.time() if now is None else now
        receiving: List[Dict[str, Any]] = []
        files = self._inbox_files()
        names = {f.name for f in files}
        for gone in [k for k in self._seen if k not in names]:
            del self._seen[gone]
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            sig = (st.st_size, st.st_mtime_ns)
            entry = self._seen.get(f.name)
            if entry is None or entry[0] != sig:
                self._seen[f.name] = (sig, now)
                receiving.append({"name": f.name, "size": st.st_size})
            elif now - entry[1] < STABLE_SECONDS:
                receiving.append({"name": f.name, "size": st.st_size})
            else:
                self._accept(f)
                self._seen.pop(f.name, None)
        with self._lock:
            self._receiving = receiving
        self._emit()

    def _accept(self, f: Path) -> None:
        """A file finished arriving in the inbox (Linux): keep it or delete it."""
        try:
            self._store(f, f.name)
        finally:
            f.unlink(missing_ok=True)

    def _store(self, src: Path, name: str) -> bool:
        """Moves ``src`` into the active session if it is a real picture. True if kept."""
        with self._lock:
            active = self._active
            dest_dir = self.sessions_dir / active if active else None
            size = src.stat().st_size
            ok = (dest_dir is not None and Path(name).suffix.lower() in SUPPORTED_EXTENSIONS
                  and 0 < size <= MAX_FILE_BYTES and dest_dir.is_dir()
                  and len(list(dest_dir.iterdir())) < MAX_SESSION_PHOTOS)
        if not ok:
            return False
        try:
            with Image.open(src) as img:
                img.verify()                         # really a picture, not a renamed file
        except Exception:
            return False
        dest = dest_dir / name
        n = 1
        while dest.exists():
            dest = dest_dir / f"{Path(name).stem}_{n}{Path(name).suffix}"
            n += 1
        shutil.move(str(src), str(dest))
        return True

    # -- hooks used by the Windows OBEX server -----------------------------------------------
    def accept_incoming(self, name: str, size: Optional[int], content_type: Optional[str]) -> Optional[int]:
        """Decides at the *start* of a transfer; returns an OBEX refusal code or None to accept."""
        with self._lock:
            active = self._active
            full = bool(active) and (self.sessions_dir / active).is_dir() and \
                len(list((self.sessions_dir / active).iterdir())) >= MAX_SESSION_PHOTOS
        if not active or full:
            return FORBIDDEN                         # nobody is receiving right now
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            return UNSUPPORTED_MEDIA
        if size is not None and size > MAX_FILE_BYTES:
            return TOO_LARGE
        return None

    def deliver_file(self, path: Path, name: str) -> bool:
        stored = self._store(Path(path), name)
        self._emit()
        return stored

    def report_progress(self, name: str, received: int, finished: bool) -> None:
        with self._lock:
            if finished:
                self._incoming.pop(name, None)
            else:
                self._incoming[name] = {"name": name, "size": received}
        now = time.time()
        if finished or now - self._progress_emit_at > 0.3:      # do not flood the browser
            self._progress_emit_at = now
            self._emit()

    def _sweep_old_sessions(self) -> None:
        cutoff = time.time() - SESSION_TTL_SECONDS
        for d in self.sessions_dir.glob("*"):
            if d.name != self._active and d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------
    # BlueZ processes (managed mode)
    # ------------------------------------------------------------------
    def _supervise(self) -> None:
        now = time.time()
        for key, start in (("agent", self._start_agent), ("obexd", self._start_obexd)):
            proc = self._procs.get(key)
            if proc is not None and proc.poll() is None:
                continue
            if now < self._next_start.get(key, 0.0):
                continue
            self._next_start[key] = now + RESTART_BACKOFF_SECONDS
            try:
                start()
                self.last_error = ""
            except Exception as exc:
                self.last_error = f"{key}: {exc}"
                logger.warning("could not start %s: %s", key, exc)

    def _start_agent(self) -> None:
        proc = self._popen([self._which("bluetoothctl") or "bluetoothctl"], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self._procs["agent"] = proc
        threading.Thread(target=self._read_agent, args=(proc,), name="bt-agent", daemon=True).start()
        for line in ("power on", f"system-alias {self.name}", "pairable-timeout 0", "pairable on",
                     "discoverable-timeout 0", "discoverable on", "agent NoInputNoOutput", "default-agent"):
            self._send_agent(line)

    def _start_obexd(self) -> None:
        obexd = self._find_obexd()
        if not obexd:
            raise RuntimeError("obexd not found")
        cmd = [obexd, "-n", "-a", "-r", str(self.inbox.resolve())]
        # obexd is a per-user D-Bus service; a system service has no session bus, so give it one
        runner = self._which("dbus-run-session")
        if runner and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            cmd = [runner, "--"] + cmd
        self._procs["obexd"] = self._popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _send_agent(self, line: str) -> None:
        proc = self._procs.get("agent")
        if proc is None or proc.poll() is not None or "\n" in line:
            return
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def _read_agent(self, proc: Any) -> None:
        """Answers pairing / authorisation prompts, trusts paired phones, remembers their names."""
        for raw in proc.stdout:
            line = _ANSI.sub("", raw).strip()
            low = line.lower()
            if any(p in low for p in _PROMPTS):          # e.g. "[agent] Authorize service ... (yes/no):"
                self._send_agent("yes")
            m = re.search(r"Device " + _MAC + r" Paired: yes", line)
            if m:
                self._send_agent(f"trust {m.group(1).upper()}")
            m = re.search(r"\[(?:NEW|CHG)\] Device " + _MAC + r" (.+)$", line)
            if m:
                rest = m.group(2)
                if rest.startswith(("Name: ", "Alias: ")):
                    self._names[m.group(1).upper()] = rest.split(": ", 1)[1]
                elif line.startswith("[NEW]") and not re.match(r"[A-Za-z]\w*: ", rest):
                    self._names[m.group(1).upper()] = rest
            m = re.search(r"Device " + _MAC + r" Connected: yes", line)
            if m:
                self._sender = self._names.get(m.group(1).upper(), "")

    def _stop_processes(self) -> None:
        for proc in list(self._procs.values()):
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
        self._procs.clear()
        self._next_start.clear()
