"""Measurement backend abstraction — the C1 fix from code review.

A `Backend` produces one CSV trace file for one replica. The interface
is intentionally narrow: the orchestrator (run_cell.py) doesn't care
whether the data comes from a real Nordic PPK2 over USB or a software
mock over TCP.

Two implementations:

* :class:`MockBackend` — wraps the existing ``mock_ppk2_server.py``
  subprocess + ``MockPPK2`` TCP client. Used during initial development
  (this is the current code path).

* :class:`PPK2Backend` — skeleton for the real Nordic PPK2 USB driver.
  Stub methods raise ``NotImplementedError`` with a clear message
  describing what each method must do. Filled in when PPK2 hardware
  arrives.

The orchestrator selects a backend via the IMPORT-SWITCH at the top
of ``scripts/run_cell.py`` (see comment there).

Design notes
------------
- ``measure_replica(...)`` is the only entry point. It returns
  ``MeasurementResult`` with success/failure, the path to the produced
  CSV, and a count of samples captured.
- The backend OWNS the lifecycle: starting the data source, draining
  samples, writing CSV, cleaning up. Failure modes (timeouts,
  disconnects) are surfaced via ``MeasurementResult.ok = False`` and
  ``error_message`` — NOT via exceptions. This keeps the orchestrator
  loop simple and resumable.
"""
from __future__ import annotations

import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class MeasurementResult:
    """Outcome of one ``measure_replica`` call.

    Attributes
    ----------
    ok
        True iff the CSV was produced and has at least one sample row.
    csv_path
        Where the CSV was written. Always set, even on failure (a partial
        file may exist for debugging).
    sample_count
        Number of sample rows in the CSV. Zero on failure or empty trace.
    duration_actual_s
        Wall-clock seconds the measurement actually ran. May differ from
        the requested duration if the backend self-terminated early.
    error_message
        Empty on success; human-readable diagnostic on failure.
    """
    ok: bool
    csv_path: Path
    sample_count: int
    duration_actual_s: float
    error_message: str = ""


class Backend(Protocol):
    """The narrow contract every backend must implement."""

    def measure_replica(
        self,
        csv_out: Path,
        duration_s: float,
        gpio_source: str,
        log_dir: Path,
    ) -> MeasurementResult:
        """Produce one CSV trace.

        Parameters
        ----------
        csv_out
            Where to write the canonical 4-column CSV.
        duration_s
            Requested measurement duration in seconds.
        gpio_source
            Mock backend uses this directly (``fake-script:idle``,
            ``fake-script:<path>``, ``real-lgpio``). The PPK2 backend
            currently ignores it — real hardware drives PA0/PA1/PA4
            via the STM32 firmware, not by a configured script.
        log_dir
            Backend-specific log files go here (server stdout, client
            connect attempts, USB enumeration output, etc.).

        Returns
        -------
        MeasurementResult
            Success/failure indicator + CSV path + diagnostics.
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
#  MockBackend
# ─────────────────────────────────────────────────────────────────────────────

def _find_free_port() -> int:
    """Bind to port 0 to grab an OS-assigned ephemeral port, then release.

    Race: between releasing and the subprocess binding, another process
    could claim the port. In practice the window is sub-millisecond and
    we've never seen a collision. If we ever do, switch to passing the
    listening socket via fd inheritance instead.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockBackend:
    """Backend driving ``mock_ppk2_server.py`` over a TCP loopback.

    Lifecycle per ``measure_replica``:

      1. ``mock_ppk2_server.py`` is spawned with ``--one-shot``, writing
         the CSV to ``csv_out``.
      2. ``MockPPK2`` client connects, issues ``start_measuring``.
      3. Background thread drains samples from the socket to prevent
         the kernel buffer from filling up at 25 kHz × 38 bytes.
      4. After ``duration_s``, ``stop_measuring`` + ``disconnect``.
      5. The server's ``--one-shot`` triggers self-termination on
         disconnect; we ``wait()`` it.

    All TCP-server-shaped logic lives here. Nothing in
    ``scripts/run_cell.py`` knows about ports or subprocesses.
    """

    def __init__(self, *, repo_root: Path | None = None) -> None:
        """
        Parameters
        ----------
        repo_root
            Optional override for the project root. Defaults to the
            grandparent directory of this file (typical layout:
            ``amore-energy-study/measurement/backends.py``).
        """
        if repo_root is None:
            repo_root = Path(__file__).resolve().parent.parent
        self.repo_root = repo_root
        self._ppk2_control = self.repo_root / "measurement" / "ppk2-control"

        # Lazy import: MockPPK2 isn't imported at module-load time so
        # the test suite can import backends.py without the measurement
        # stack pulled in.
        self._MockPPK2 = None

    def _get_client_class(self):
        if self._MockPPK2 is None:
            sys.path.insert(0, str(self._ppk2_control))
            from mock_ppk2_client import MockPPK2  # noqa: PLC0415
            self._MockPPK2 = MockPPK2
        return self._MockPPK2

    def measure_replica(
        self,
        csv_out: Path,
        duration_s: float,
        gpio_source: str,
        log_dir: Path,
    ) -> MeasurementResult:
        server_py = self._ppk2_control / "mock_ppk2_server.py"
        if not server_py.is_file():
            return MeasurementResult(
                ok=False, csv_path=csv_out, sample_count=0,
                duration_actual_s=0.0,
                error_message=f"mock_ppk2_server.py missing at {server_py}",
            )

        port = _find_free_port()
        server_log = log_dir / f"{csv_out.stem}.server.log"

        server_cmd = [
            sys.executable, "-u", str(server_py),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--gpio-source", gpio_source,
            "--csv-out", str(csv_out),
            "--duration", str(duration_s),
            "--pacing", "none",
            "--one-shot",
        ]

        t_start = time.time()
        slog = server_log.open("w")
        try:
            server_proc = subprocess.Popen(
                server_cmd,
                stdout=slog, stderr=subprocess.STDOUT,
                cwd=str(self.repo_root),
            )
        except (FileNotFoundError, OSError) as e:
            slog.close()
            return MeasurementResult(
                ok=False, csv_path=csv_out, sample_count=0,
                duration_actual_s=0.0,
                error_message=f"server spawn failed: {e}",
            )

        # Give the server a moment to bind before we try to connect
        time.sleep(0.6)
        if server_proc.poll() is not None:
            slog.flush(); slog.close()
            tail = server_log.read_text()[-600:]
            return MeasurementResult(
                ok=False, csv_path=csv_out, sample_count=0,
                duration_actual_s=time.time() - t_start,
                error_message=f"server died before client connected. log tail:\n{tail}",
            )

        sample_count_drained = 0
        err = ""
        try:
            MockPPK2 = self._get_client_class()
            client = MockPPK2(
                host="127.0.0.1", port=port,
                connect_retries=10, retry_delay_s=0.3,
            )
            client.connect()
            try:
                client.set_source_voltage(3300)
            except Exception:
                pass  # not all server versions implement it
            try:
                client.set_stop_mode(False)
            except Exception:
                pass
            client.start_measuring()

            # Background drainer: keeps the socket buffer from filling
            stop_drain = [False]
            drained = [0]
            def _drain():
                while not stop_drain[0]:
                    s = client.get_samples()
                    if s:
                        drained[0] += len(s)
                    time.sleep(0.1)
            t = threading.Thread(target=_drain, daemon=True)
            t.start()

            time.sleep(duration_s + 0.5)

            stop_drain[0] = True
            t.join(timeout=1.0)

            client.stop_measuring()
            drained[0] += len(client.get_samples())
            client.disconnect()
            sample_count_drained = drained[0]
        except Exception as e:
            err = f"client error: {e}"

        # Wait for one-shot server to exit cleanly
        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.send_signal(signal.SIGTERM)
            try:
                server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_proc.kill()

        slog.flush(); slog.close()

        # Verify CSV
        sample_count_csv = 0
        if csv_out.is_file():
            with csv_out.open() as f:
                f.readline()  # header
                # count remaining rows (cheap)
                for _ in f:
                    sample_count_csv += 1

        duration_actual = time.time() - t_start
        ok = (sample_count_csv > 0) and (not err)

        if not ok and not err:
            err = f"CSV had {sample_count_csv} sample rows; expected > 0"

        return MeasurementResult(
            ok=ok,
            csv_path=csv_out,
            sample_count=sample_count_csv,
            duration_actual_s=duration_actual,
            error_message=err,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  PPK2Backend — skeleton for the real Nordic PPK2 device
# ─────────────────────────────────────────────────────────────────────────────

class PPK2Backend:
    """Real Nordic PPK2 backend over USB serial.

    Lifecycle per ``measure_replica``:

      1. Open PPK2 over USB; read calibration modifiers.
      2. Configure source-meter mode at the requested voltage.
      3. Power on the DUT (STM32) via PPK2 VOUT.
      4. Start measuring; drain loop reads samples + digital channels.
      5. Write 4-column CSV: timestamp_us, current_uA, voltage_V, gpio_byte.
      6. After ``duration_s``, stop, power off, disconnect.

    Wiring assumption:
      - PPK2 VOUT -> STM32 3V3 (IDD jumper removed)
      - PPK2 GND  -> STM32 GND
      - PPK2 D0/D1/D2 (optional) -> STM32 PA0/PA1/PA4 GPIO triggers
        gpio_byte = D2<<2 | D1<<1 | D0

    Note on sample rate:
      The IRNAS ppk2-api 0.9.2 uses AVERAGE mode (~1 ksps output).
      AVG_NUM_SET is marked "no-firmware" so the rate is not changeable
      via this library. For ms-scale phases this is sufficient.

    The ``gpio_source`` parameter is ignored: real hardware reads
    digital channels directly via the PPK2's D0-D7 pins. Kept in the
    signature so the orchestrator can pass it without branching.
    """

    DEFAULT_VOLTAGE_MV = 3300
    DEFAULT_SETTLE_S = 0.5
    DRAIN_INTERVAL_S = 0.05  # 50 ms drain cadence
    SAMPLE_PERIOD_US = 1000  # 1 ms = 1 ksps (PPK2 average mode)

    def __init__(
        self,
        serial_port: str = "/dev/ttyACM1",
        *,
        voltage_mV: int = DEFAULT_VOLTAGE_MV,
        settle_s: float = DEFAULT_SETTLE_S,
    ) -> None:
        self.serial_port = serial_port
        self.voltage_mV = voltage_mV
        self.settle_s = settle_s

    def measure_replica(
        self,
        csv_out: Path,
        duration_s: float,
        gpio_source: str,
        log_dir: Path,
    ) -> MeasurementResult:
        # gpio_source is ignored - PPK2 reads digital channels directly
        del gpio_source  # silence unused-warning

        # Lazy import so module load doesn't require ppk2-api installed
        try:
            from ppk2_api.ppk2_api import PPK2_API
        except ImportError as e:
            return MeasurementResult(
                ok=False, csv_path=csv_out, sample_count=0,
                duration_actual_s=0.0,
                error_message=f"ppk2-api not installed: {e}",
            )

        import csv as csv_mod

        ppk2 = None
        csv_fp = None
        sample_count = 0
        t_start = time.time()
        err = ""
        ppk2_log = log_dir / f"{csv_out.stem}.ppk2.log"

        try:
            log_fp = ppk2_log.open("w")
            voltage_V = self.voltage_mV / 1000.0

            # 1. Connect
            log_fp.write(f"[{time.time():.3f}] Opening PPK2 at {self.serial_port}\n")
            ppk2 = PPK2_API(self.serial_port, timeout=2, write_timeout=2)
            ppk2.get_modifiers()
            log_fp.write(f"[{time.time():.3f}] get_modifiers OK\n")

            # 2. Configure
            ppk2.set_source_voltage(self.voltage_mV)
            ppk2.use_source_meter()
            log_fp.write(f"[{time.time():.3f}] Source mode @ {self.voltage_mV} mV\n")

            # 3. CSV setup
            csv_fp = csv_out.open("w", encoding="utf-8", newline="")
            writer = csv_mod.writer(csv_fp)
            writer.writerow(["timestamp_us", "current_uA", "voltage_V", "gpio_byte"])

            # 4. Power on, wait for STM32 to boot
            ppk2.toggle_DUT_power("ON")
            time.sleep(self.settle_s)
            log_fp.write(f"[{time.time():.3f}] DUT powered, settled\n")

            # 5. Start measuring
            ppk2.start_measuring()
            log_fp.write(f"[{time.time():.3f}] Measuring started\n")
            cumulative_us = 0
            t_end = time.time() + duration_s

            # 6. Drain loop
            while time.time() < t_end:
                time.sleep(self.DRAIN_INTERVAL_S)
                raw = ppk2.get_data()
                if not raw:
                    continue
                samples_uA, digital_raw = ppk2.get_samples(raw)
                if not samples_uA:
                    continue

                for i, current_uA in enumerate(samples_uA):
                    if i < len(digital_raw):
                        gpio_byte = digital_raw[i] & 0x07  # D0-D2 only
                    else:
                        gpio_byte = 0
                    writer.writerow([
                        cumulative_us + i * self.SAMPLE_PERIOD_US,
                        f"{current_uA:.3f}",
                        f"{voltage_V:.3f}",
                        gpio_byte,
                    ])
                    sample_count += 1
                cumulative_us += len(samples_uA) * self.SAMPLE_PERIOD_US

            # Flush CSV before stop
            csv_fp.flush()
            log_fp.write(f"[{time.time():.3f}] Drained {sample_count} samples\n")

            # 7. Stop
            ppk2.stop_measuring()
            ppk2.toggle_DUT_power("OFF")
            log_fp.write(f"[{time.time():.3f}] Stopped, DUT off\n")
            log_fp.close()

        except Exception as e:
            err = f"PPK2Backend error: {type(e).__name__}: {e}"
        finally:
            if csv_fp:
                try:
                    csv_fp.close()
                except Exception:
                    pass
            if ppk2:
                try:
                    ppk2.toggle_DUT_power("OFF")
                except Exception:
                    pass

        duration_actual = time.time() - t_start
        ok = (sample_count > 0) and (not err)

        if not ok and not err:
            err = f"PPK2 produced {sample_count} samples; expected > 0"

        return MeasurementResult(
            ok=ok,
            csv_path=csv_out,
            sample_count=sample_count,
            duration_actual_s=duration_actual,
            error_message=err,
        )
