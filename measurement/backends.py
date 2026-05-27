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
        # Bug #7 fix: track whether the client ever connected so we
        # don't wait 10s for the server's one-shot exit when nobody
        # actually opened the session.
        connected = False
        try:
            MockPPK2 = self._get_client_class()
            client = MockPPK2(
                host="127.0.0.1", port=port,
                connect_retries=10, retry_delay_s=0.3,
            )
            client.connect()
            connected = True
            # Bug #1 fix (2026-05-23): wrap the post-connect block in
            # try/finally so client.disconnect() runs on every exit
            # path, including exceptions thrown mid-measurement. The
            # previous layout only disconnected in the success branch,
            # which kept sockets open until GC and made the server's
            # one-shot wait time out (10s SIGTERM + 3s SIGKILL per
            # failed replica).
            try:
                try:
                    client.set_source_voltage(3300)
                except Exception:
                    pass  # not all server versions implement it
                try:
                    client.set_stop_mode(False)
                except Exception:
                    pass
                client.start_measuring()

                # Background drainer: keeps the socket buffer from filling.
                # Bug #8 fix: capture any exception so a silent thread death
                # (socket dropped, server crash, etc.) surfaces to the
                # caller instead of producing an "ok" result with a partial
                # sample count and no error message.
                stop_drain = [False]
                drained = [0]
                drain_error: list[Exception | None] = [None]
                def _drain():
                    try:
                        while not stop_drain[0]:
                            s = client.get_samples()
                            if s:
                                drained[0] += len(s)
                            time.sleep(0.1)
                    except Exception as e:
                        drain_error[0] = e
                t = threading.Thread(target=_drain, daemon=True)
                t.start()

                # Bug #6 fix (2026-05-23): poll for early failure
                # instead of sleeping blindly for the whole window.
                # The previous time.sleep(duration_s + 0.5) ate the
                # full duration even when server_proc died after one
                # second; in batch runs this multiplied wasted time
                # by the number of failing replicas.
                t_deadline = time.time() + duration_s + 0.5
                while time.time() < t_deadline:
                    if drain_error[0] is not None:
                        break
                    if server_proc.poll() is not None:
                        break
                    time.sleep(0.1)

                stop_drain[0] = True
                # Bug #2 fix (2026-05-23): the previous join(timeout=1.0)
                # could time out with the drain thread still inside a
                # blocking get_samples() call. The main thread would
                # then call stop_measuring() + get_samples() on the
                # SAME client concurrently with the still-alive drain
                # thread, producing silent socket/frame corruption.
                # Now: longer join timeout, and if the drain thread is
                # still alive we leave the client alone — fail loudly
                # instead of double-touching the socket.
                t.join(timeout=2.0)
                if t.is_alive():
                    # Bug #5 fix: surface the drain hang explicitly
                    # rather than continuing into a race that would
                    # only show as "client error: ..." (or worse,
                    # produce a CSV that looks fine but has corrupted
                    # samples).
                    err = (err or
                           "drain thread hung (still alive after 2s join); "
                           "not touching client further to avoid socket race")
                else:
                    client.stop_measuring()
                    drained[0] += len(client.get_samples())
                    sample_count_drained = drained[0]
                    # Bug #8: propagate drain error if any
                    if drain_error[0] is not None:
                        err = f"drain thread died: {drain_error[0]}"
            finally:
                # Bug #1 fix: disconnect runs no matter how the inner
                # block exits. Swallow disconnect errors — they are
                # secondary to whatever caused us to land here.
                try:
                    client.disconnect()
                except Exception:
                    pass
        except Exception as e:
            err = f"client error: {e}"

        # Wait for one-shot server to exit cleanly.
        # Bug #7 fix: if nobody connected, the server is still waiting
        # for accept() — there's nothing to wait for. Go straight to
        # SIGTERM with a short timeout instead of paying the full 10s.
        wait_timeout = 10 if connected else 2
        try:
            server_proc.wait(timeout=wait_timeout)
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
        keep_dut_powered: bool = False,
    ) -> None:
        self.serial_port = serial_port
        self.voltage_mV = voltage_mV
        self.settle_s = settle_s
        # keep_dut_powered=True: assume DUT is already powered by an
        # external PPK2 hold script (used by full_regression.sh). The
        # backend will NOT call set_source_voltage / use_source_meter /
        # toggle_DUT_power — it only opens the serial connection to
        # read samples in passive mode. This avoids the USB conflict
        # that would arise if two processes both opened the PPK2 for
        # active control.
        self.keep_dut_powered = keep_dut_powered

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
        log_fp = None  # Bug #6: declared up-front so finally can close it
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

            # 2. Configure (skip when keep_dut_powered — DUT is already
            # being held by an external PPK2 process; we only sample.)
            if not self.keep_dut_powered:
                ppk2.set_source_voltage(self.voltage_mV)
                ppk2.use_source_meter()
                log_fp.write(f"[{time.time():.3f}] Source mode @ {self.voltage_mV} mV\n")
            else:
                log_fp.write(f"[{time.time():.3f}] keep_dut_powered=True; skipping source-mode config\n")

            # 3. CSV setup
            csv_fp = csv_out.open("w", encoding="utf-8", newline="")
            writer = csv_mod.writer(csv_fp)
            writer.writerow(["timestamp_us", "current_uA", "voltage_V", "gpio_byte"])

            # 4. Power on, wait for STM32 to boot (skip when keep_dut_powered)
            if not self.keep_dut_powered:
                ppk2.toggle_DUT_power("ON")
                time.sleep(self.settle_s)
            else:
                log_fp.write(f"[{time.time():.3f}] keep_dut_powered=True; skipping power-on\n")
            log_fp.write(f"[{time.time():.3f}] DUT powered, settled\n")

            # 5. Start measuring
            ppk2.start_measuring()
            log_fp.write(f"[{time.time():.3f}] Measuring started\n")
            # Bug #7 fix: anchor sample timestamps to wall clock per
            # batch so dropped/missing samples cannot make CSV time
            # drift relative to real time. Within a batch we still
            # space samples by SAMPLE_PERIOD_US (PPK2's internal rate),
            # but each batch's *first* sample's time is taken from
            # time.monotonic() - t_measure_start. This keeps GPIO-event
            # alignment correct even if PPK2 occasionally delivers a
            # short batch.
            #
            # Bug #3 fix (2026-05-23): the wall-clock anchor on its own
            # does NOT guarantee CSV-row monotonicity across batches.
            # Example: batch A arrives at t=100ms with 50 samples →
            # anchored at [50..99]ms; batch B arrives at t=110ms with
            # 50 samples → anchored at [60..109]ms. Sample 0 of B
            # (60ms) precedes the last sample of A (99ms), producing a
            # non-monotonic CSV that parse_traces.py will now reject
            # outright (and that previously caused silently-negative
            # dt in energy integration). We track `last_written_us` and
            # clamp each new batch's t0 to at least one period past it.
            t_measure_start = time.monotonic()
            t_end = time.time() + duration_s
            last_written_us = -self.SAMPLE_PERIOD_US

            # 6. Drain loop
            while time.time() < t_end:
                time.sleep(self.DRAIN_INTERVAL_S)
                raw = ppk2.get_data()
                if not raw:
                    continue
                samples_uA, digital_raw = ppk2.get_samples(raw)
                if not samples_uA:
                    continue

                # Wall-clock anchor for this batch's first sample.
                batch_t0_us = int(
                    (time.monotonic() - t_measure_start) * 1_000_000
                )
                # Subtract len(samples_uA)*SAMPLE_PERIOD_US so the
                # FIRST sample of the batch sits at the *arrival* time
                # minus the batch's logical duration — i.e. the batch
                # is anchored at its true start, not at the moment
                # get_data() returned.
                batch_duration_us = len(samples_uA) * self.SAMPLE_PERIOD_US
                batch_t0_us = max(0, batch_t0_us - batch_duration_us)
                # Bug #3 fix: enforce monotonicity. If the wall-clock
                # anchor would put this batch earlier than (or equal
                # to) the last sample we already wrote, push it forward
                # by exactly one sample period. The resulting CSV is
                # then guaranteed strictly monotonic in timestamp_us.
                # If the PPK2 dropped a long gap, the clamp is a no-op
                # and the gap shows up naturally in measured_us vs
                # duration_us downstream (see parse_traces gap_us).
                batch_t0_us = max(
                    batch_t0_us, last_written_us + self.SAMPLE_PERIOD_US
                )

                for i, current_uA in enumerate(samples_uA):
                    if i < len(digital_raw):
                        gpio_byte = digital_raw[i] & 0x07  # D0-D2 only
                    else:
                        gpio_byte = 0
                    writer.writerow([
                        batch_t0_us + i * self.SAMPLE_PERIOD_US,
                        f"{current_uA:.3f}",
                        f"{voltage_V:.3f}",
                        gpio_byte,
                    ])
                    sample_count += 1
                # Update the high-water mark — the last row we just wrote.
                last_written_us = (
                    batch_t0_us + (len(samples_uA) - 1) * self.SAMPLE_PERIOD_US
                )

            # Flush CSV before stop
            csv_fp.flush()
            log_fp.write(f"[{time.time():.3f}] Drained {sample_count} samples\n")

            # 7. Stop
            ppk2.stop_measuring()
            if not self.keep_dut_powered:

                ppk2.toggle_DUT_power("OFF")

            else:

                pass  # keep_dut_powered: leave DUT powered for next replica
            log_fp.write(f"[{time.time():.3f}] Stopped, DUT off\n")
            # log_fp closed in finally — Bug #6 fix

        except Exception as e:
            err = f"PPK2Backend error: {type(e).__name__}: {e}"
        finally:
            if csv_fp:
                try:
                    csv_fp.close()
                except Exception:
                    pass
            if log_fp:
                # Bug #6 fix: guarantee log_fp closes even if an
                # exception fired anywhere between its open and the
                # in-try close. Previously a mid-try exception would
                # leak the file handle to GC.
                try:
                    log_fp.close()
                except Exception:
                    pass
            if ppk2:
                # Bug #4 fix (2026-05-23): if an exception fired between
                # start_measuring() and the explicit stop_measuring()
                # above, the PPK2 stayed in measuring state. The next
                # replica connecting to the same device could then
                # receive stale samples from the firmware buffer of
                # the previous (failed) measurement — a silent bias
                # source. Call stop_measuring() here defensively; it's
                # idempotent on a device already stopped.
                try:
                    ppk2.stop_measuring()
                except Exception:
                    pass
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
