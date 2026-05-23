"""``mock_ppk2_server.py`` — TCP server that mimics PPK2 over the wire.

Composes the iter 7 sample pipeline (GPIO source → interpolate →
assemble) with the iter 8 wire protocol to deliver a streaming PPK2
emulator. Spec §1: outputs live on the Pi — the server writes the
canonical 4-column CSV to ``--csv-out`` while simultaneously streaming
chunks to the connected client.

Architecture
------------

Per connected client:

* The **handler thread** (the thread that called ``serve_one``) runs
  the request loop: read NDJSON line, decode Command, mutate session
  state, send Response. Synchronous and simple.
* The **streamer thread** is spawned on ``start_measuring`` and torn
  down on ``stop_measuring`` (or disconnect / exception). It owns the
  sample pipeline and a CSV file handle, sends chunks to the client at
  paced intervals, and writes those same samples to the CSV in lockstep.

A ``threading.Lock`` on ``socket.sendall`` serializes writes from the
two threads — the handler writes Responses, the streamer writes
Chunks; both go down the same TCP stream.

GPIO sources
------------

Selected at server start via ``--gpio-source`` (matches the Protocol
in ``gpio_source.py``):

* ``fake-script:<path>``  — replay a scenario file (iter 9 default).
* ``fake-script:idle``    — synthetic empty-events source (no
                            transitions, ever — produces pure idle
                            current).
* ``real-lgpio``          — capture from BCM 17/27/22 via lgpio
                            callbacks. Deferred to iter 11.

Real-time pacing
----------------

The pipeline produces samples at logical 100 ksps (10 µs spacing). The
streamer doesn't dump them as fast as Python can — it paces emission
to roughly real time so a 1 s scenario takes ~1 s to stream. Pacing
granularity is one chunk: each chunk represents ``CHUNK_DURATION_US``
of logical time, and the streamer sleeps the rest of that wall-clock
window before sending the next chunk. This matches what a real PPK2
USB driver does.

CLI usage
---------

::

    python3 mock_ppk2_server.py \\
        --port 9999 \\
        --gpio-source fake-script:scenarios/mode_a_round.txt \\
        --csv-out /home/pi/amore-energy-study/measurement/traces/cell_0001.csv \\
        --duration 1.0
"""
from __future__ import annotations

import argparse
import logging
import random
import socket
import sys
import threading
import time
from pathlib import Path
from typing import IO, Callable, Iterator, Optional, Tuple

# Local modules
from csv_format import CSV_HEADER, Sample
from gpio_source import FileGPIOSource, ScriptedGPIOSource
from interpolator import DEFAULT_SAMPLE_PERIOD_US, interpolate_to_fixed_rate
from sample_assembler import assemble_samples
from wire_protocol import (
    DEFAULT_PORT,
    Command,
    decode_message,
    encode_chunk,
    encode_error,
    encode_ok,
)

log = logging.getLogger("mock_ppk2_server")


# ---------------------------------------------------------------------------
# Pacing / chunking constants
# ---------------------------------------------------------------------------

#: Each emitted chunk represents this many µs of logical sample time.
#: 10 ms / 10 µs = 1000 samples per chunk. Tunable; smaller chunks =
#: lower latency but more wire/CSV overhead.
CHUNK_DURATION_US: int = 10_000

#: Default scenario duration for `fake-script:idle` and any source that
#: doesn't have an inherent end. Override with --duration.
DEFAULT_DURATION_S: float = 1.0


# ---------------------------------------------------------------------------
# GPIO source resolution
# ---------------------------------------------------------------------------

def resolve_gpio_source(spec: str):
    """Parse the --gpio-source spec and return a GPIOSource instance.

    Currently supports:
      ``fake-script:<path>`` — FileGPIOSource on a scenario file.
      ``fake-script:idle``   — empty ScriptedGPIOSource (pure idle).
      ``real-lgpio``         — RAISES NotImplementedError (iter 11).
    """
    if spec == "real-lgpio":
        raise NotImplementedError(
            "real-lgpio source is deferred to iter 11"
        )
    if not spec.startswith("fake-script:"):
        raise ValueError(
            f"unknown --gpio-source: {spec!r}. "
            "Expected 'fake-script:<path>' or 'fake-script:idle'."
        )
    arg = spec[len("fake-script:") :]
    if arg == "idle":
        return ScriptedGPIOSource([])
    return FileGPIOSource(arg)


# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

class ServerSession:
    """Per-connection state and helpers.

    Owns the socket, the streamer thread (when active), the CSV file
    handle, and a write lock that serializes ``sendall`` calls between
    the handler and streamer threads.
    """

    def __init__(
        self,
        conn: socket.socket,
        gpio_spec: str,
        csv_out: Optional[Path],
        duration_s: float,
        rng_seed: Optional[int],
        pacing: str = "real-time",
        sample_period_us: int = DEFAULT_SAMPLE_PERIOD_US,
    ) -> None:
        self.conn = conn
        self.gpio_spec = gpio_spec
        self.csv_out = csv_out
        self.duration_s = duration_s
        self.rng_seed = rng_seed
        # "real-time" : sleep between chunks so wall clock ≈ logical time
        #               (default; matches how a real PPK2 paces over USB).
        # "none"       : emit chunks as fast as the producer can — useful
        #               on slow Pi hardware where real-time pacing can't
        #               keep up with 100 ksps logical rate, or for fast
        #               offline/CI runs that don't care about wall time.
        self.pacing = pacing
        # Logical sample period in µs. 10 = 100 ksps (real PPK2). 40 =
        # 25 ksps (default for the mock — Pi 3B can't sustain 100 ksps
        # in pure Python). Spec §5.3 + PRD §5.4.4 require ≥10 ksps; we
        # ship with 4× headroom over that minimum.
        self.sample_period_us = sample_period_us

        # Wire-protocol writes: handler does Responses, streamer does
        # Chunks. Both sides must serialize through this lock.
        self._write_lock = threading.Lock()

        # Session config (mutated by commands before start_measuring)
        self.voltage_mV: int = 3300
        self.stop_mode: bool = False

        # Streamer state
        self._streamer_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._csv_fp: Optional[IO[str]] = None
        self._csv_lock = threading.Lock()
        self.total_samples_sent: int = 0
        self._streaming = False

    # ── Wire helpers ────────────────────────────────────────────────────────

    def send_line(self, line: bytes) -> None:
        """Thread-safe write of a single NDJSON line to the client."""
        with self._write_lock:
            try:
                self.conn.sendall(line)
            except OSError as e:
                log.warning("send failed: %s", e)
                self._stop_event.set()

    # ── Streamer ────────────────────────────────────────────────────────────

    def start_streaming(self) -> None:
        """Spawn the streamer thread. No-op if already streaming."""
        if self._streaming:
            return
        self._stop_event.clear()
        self.total_samples_sent = 0

        # Open CSV (if requested) BEFORE starting the streamer so any
        # filesystem error surfaces synchronously.
        if self.csv_out is not None:
            self.csv_out.parent.mkdir(parents=True, exist_ok=True)
            self._csv_fp = self.csv_out.open("w", encoding="utf-8")
            self._csv_fp.write(CSV_HEADER + "\n")
            self._csv_fp.flush()

        self._streaming = True
        self._streamer_thread = threading.Thread(
            target=self._streamer_loop, daemon=True, name="MockPPK2-stream"
        )
        self._streamer_thread.start()

    def stop_streaming(self) -> None:
        """Signal the streamer to stop, join it, close CSV.

        Safe to call multiple times. The streamer's _streamer_loop also
        runs the same cleanup in its finally block (see Bug #1/#2
        fixes), so this method is idempotent — closing an already-closed
        fp is a no-op via the None check.
        """
        if not self._streaming and self._streamer_thread is None:
            return
        self._stop_event.set()
        if self._streamer_thread is not None:
            self._streamer_thread.join(timeout=2.0)
            self._streamer_thread = None
        with self._csv_lock:
            if self._csv_fp is not None:
                try:
                    self._csv_fp.close()
                except Exception:
                    pass
                self._csv_fp = None
        self._streaming = False

    def _streamer_loop(self) -> None:
        """Stream sample chunks to the wire + CSV, paced near real time.

        Guarantees on every exit path (natural end / external stop /
        exception): self._streaming is reset to False, the CSV file
        handle is closed, and self._stop_event is set so any subsequent
        stop_streaming() call returns immediately. Failures inside the
        loop also send an encode_error to the client.
        """
        try:
            try:
                source = resolve_gpio_source(self.gpio_spec)
            except Exception as e:
                log.error("could not resolve GPIO source: %s", e)
                try:
                    self.send_line(encode_error(f"gpio source failed: {e}"))
                except Exception:
                    pass
                return

            end_time_us = int(self.duration_s * 1_000_000)
            rng = (
                random.Random(self.rng_seed)
                if self.rng_seed is not None else None
            )
            gpio_samples = interpolate_to_fixed_rate(
                source.events(),
                end_time_us=end_time_us,
                sample_period_us=self.sample_period_us,
            )
            sample_iter: Iterator[Sample] = assemble_samples(
                gpio_samples,
                voltage_mV=self.voltage_mV,
                stop_mode=self.stop_mode,
                rng=rng,
            )

            chunk_buf: list[Tuple[int, float, float, int]] = []
            next_chunk_boundary = CHUNK_DURATION_US
            wall_t0 = time.monotonic()

            for s in sample_iter:
                if self._stop_event.is_set():
                    break
                chunk_buf.append(
                    (s.timestamp_us, s.current_uA, s.voltage_V, s.gpio_byte)
                )

                if s.timestamp_us + self.sample_period_us >= next_chunk_boundary:
                    self._flush_chunk(chunk_buf)
                    chunk_buf = []

                    if self.pacing == "real-time":
                        # Sleep until wall clock catches up to logical time.
                        target_wall = wall_t0 + (next_chunk_boundary / 1_000_000.0)
                        slack = target_wall - time.monotonic()
                        if slack > 0:
                            self._stop_event.wait(timeout=slack)
                    # When pacing == "none" we just continue immediately.
                    next_chunk_boundary += CHUNK_DURATION_US

            # Final partial chunk
            if chunk_buf and not self._stop_event.is_set():
                self._flush_chunk(chunk_buf)
        except Exception as e:
            # Bug #2 fix: any uncaught exception inside the loop would
            # otherwise just kill the thread and leave self._streaming
            # True forever. Tell the client what happened, then fall
            # through to the finally cleanup.
            log.exception("streamer crashed")
            try:
                self.send_line(encode_error(f"streamer crashed: {e}"))
            except Exception:
                pass
        finally:
            # Bug #1 fix: guarantee state reset on every exit path
            # (natural end-of-scenario, external stop, or exception).
            # Without this, a second start_measuring() would be a silent
            # no-op because of the "if self._streaming: return" gate.
            self._stop_event.set()
            with self._csv_lock:
                if self._csv_fp is not None:
                    try:
                        self._csv_fp.close()
                    except Exception:
                        pass
                    self._csv_fp = None
            self._streaming = False

    def _flush_chunk(
        self, samples: list[Tuple[int, float, float, int]]
    ) -> None:
        """Send a chunk on the wire AND append rows to the CSV."""
        if not samples:
            return
        # Wire
        self.send_line(encode_chunk(samples))
        self.total_samples_sent += len(samples)
        # CSV
        if self._csv_fp is not None:
            with self._csv_lock:
                for ts, i, v, gb in samples:
                    self._csv_fp.write(f"{ts},{i:.3f},{v:.3f},{gb}\n")
                self._csv_fp.flush()


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

def handle_command(session: ServerSession, cmd: Command) -> bool:
    """Apply a command to the session. Return False to disconnect."""
    if cmd.cmd == "connect":
        session.send_line(encode_ok({"hwver": "mock-amore-v1"}))
        return True

    if cmd.cmd == "set_source_voltage":
        try:
            mV = int(cmd.params["mV"])
        except (KeyError, TypeError, ValueError):
            session.send_line(encode_error("set_source_voltage: bad mV"))
            return True
        if mV < 0:
            session.send_line(encode_error("voltage cannot be negative"))
            return True
        session.voltage_mV = mV
        session.send_line(encode_ok({"mV": mV}))
        return True

    if cmd.cmd == "set_stop_mode":
        try:
            enabled = bool(cmd.params["enabled"])
        except KeyError:
            session.send_line(encode_error("set_stop_mode: missing enabled"))
            return True
        session.stop_mode = enabled
        session.send_line(encode_ok({"enabled": enabled}))
        return True

    if cmd.cmd == "get_modifiers":
        session.send_line(encode_ok({
            "VDD": session.voltage_mV,
            "calibration": "mock-amore-v1",
        }))
        return True

    if cmd.cmd == "start_measuring":
        try:
            session.start_streaming()
        except Exception as e:
            log.exception("start_streaming failed")
            session.send_line(encode_error(f"start failed: {e}"))
            return True
        session.send_line(encode_ok({}))
        return True

    if cmd.cmd == "stop_measuring":
        session.stop_streaming()
        session.send_line(encode_ok({
            "total_samples": session.total_samples_sent,
        }))
        return True

    if cmd.cmd == "disconnect":
        session.stop_streaming()
        session.send_line(encode_ok({}))
        return False

    session.send_line(encode_error(f"unknown command: {cmd.cmd}"))
    return True


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------

def serve_one_connection(
    conn: socket.socket,
    gpio_spec: str,
    csv_out: Optional[Path],
    duration_s: float,
    rng_seed: Optional[int],
    pacing: str = "real-time",
    sample_period_us: int = DEFAULT_SAMPLE_PERIOD_US,
) -> None:
    """Handle one client connection from accept to close."""
    session = ServerSession(
        conn, gpio_spec, csv_out, duration_s, rng_seed,
        pacing=pacing, sample_period_us=sample_period_us,
    )
    try:
        f = conn.makefile("rb", buffering=0)
        for raw in iter(f.readline, b""):
            try:
                msg = decode_message(raw)
            except ValueError as e:
                session.send_line(encode_error(f"bad message: {e}"))
                continue
            if not isinstance(msg, Command):
                session.send_line(
                    encode_error("server expected a Command, got something else")
                )
                continue
            keep_going = handle_command(session, msg)
            if not keep_going:
                break
    finally:
        session.stop_streaming()
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass


def run_server(
    host: str,
    port: int,
    gpio_spec: str,
    csv_out: Optional[Path],
    duration_s: float,
    rng_seed: Optional[int],
    one_shot: bool = False,
    on_bound: Optional[Callable[[int], None]] = None,
    pacing: str = "real-time",
    sample_period_us: int = DEFAULT_SAMPLE_PERIOD_US,
) -> None:
    """Bind, accept, dispatch to ``serve_one_connection``.

    Args:
        port: TCP port to bind on. Pass 0 to let the kernel choose;
            the actually-bound port can be retrieved via ``on_bound``.
        on_bound: optional callback invoked with the bound port after
            ``listen()``. Useful for tests that need to discover the
            ephemeral port without a TOCTOU race against the kernel.
        one_shot: exit after the first client disconnects (test mode).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(1)
    actual_port = s.getsockname()[1]
    log.info("listening on %s:%d  one_shot=%s", host, actual_port, one_shot)
    log.info("gpio_source=%s  csv_out=%s  duration=%.3fs",
             gpio_spec, csv_out, duration_s)
    if on_bound is not None:
        on_bound(actual_port)
    try:
        while True:
            conn, peer = s.accept()
            log.info("accepted connection from %s", peer)
            try:
                serve_one_connection(
                    conn, gpio_spec, csv_out, duration_s, rng_seed,
                    pacing=pacing, sample_period_us=sample_period_us,
                )
            except Exception:
                log.exception("connection handler crashed")
            log.info("client %s disconnected", peer)
            if one_shot:
                break
    finally:
        try:
            s.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _int_with_prefix(s: str) -> int:
    """argparse type that accepts decimal, 0x..., 0o..., 0b... ints.

    Standard ``type=int`` rejects "0xC0FFEE" because int(s) defaults to
    base=10. Using ``int(s, 0)`` lets Python infer the base from the
    prefix, which is what people expect on the command line.
    """
    return int(s, 0)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Mock PPK2 TCP server.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument(
        "--gpio-source", default="fake-script:idle",
        help="GPIO source spec: 'fake-script:<path>', 'fake-script:idle', "
             "or 'real-lgpio' (deferred).",
    )
    p.add_argument(
        "--csv-out", type=Path, default=None,
        help="Optional path for the canonical CSV output (spec §1).",
    )
    p.add_argument(
        "--duration", type=float, default=DEFAULT_DURATION_S,
        help="Sampling duration in seconds (default 1.0).",
    )
    p.add_argument("--rng-seed", type=_int_with_prefix, default=None)
    p.add_argument(
        "--sample-rate-hz", type=int, default=25_000,
        help="Logical sample rate in Hz. Real PPK2 is 100_000. Mock "
             "default is 25_000 — Pi 3B can't sustain 100 ksps in "
             "pure Python. Spec §5.3 / PRD §5.4.4 require ≥10 ksps.",
    )
    p.add_argument(
        "--pacing", choices=["real-time", "none"], default="real-time",
        help="Sample emission rate. 'real-time' (default) sleeps between "
             "chunks so wall clock matches logical 100 ksps — accurate "
             "but requires the host to keep up. 'none' emits as fast as "
             "possible — needed on Pi 3B which can't sustain real-time "
             "100 ksps over TCP.",
    )
    p.add_argument(
        "--one-shot", action="store_true",
        help="Exit after the first client disconnects.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = p.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.sample_rate_hz < 1000 or args.sample_rate_hz > 1_000_000:
        log.error("sample-rate-hz out of range [1k, 1M]: %d",
                  args.sample_rate_hz)
        return 2
    sample_period_us = max(1, round(1_000_000 / args.sample_rate_hz))
    log.info("logical sample rate: %d Hz (period %d µs)",
             args.sample_rate_hz, sample_period_us)

    run_server(
        host=args.host, port=args.port, gpio_spec=args.gpio_source,
        csv_out=args.csv_out, duration_s=args.duration,
        rng_seed=args.rng_seed, one_shot=args.one_shot,
        pacing=args.pacing,
        sample_period_us=sample_period_us,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
