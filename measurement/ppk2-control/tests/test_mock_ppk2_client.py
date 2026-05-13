"""Tests for MockPPK2 client against a stub TCP server.

The stub server runs in-process on a background thread. It speaks the
spec §6 wire protocol but doesn't run the real sample pipeline — it
only echoes commands and emits scripted chunks. This isolates the
client from any server bugs and verifies the API surface end-to-end:
connect/disconnect lifecycle, command/response RPC, async chunk
buffering, error propagation.

The full server (with actual pipeline) lands in iter 9.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_ppk2_client import MockPPK2, ProtocolError  # noqa: E402
from wire_protocol import (  # noqa: E402
    Command,
    decode_message,
    encode_chunk,
    encode_error,
    encode_ok,
)


# ---------------------------------------------------------------------------
# Stub server
# ---------------------------------------------------------------------------

class StubServer:
    """A tiny in-process TCP server that speaks the wire protocol.

    Behavior is configured per-test via the ``handler`` callback —
    given a Command, it returns either a bytes-encoded Response or a
    list of bytes-encoded messages (e.g. response + chunks). The stub
    thread runs once per connected client, then returns.
    """

    def __init__(self, handler: Callable[[Command], List[bytes]]) -> None:
        self._handler = handler
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port: int = self._sock.getsockname()[1]
        self._thread = threading.Thread(
            target=self._serve_one, daemon=True, name="StubServer"
        )
        self._stop = threading.Event()
        # Connections / received commands for inspection
        self.received_commands: List[Command] = []

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=1.0)

    def _serve_one(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            f = conn.makefile("rwb", buffering=0)
            for raw in iter(f.readline, b""):
                if self._stop.is_set() or not raw:
                    break
                try:
                    msg = decode_message(raw)
                except ValueError:
                    continue
                if not isinstance(msg, Command):
                    continue
                self.received_commands.append(msg)
                outputs = self._handler(msg)
                for out in outputs:
                    try:
                        f.write(out)
                        f.flush()
                    except OSError:
                        return
                if msg.cmd == "disconnect":
                    return


def _echo_handler(cmd: Command) -> List[bytes]:
    """Default handler: respond ok with echoed params for each command."""
    if cmd.cmd == "set_source_voltage":
        return [encode_ok({"mV": cmd.params.get("mV")})]
    if cmd.cmd == "set_stop_mode":
        return [encode_ok({"enabled": cmd.params.get("enabled")})]
    if cmd.cmd == "get_modifiers":
        return [encode_ok({"VDD": 3300, "calibration": "mock"})]
    if cmd.cmd == "stop_measuring":
        return [encode_ok({"total_samples": 0})]
    return [encode_ok({})]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def stub() -> "StubServer":
    s = StubServer(_echo_handler)
    s.start()
    yield s
    s.stop()


# ---------------------------------------------------------------------------
# Tests: connection lifecycle
# ---------------------------------------------------------------------------

def test_connect_and_disconnect(stub: StubServer) -> None:
    client = MockPPK2(host="127.0.0.1", port=stub.port)
    client.connect()
    assert client._connected is True
    client.disconnect()
    assert client._connected is False
    assert any(c.cmd == "connect" for c in stub.received_commands)
    assert any(c.cmd == "disconnect" for c in stub.received_commands)


def test_connect_retries_on_refused() -> None:
    """Server not listening yet → connect retries with backoff."""
    # Port 1 is privileged + nothing listens; reliably refuses on Linux.
    client = MockPPK2(
        host="127.0.0.1", port=1,
        connect_retries=2, retry_delay_s=0.05,
    )
    with pytest.raises(ConnectionError, match="2 attempts"):
        client.connect()


def test_context_manager(stub: StubServer) -> None:
    with MockPPK2(host="127.0.0.1", port=stub.port) as client:
        assert client._connected is True
    # After context exit, disconnected
    assert client._connected is False


def test_commands_require_connect(stub: StubServer) -> None:
    client = MockPPK2(host="127.0.0.1", port=stub.port)
    with pytest.raises(RuntimeError, match="not connected"):
        client.set_source_voltage(3300)


# ---------------------------------------------------------------------------
# Tests: command/response RPC
# ---------------------------------------------------------------------------

def test_set_source_voltage_propagates_value(stub: StubServer) -> None:
    with MockPPK2(host="127.0.0.1", port=stub.port) as client:
        client.set_source_voltage(3300)
    sv_cmds = [c for c in stub.received_commands if c.cmd == "set_source_voltage"]
    assert len(sv_cmds) == 1
    assert sv_cmds[0].params == {"mV": 3300}


def test_set_stop_mode_passes_bool(stub: StubServer) -> None:
    with MockPPK2(host="127.0.0.1", port=stub.port) as client:
        client.set_stop_mode(True)
        client.set_stop_mode(False)
    stop_cmds = [c for c in stub.received_commands if c.cmd == "set_stop_mode"]
    assert [c.params for c in stop_cmds] == [
        {"enabled": True},
        {"enabled": False},
    ]


def test_get_modifiers_returns_dict(stub: StubServer) -> None:
    with MockPPK2(host="127.0.0.1", port=stub.port) as client:
        mods = client.get_modifiers()
    assert mods == {"VDD": 3300, "calibration": "mock"}


def test_stop_measuring_returns_none(stub: StubServer) -> None:
    """API reconciliation #2 — return None to match upstream."""
    with MockPPK2(host="127.0.0.1", port=stub.port) as client:
        client.start_measuring()
        result = client.stop_measuring()
    assert result is None


def test_server_error_propagates(stub: StubServer) -> None:
    def err_handler(cmd: Command) -> List[bytes]:
        if cmd.cmd == "set_source_voltage":
            return [encode_error("voltage out of range")]
        return _echo_handler(cmd)

    s = StubServer(err_handler)
    s.start()
    try:
        with MockPPK2(host="127.0.0.1", port=s.port) as client:
            with pytest.raises(ProtocolError, match="voltage out of range"):
                client.set_source_voltage(99999)
    finally:
        s.stop()


# ---------------------------------------------------------------------------
# Tests: chunk streaming
# ---------------------------------------------------------------------------

def test_get_samples_drains_buffer(stub: StubServer) -> None:
    """Server emits chunks after start_measuring; client returns them."""
    samples_to_send = [
        (0,  50_000.0, 3.3, 0),
        (10, 50_500.0, 3.3, 0),
        (20, 85_000.0, 3.3, 1),
    ]

    def handler(cmd: Command) -> List[bytes]:
        if cmd.cmd == "start_measuring":
            return [encode_ok({}), encode_chunk(samples_to_send)]
        return _echo_handler(cmd)

    s = StubServer(handler)
    s.start()
    try:
        with MockPPK2(host="127.0.0.1", port=s.port) as client:
            client.start_measuring()
            # Tiny delay for the chunk to land on the client thread.
            time.sleep(0.1)
            got = client.get_samples()
            assert got == samples_to_send
            # Second call drains nothing.
            assert client.get_samples() == []
            client.stop_measuring()
    finally:
        s.stop()


def test_chunks_accumulate_across_multiple_messages(stub: StubServer) -> None:
    """Multiple chunks are concatenated in order in the buffer."""
    chunk1 = [(0, 50_000.0, 3.3, 0)]
    chunk2 = [(10, 85_000.0, 3.3, 1), (20, 88_000.0, 3.3, 4)]

    def handler(cmd: Command) -> List[bytes]:
        if cmd.cmd == "start_measuring":
            return [
                encode_ok({}),
                encode_chunk(chunk1),
                encode_chunk(chunk2),
            ]
        return _echo_handler(cmd)

    s = StubServer(handler)
    s.start()
    try:
        with MockPPK2(host="127.0.0.1", port=s.port) as client:
            client.start_measuring()
            time.sleep(0.1)
            got = client.get_samples()
            assert got == chunk1 + chunk2
            client.stop_measuring()
    finally:
        s.stop()


def test_start_measuring_clears_prior_samples(stub: StubServer) -> None:
    """A second start_measuring shouldn't return samples from the first."""
    chunk1 = [(0, 50_000.0, 3.3, 0)]
    chunk2 = [(100, 85_000.0, 3.3, 1)]

    sessions = [chunk1, chunk2]
    session_idx = [0]

    def handler(cmd: Command) -> List[bytes]:
        if cmd.cmd == "start_measuring":
            chunk = sessions[session_idx[0]]
            session_idx[0] += 1
            return [encode_ok({}), encode_chunk(chunk)]
        return _echo_handler(cmd)

    s = StubServer(handler)
    s.start()
    try:
        with MockPPK2(host="127.0.0.1", port=s.port) as client:
            client.start_measuring()
            time.sleep(0.1)
            client.stop_measuring()
            # Don't call get_samples here — leave chunk1 in the buffer.

            client.start_measuring()
            # The fresh start_measuring should have cleared chunk1.
            time.sleep(0.1)
            got = client.get_samples()
            assert got == chunk2
            client.stop_measuring()
    finally:
        s.stop()


# ---------------------------------------------------------------------------
# Tests: full PPK2_API surface (smoke)
# ---------------------------------------------------------------------------

def test_full_session_smoke(stub: StubServer) -> None:
    """A typical run_cell-ish session: configure, measure, drain, stop."""
    chunk = [(t, 50_000.0, 3.3, 0) for t in range(0, 100, 10)]

    def handler(cmd: Command) -> List[bytes]:
        if cmd.cmd == "start_measuring":
            return [encode_ok({}), encode_chunk(chunk)]
        return _echo_handler(cmd)

    s = StubServer(handler)
    s.start()
    try:
        client = MockPPK2(host="127.0.0.1", port=s.port)
        client.connect()
        client.set_source_voltage(3300)
        client.set_stop_mode(False)
        mods = client.get_modifiers()
        assert "VDD" in mods
        client.start_measuring()
        time.sleep(0.1)
        samples = client.get_samples()
        assert len(samples) == 10
        client.stop_measuring()
        client.disconnect()
    finally:
        s.stop()

    cmds = [c.cmd for c in s.received_commands]
    # Order should be: connect, set_source_voltage, set_stop_mode,
    # get_modifiers, start_measuring, stop_measuring, disconnect.
    assert cmds == [
        "connect", "set_source_voltage", "set_stop_mode", "get_modifiers",
        "start_measuring", "stop_measuring", "disconnect",
    ]
