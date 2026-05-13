"""End-to-end integration tests for mock_ppk2_server + MockPPK2 client.

Spins up the actual server in a background thread (binding to an
ephemeral port), connects with the real client, runs scenarios, and
verifies:
  * the wire round-trip works,
  * sample chunks arrive with the right shape and statistics,
  * the on-disk CSV matches the streamed samples,
  * stop_measuring cleanly tears the streamer down.

These are the iter 9 acceptance gates.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csv_format import read_samples  # noqa: E402
from mock_ppk2_client import MockPPK2  # noqa: E402
from mock_ppk2_server import run_server  # noqa: E402




def _drain_until_idle(client, max_seconds: float = 1.5,
                       quiet_cycles: int = 5) -> list:
    """Repeatedly drain samples from the client until the stream goes quiet.

    Pacing on the server side means data dribbles in over wall-clock
    time; we can't just sleep duration_s and call get_samples once
    (some chunks may still be in flight). Instead poll: every 50 ms
    drain samples, and stop after ``quiet_cycles`` consecutive empty
    drains.
    """
    out: list = []
    quiet = 0
    deadline = time.monotonic() + max_seconds
    while time.monotonic() < deadline:
        time.sleep(0.05)
        chunk = client.get_samples()
        if chunk:
            out.extend(chunk)
            quiet = 0
        else:
            quiet += 1
            if quiet >= quiet_cycles and out:
                break
    return out

# ---------------------------------------------------------------------------
# Fixture: server on an ephemeral port, in a background thread
# ---------------------------------------------------------------------------

class ServerHandle:
    """Wraps a server thread + the port it bound to.

    Avoids TOCTOU between port-pick and server-bind by passing port=0
    to ``run_server`` and learning the kernel-chosen port via the
    on_bound callback.
    """

    def __init__(
        self,
        gpio_spec: str = "fake-script:idle",
        duration_s: float = 0.3,
        csv_out: Optional[Path] = None,
        rng_seed: Optional[int] = 0xC0FFEE,
    ) -> None:
        bound_event = threading.Event()
        bound_port: list[int] = []

        def on_bound(p: int) -> None:
            bound_port.append(p)
            bound_event.set()

        self.thread = threading.Thread(
            target=run_server,
            kwargs=dict(
                host="127.0.0.1", port=0,
                gpio_spec=gpio_spec, csv_out=csv_out,
                duration_s=duration_s, rng_seed=rng_seed,
                one_shot=True, on_bound=on_bound,
            ),
            daemon=True,
            name="mock-ppk2-server",
        )
        self.thread.start()

        if not bound_event.wait(timeout=3.0):
            raise TimeoutError("server failed to bind within 3 s")
        self.port = bound_port[0]

    def join(self, timeout: float = 5.0) -> None:
        self.thread.join(timeout=timeout)


@pytest.fixture
def server() -> ServerHandle:
    h = ServerHandle()
    yield h
    h.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_idle_session_basic_shape(server: ServerHandle) -> None:
    """Connect, run idle for ~0.3 s, drain samples, verify shape.

    The server is paced so chunks arrive in real time. We use
    ``_drain_until_idle`` which polls every 50 ms and stops when
    several consecutive polls return nothing. Bounds are loose to
    accommodate scheduler jitter on a busy CI box.
    """
    with MockPPK2(host="127.0.0.1", port=server.port) as client:
        client.set_source_voltage(3300)
        client.set_stop_mode(False)
        mods = client.get_modifiers()
        assert mods["VDD"] == 3300

        client.start_measuring()
        all_samples = _drain_until_idle(client, max_seconds=2.0)
        client.stop_measuring()

    # Logical 0.3 s × 100 ksps = 30 000 samples. Loose bound: at least
    # half of expected (15 000) to confirm we're not getting just a
    # fragment, no upper bound (some over-shoot is fine).
    assert 15_000 <= len(all_samples) <= 35_000, (
        f"got {len(all_samples)} samples, expected ~30000"
    )
    # Idle phase → all gpio_byte=0
    assert all(s[3] == 0 for s in all_samples)
    # Voltage stable
    assert all(s[2] == 3.3 for s in all_samples)
    # Idle current ~50 mA
    mean_uA = sum(s[1] for s in all_samples) / len(all_samples)
    assert abs(mean_uA - 50_000.0) < 1000


def test_voltage_set_propagates_to_samples() -> None:
    """When client sets 3000 mV, samples carry voltage_V=3.0."""
    h = ServerHandle(duration_s=0.2)
    try:
        with MockPPK2(host="127.0.0.1", port=h.port) as client:
            client.set_source_voltage(3000)
            client.start_measuring()
            samples = _drain_until_idle(client, max_seconds=1.5)
            client.stop_measuring()
        assert len(samples) > 100
        assert all(s[2] == 3.0 for s in samples), (
            "voltage should be 3.0 V — server.set_source_voltage broken?"
        )
    finally:
        h.join(timeout=3.0)


def test_stop_mode_propagates(tmp_path: Path) -> None:
    """Stop-mode → samples should be ~0.5 µA, not ~50 mA."""
    h = ServerHandle(duration_s=0.2)
    try:
        with MockPPK2(host="127.0.0.1", port=h.port) as client:
            client.set_stop_mode(True)
            client.start_measuring()
            samples = _drain_until_idle(client, max_seconds=1.5)
            client.stop_measuring()
        assert len(samples) > 100
        mean_uA = sum(s[1] for s in samples) / len(samples)
        # Tight bound — stop-mode mean is 0.5 µA, much less than 1 µA
        assert mean_uA < 2.0, f"stop-mode mean too high: {mean_uA} µA"
    finally:
        h.join(timeout=3.0)


def test_csv_output_matches_streamed_samples(tmp_path: Path) -> None:
    """Server writes the same samples to CSV that it streams to client."""
    csv_path = tmp_path / "trace.csv"
    h = ServerHandle(duration_s=0.2, csv_out=csv_path)
    try:
        with MockPPK2(host="127.0.0.1", port=h.port) as client:
            client.set_source_voltage(3300)
            client.start_measuring()
            wire_samples = _drain_until_idle(client, max_seconds=1.5)
            client.stop_measuring()
    finally:
        h.join(timeout=3.0)

    assert csv_path.is_file(), "server should have written CSV"
    csv_samples = list(read_samples(csv_path))

    # Same count
    assert len(csv_samples) == len(wire_samples), (
        f"CSV has {len(csv_samples)} but wire delivered {len(wire_samples)}"
    )
    # Same content (rounded to 3dp on both paths so they match exactly)
    for ws, cs in zip(wire_samples, csv_samples):
        assert ws[0] == cs.timestamp_us
        assert abs(ws[1] - cs.current_uA) < 0.001  # 3dp rounding
        assert ws[2] == cs.voltage_V
        assert ws[3] == cs.gpio_byte


def test_mode_a_round_scenario_via_server(tmp_path: Path) -> None:
    """End-to-end Mode-A scenario: scenario file → server → client → CSV.

    Verifies the full pipeline through the wire. Phases identified by
    gpio_byte should have their canonical means.
    """
    scenario = tmp_path / "mode_a.txt"
    scenario.write_text(
        "100  1\n"   # PA0 high — Setup
        "150  0\n"   # all low (250 ms total, leaving 50 ms in 0.3s window)
        , encoding="utf-8",
    )
    csv_out = tmp_path / "out.csv"

    h = ServerHandle(
        gpio_spec=f"fake-script:{scenario}",
        duration_s=0.3,
        csv_out=csv_out,
    )
    try:
        with MockPPK2(host="127.0.0.1", port=h.port) as client:
            client.set_source_voltage(3300)
            client.start_measuring()
            samples = _drain_until_idle(client, max_seconds=1.5)
            client.stop_measuring()
    finally:
        h.join(timeout=3.0)

    # Partition by gpio_byte and check means.
    setup = [s[1] for s in samples if s[3] == 1]
    idle  = [s[1] for s in samples if s[3] == 0]
    assert len(setup) > 100, "setup window should have plenty of samples"
    assert len(idle) > 100,  "idle window should have plenty of samples"

    setup_mean = sum(setup) / len(setup)
    idle_mean  = sum(idle) / len(idle)
    assert abs(setup_mean - 85_000.0) < 500, f"setup mean {setup_mean}"
    assert abs(idle_mean  - 50_000.0) < 500, f"idle mean {idle_mean}"


def test_stop_measuring_actually_stops(tmp_path: Path) -> None:
    """After stop_measuring, the streamer halts and no NEW chunks appear.

    There's a small race window where chunks already on the wire arrive
    after stop_measuring returns. We drain those once, then assert the
    next get_samples() call yields nothing — the streamer truly stopped.
    """
    h = ServerHandle(duration_s=2.0)  # well over what we'll consume
    try:
        with MockPPK2(host="127.0.0.1", port=h.port) as client:
            client.start_measuring()
            time.sleep(0.15)
            mid_count = len(client.get_samples())
            client.stop_measuring()
            # Drain residual chunks already in flight, then settle.
            time.sleep(0.1)
            _ = client.get_samples()
            time.sleep(0.3)
            late_count = len(client.get_samples())
        assert mid_count > 0, "should have collected chunks before stop"
        # After settling, NO new chunks should appear.
        assert late_count == 0, (
            f"streamer kept running after stop+settle: late={late_count}"
        )
    finally:
        h.join(timeout=3.0)


def test_unknown_command_returns_error(server: ServerHandle) -> None:
    """A command the server doesn't recognize → ok=false response."""
    # We bypass MockPPK2 (which only emits known commands) and talk raw.
    s = socket.create_connection(("127.0.0.1", server.port), timeout=2.0)
    f = s.makefile("rwb", buffering=0)
    f.write(b'{"cmd":"connect"}\n')
    assert b'"ok":true' in f.readline()
    f.write(b'{"cmd":"flux_capacitor"}\n')
    resp = f.readline()
    assert b'"ok":false' in resp
    assert b"unknown command" in resp
    f.write(b'{"cmd":"disconnect"}\n')
    f.readline()
    s.close()
