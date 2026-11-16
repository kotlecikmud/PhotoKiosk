"""Windows transport for receiving photos over Bluetooth.

Windows lets an application host an RFCOMM service (Microsoft documents this exact scenario as
"RFCOMM Scenario: Receive File as a Server", using ``RfcommServiceId.ObexObjectPush``) but hands
over only the raw socket - so ``backend.obex`` speaks the OBEX protocol on top of it.

The Windows Runtime is reached through the ``winrt-*`` packages (Python 3.11+). Importing them is
deferred until the transport starts, so this module can be imported anywhere. Nothing here needs
a Bluetooth pairing or a dialog: the socket allows an unauthenticated (encrypted) link, which is
what phones use for Object Push, and the PC is made discoverable while the service is advertised.
"""
import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from backend.obex import ObexServer, serve

logger = logging.getLogger(__name__)

RETRY_SECONDS = 10.0


class RfcommStream:
    """``read_exact`` / ``write`` on top of a WinRT DataReader + DataWriter pair.

    The reader is expected to be in *partial* mode (a load returns as soon as some bytes arrive).
    """

    def __init__(self, reader: Any, writer: Any):
        self.reader, self.writer = reader, writer

    async def read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            if self.reader.unconsumed_buffer_length == 0:
                loaded = await self.reader.load_async(max(n - len(buf), 1))
                if not loaded:
                    raise EOFError("Bluetooth link closed")
            take = min(self.reader.unconsumed_buffer_length, n - len(buf))
            chunk = bytearray(take)
            self.reader.read_bytes(chunk)
            buf += chunk
        return bytes(buf)

    async def write(self, data: bytes) -> None:
        self.writer.write_bytes(data)
        await self.writer.store_async()


class WindowsObexTransport:
    """Advertises the OBEX Object Push service and serves every phone that connects."""

    def __init__(
        self,
        tmp_dir,
        accept: Callable,
        on_file: Callable,
        on_progress: Optional[Callable] = None,
        make_discoverable: bool = True,
    ):
        self.tmp_dir = Path(tmp_dir)
        self.accept, self.on_file, self.on_progress = accept, on_file, on_progress
        self.make_discoverable = make_discoverable
        self.ready = False
        self.error = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._thread_main, name="bluetooth-windows", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        loop, ev = self._loop, self._stop_event
        if loop and ev and not loop.is_closed():
            loop.call_soon_threadsafe(ev.set)
        if self._thread:
            self._thread.join(timeout=5)
        self.ready = False

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()

    async def _main(self) -> None:
        self._stop_event = asyncio.Event()
        while not self._stop_event.is_set():
            try:
                await self._advertise_until_stopped()
            except Exception as exc:                     # no adapter, radio off, service refused ...
                self.ready = False
                self.error = f"{type(exc).__name__}: {exc}"
                logger.warning("Bluetooth service on Windows failed: %s", self.error)
            if not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), RETRY_SECONDS)
                except asyncio.TimeoutError:
                    pass

    async def _advertise_until_stopped(self) -> None:
        from winrt.windows.devices.bluetooth.rfcomm import RfcommServiceId, RfcommServiceProvider
        from winrt.windows.networking.sockets import SocketProtectionLevel, StreamSocketListener

        await self._check_adapter()
        provider = await RfcommServiceProvider.create_async(RfcommServiceId.obex_object_push)
        listener = StreamSocketListener()
        token = listener.add_connection_received(self._on_connection)     # called on a WinRT thread
        try:
            await listener.bind_service_name_async(
                provider.service_id.as_string(),
                SocketProtectionLevel.BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION)
            # second argument: also make the PC's radio discoverable while the service is advertised
            provider.start_advertising(listener, self.make_discoverable)
            self.ready, self.error = True, ""
            logger.info("Bluetooth Object Push service advertised")
            await self._stop_event.wait()
        finally:
            self.ready = False
            try:
                provider.stop_advertising()
            finally:
                listener.remove_connection_received(token)
                listener.close()

    @staticmethod
    async def _check_adapter() -> None:
        try:
            from winrt.windows.devices.bluetooth import BluetoothAdapter
        except ImportError:
            return                                       # optional check
        adapter = await BluetoothAdapter.get_default_async()
        if adapter is None:
            raise RuntimeError("no Bluetooth adapter found (or Bluetooth is switched off)")

    # ------------------------------------------------------------------
    def _on_connection(self, _listener, args) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._handle(args.socket), self._loop)

    async def _handle(self, socket: Any) -> None:
        from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions

        reader = DataReader(socket.input_stream)
        reader.input_stream_options = InputStreamOptions.PARTIAL
        writer = DataWriter(socket.output_stream)
        stream = RfcommStream(reader, writer)
        server = ObexServer(self.tmp_dir, self.accept, self.on_file, self.on_progress)
        try:
            await serve(server, stream.read_exact, stream.write)
        except Exception:
            logger.warning("Bluetooth connection failed", exc_info=True)
        finally:
            try:
                socket.close()
            except Exception:
                pass


# ----------------------------------------------------------------------------------------------
# Stand-alone check, to try the Windows side without the kiosk:
#     python -m backend.bluetooth_windows [folder]
# Make the PC visible, send a photo from a phone (Share > Bluetooth > this PC) and watch the log.
def _self_test() -> None:
    import argparse
    import shutil
    import time

    parser = argparse.ArgumentParser(description="Receive files over Bluetooth on this PC (self-test).")
    parser.add_argument("folder", nargs="?", default="bluetooth_received")
    args = parser.parse_args()
    out = Path(args.folder)
    out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    def accept(name, size, ctype):
        print(f"  incoming: {name!r} ({size} bytes, {ctype})")
        return None

    def on_file(path, name):
        shutil.move(str(path), str(out / name))
        print(f"  saved: {out / name}")
        return True

    transport = WindowsObexTransport(out / ".incoming", accept, on_file,
                                     lambda n, r, done: None if not done else print(f"  finished: {n} ({r} bytes)"))
    transport.start()
    print("Starting the Bluetooth Object Push service... (Ctrl+C to stop)")
    reported = None
    try:
        while True:
            state = (transport.ready, transport.error)
            if state != reported:
                reported = state
                print("READY - send a photo from your phone to this PC." if transport.ready
                      else f"not ready: {transport.error or 'starting...'}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        transport.stop()


if __name__ == "__main__":
    _self_test()
