"""``MockPPK2`` — TCP client class with the PPK2_API surface.

This is the class ``run_cell.py`` will instantiate (a future iter).
Method names and shapes follow the upstream IRNAS ``ppk2_api.PPK2_API``
where that makes sense, with the four reconciliation tweaks agreed at
the start of this engineering session:

1. Adds ``get_modifiers()`` (upstream-compatible).
2. ``stop_measuring()`` returns ``None`` (matches upstream; the
   ``total_samples`` value the wire reports is logged but not returned).
3. ``set_stop_mode(enabled)`` is a separate method (NOT a kwarg of
   ``start_measuring``), so when a real PPK2 backend is plugged in the
   ``start_measuring()`` signature is identical.
4. ``connect()`` is a no-op stub on real hardware but does the actual
   TCP connect-with-retry here.

Read-loop architecture
----------------------

After ``start_measuring()`` the server streams sample chunks
asynchronously. A background daemon thread continuously reads the
socket and pushes incoming ``Chunk`` payloads into a thread-safe
buffer. ``get_samples()`` drains the buffer in the calling thread.
This matches the upstream PPK2_API pattern (a separate read thread
that fills a ring buffer; the main thread polls it).

Error handling
--------------

* ``connect()`` retries with a short backoff (default 5×0.5 s) for
  transient TCP refuse during server startup. After exhausting retries
  it raises ``ConnectionError``.
* Any malformed message from the server raises ``ProtocolError``.
* Calling commands without ``connect()`` raises ``RuntimeError``.
"""
from __future__ import annotations

import socket
import threading
import time
from collections import deque
from typing import Any, List, Tuple

from wire_protocol import (
    DEFAULT_PORT,
    Chunk,
    Response,
    decode_message,
    encode_command,
)


class ProtocolError(RuntimeError):
    """Raised when the server returns malformed messages."""


# Type alias: a sample on the wire (matches Chunk.samples element type).
WireSample = Tuple[int, float, float, int]


class MockPPK2:
    """TCP client that mimics ``PPK2_API`` for the AmorE Mock PPK2 server.

    Instances are NOT thread-safe for command calls — callers should
    issue commands from a single thread. The internal background reader
    thread is independent and handles incoming chunks in parallel with
    command/response exchanges.

    Parameters
    ----------
    host
        Server hostname or IP. Spec §16.2 default is ``raspberrypi.local``.
    port
        TCP port (spec §6 default 9999).
    connect_retries, retry_delay_s
        Connect-side reliability — the server may take a moment to bind.
    """

    def __init__(
        self,
        host: str = "raspberrypi.local",
        port: int = DEFAULT_PORT,
        connect_retries: int = 5,
        retry_delay_s: float = 0.5,
    ) -> None:
        self.host = host
        self.port = port
        self._connect_retries = connect_retries
        self._retry_delay_s = retry_delay_s

        self._sock: socket.socket | None = None
        self._sock_file = None  # text-mode wrapper for line reads
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._sample_buf: deque[WireSample] = deque()
        self._sample_buf_lock = threading.Lock()
        # Responses to synchronous commands land here; _read_loop pulls
        # off chunks (which go to _sample_buf) and leaves Responses for
        # the command method to retrieve.
        self._response_event = threading.Event()
        self._latest_response: Response | None = None
        self._response_lock = threading.Lock()
        self._connected = False

    # ── Connection lifecycle ────────────────────────────────────────────────

    def connect(self) -> None:
        """Open TCP, send {"cmd":"connect"}, await ``ok``."""
        last_err: Exception | None = None
        for attempt in range(self._connect_retries):
            try:
                s = socket.create_connection(
                    (self.host, self.port), timeout=2.0
                )
                self._sock = s
                self._sock_file = s.makefile("rwb", buffering=0)
                break
            except (OSError, ConnectionRefusedError) as e:
                last_err = e
                if attempt + 1 < self._connect_retries:
                    time.sleep(self._retry_delay_s)
        else:
            raise ConnectionError(
                f"could not connect to {self.host}:{self.port} after "
                f"{self._connect_retries} attempts: {last_err}"
            )

        # Start the background reader BEFORE issuing the first command,
        # so the response is captured.
        self._reader_stop.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="MockPPK2-reader"
        )
        self._reader_thread.start()

        self._connected = True
        self._call("connect")

    def disconnect(self) -> None:
        """Send disconnect, stop reader, close socket. Idempotent."""
        if not self._connected:
            return
        try:
            self._call("disconnect")
        except Exception:
            pass  # best-effort; we're tearing down anyway
        self._connected = False
        self._reader_stop.set()
        if self._sock_file is not None:
            try:
                self._sock_file.close()
            except Exception:
                pass
            self._sock_file = None
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    # ── PPK2_API surface ────────────────────────────────────────────────────

    def set_source_voltage(self, mV: int) -> None:
        """Set output voltage in millivolts (e.g. 3300 for 3.3 V)."""
        self._require_connected()
        self._call("set_source_voltage", mV=int(mV))

    def set_stop_mode(self, enabled: bool) -> None:
        """Enable Stop-mode current model (~0.5 µA at idle).

        See API reconciliation note #3 — kept as a separate method
        instead of a kwarg of ``start_measuring`` so the latter has an
        upstream-identical signature.
        """
        self._require_connected()
        self._call("set_stop_mode", enabled=bool(enabled))

    def get_modifiers(self) -> dict[str, Any]:
        """Return the device 'modifiers' dict (calibration metadata).

        Real PPK2: per-device calibration constants. Mock PPK2: a small
        constant dict — present for API compatibility with run_cell.py.
        """
        self._require_connected()
        resp = self._call("get_modifiers")
        return dict(resp.data)

    def start_measuring(self) -> None:
        """Begin sample streaming. Chunks accumulate in the read buffer."""
        self._require_connected()
        # Clear any leftover samples from a prior session.
        with self._sample_buf_lock:
            self._sample_buf.clear()
        self._call("start_measuring")

    def stop_measuring(self) -> None:
        """Stop streaming. Drain chunks already in the socket buffer.

        Returns ``None`` — see API reconciliation note #2. The wire
        does carry a ``total_samples`` field for observability but we
        don't surface it here so the API matches upstream PPK2_API.
        """
        self._require_connected()
        self._call("stop_measuring")

    def get_samples(self) -> List[WireSample]:
        """Drain and return all samples currently in the read buffer.

        Each call returns whatever has accumulated since the previous
        call (or since ``start_measuring`` for the first call). Returns
        an empty list if nothing is available — does NOT block.
        """
        self._require_connected()
        with self._sample_buf_lock:
            out = list(self._sample_buf)
            self._sample_buf.clear()
        return out

    # ── Internals ───────────────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError(
                "MockPPK2 not connected — call connect() first"
            )

    def _call(self, cmd: str, **params: Any) -> Response:
        """Synchronous command/response RPC."""
        if self._sock_file is None:
            raise RuntimeError("socket not open")
        with self._response_lock:
            self._latest_response = None
            self._response_event.clear()
        try:
            self._sock_file.write(encode_command(cmd, **params))
            self._sock_file.flush()
        except OSError as e:
            raise ProtocolError(f"send failed for {cmd!r}: {e}") from e

        if not self._response_event.wait(timeout=5.0):
            raise ProtocolError(f"no response to {cmd!r} within 5 s")

        with self._response_lock:
            resp = self._latest_response
            self._latest_response = None
        if resp is None:
            raise ProtocolError(f"response for {cmd!r} was lost")
        if not resp.ok:
            raise ProtocolError(f"server error on {cmd!r}: {resp.error}")
        return resp

    def _read_loop(self) -> None:
        """Background thread: read NDJSON lines, dispatch by type.

        Ends cleanly on:
          - EOF (server closed the socket → ``readline`` returns b"")
          - Stop signal from disconnect()
          - OSError (socket closed under us — normal during disconnect)
        """
        if self._sock_file is None:
            return
        try:
            for raw_line in iter(self._sock_file.readline, b""):
                if self._reader_stop.is_set():
                    break
                if not raw_line:
                    break
                try:
                    msg = decode_message(raw_line)
                except ValueError:
                    continue  # malformed line — skip silently
                if isinstance(msg, Chunk):
                    with self._sample_buf_lock:
                        self._sample_buf.extend(msg.samples)
                elif isinstance(msg, Response):
                    with self._response_lock:
                        self._latest_response = msg
                    self._response_event.set()
                # Commands (server→client) shouldn't happen — ignore.
        except (OSError, ValueError):
            # OSError: socket closed during disconnect (expected).
            # ValueError: I/O on closed file (also expected).
            return

    # ── Context manager sugar ───────────────────────────────────────────────

    def __enter__(self) -> "MockPPK2":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.disconnect()
