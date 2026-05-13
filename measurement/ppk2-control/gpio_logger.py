"""GPIO transition logger for the AmorE Mock PPK2.

Captures GPIO transitions on the trigger pins and writes them to a
minimal CSV with columns ``timestamp_us,gpio_byte``. Per PRD
this is the firmware-validation tool used during firmware bring-up before the
full ``mock_ppk2_server.py`` exists; it will be subsumed into the
server.

Two modes
---------

* ``--mode real`` (default) — live capture via lgpio callbacks on the
  Pi. Requires ``python3-lgpio``. Pin defaults from spec §2.2:
  PA0=BCM17, PA1=BCM27, PA4=BCM22.

* ``--mode fake-stdin`` — reads ``delay_ms gpio_byte`` lines from stdin
  and emits a CSV as if those transitions had occurred at the
  cumulative times. No GPIO required; works on the host. Used for unit
  tests of the data path.

CSV format (intentionally simpler than the full ``csv_format.py``
4-column trace — current and voltage are added by ``mock_ppk2_server.py``
)::

    timestamp_us,gpio_byte
    100000,1
    480000,0
    530000,2

Pin → bit packing matches ``csv_format.py``: bit 0 = PA0, bit 1 = PA1,
bit 2 = PA4. Bits 3-7 reserved (always 0).

CLI examples
------------

Live capture for 30 seconds (on the Pi)::

    python3 gpio_logger.py --mode real --out trace.csv --duration 30

Replay a scripted scenario through the same writer (anywhere)::

    cat scenario.txt | python3 gpio_logger.py \\
        --mode fake-stdin --out trace.csv

scenario.txt format — one transition per line, "delay_ms gpio_byte"::

    # comment lines and blank lines are ignored
    100  1   # 100 ms after start, PA0 high (gpio_byte=0b001)
    380  0   # 380 ms later, all triggers low
    50   2   # 50 ms later, PA1 high (gpio_byte=0b010)
    100  0   # 100 ms later, all low again
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import IO

# ---------------------------------------------------------------------------
# Defaults — match spec §2.2 pin assignment
# ---------------------------------------------------------------------------

DEFAULT_PA0_BCM = 17
DEFAULT_PA1_BCM = 27
DEFAULT_PA4_BCM = 22
DEFAULT_CHIP = 0

CSV_HEADER = "timestamp_us,gpio_byte"


def gpio_byte_from_levels(pa0: int, pa1: int, pa4: int) -> int:
    """Pack three pin levels (0/1) into the canonical gpio_byte.

    Mirrors the bit assignment used by ``csv_format.py`` so analysis
    code can read either format with the same masks.
    """
    return (pa0 & 1) | ((pa1 & 1) << 1) | ((pa4 & 1) << 2)


# ---------------------------------------------------------------------------
# Real-GPIO mode — lgpio callbacks (only available on the Pi)
# ---------------------------------------------------------------------------

def run_real_mode(args: argparse.Namespace) -> int:
    """Capture live edges via lgpio callbacks for ``args.duration``.

    ``lgpio`` is imported lazily so the script remains importable on
    hosts that don't have it (where only fake-stdin mode is needed).
    """
    import lgpio  # noqa: PLC0415 — lazy import for cross-platform usability

    pins = [args.pa0, args.pa1, args.pa4]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    h = lgpio.gpiochip_open(args.chip)

    # Track current level per pin so we can pack a fresh gpio_byte on
    # every edge.
    level = {p: 0 for p in pins}

    # lgpio callbacks deliver ts in nanoseconds since a monotonic epoch;
    # we record offsets from the first edge so the CSV starts at t=0.
    start_ns: int | None = None

    fp = out.open("w", encoding="utf-8")
    fp.write(CSV_HEADER + "\n")
    fp.flush()

    def on_edge(_chip: int, gpio: int, lvl: int, ts_ns: int) -> None:
        nonlocal start_ns
        if lvl not in (0, 1):
            return  # 2 = watchdog, 3 = glitch — not signal data
        if start_ns is None:
            start_ns = ts_ns
        level[gpio] = lvl
        ts_us = (ts_ns - start_ns) // 1000
        gb = gpio_byte_from_levels(
            level[args.pa0], level[args.pa1], level[args.pa4]
        )
        fp.write(f"{ts_us},{gb}\n")

    callbacks: list[object] = []
    try:
        for p in pins:
            try:
                lgpio.gpio_free(h, p)
            except Exception:
                pass
            lgpio.gpio_claim_input(h, p)  # no internal pull — STM32 drives push-pull
            callbacks.append(lgpio.callback(h, p, lgpio.BOTH_EDGES, on_edge))

        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:
            pass
    finally:
        for cb in callbacks:
            try:
                cb.cancel()
            except Exception:
                pass
        for p in pins:
            try:
                lgpio.gpio_free(h, p)
            except Exception:
                pass
        lgpio.gpiochip_close(h)
        fp.close()
    # Warn if no edges captured — symptom of a wiring or pinmux problem
    # that the CSV header alone wouldn't reveal.
    written_lines = sum(1 for _ in out.open(encoding="utf-8")) - 1
    if written_lines == 0:
        import sys as _sys
        print(
            f"WARN: gpio_logger captured 0 edges in {args.duration:.1f}s. "
            "Check wiring and that the STM32 is actively toggling triggers.",
            file=_sys.stderr,
        )
    return 0


# ---------------------------------------------------------------------------
# Fake-stdin mode — replay a scripted scenario
# ---------------------------------------------------------------------------

def run_fake_stdin_mode(
    args: argparse.Namespace, source: IO[str] | None = None
) -> int:
    """Read ``delay_ms gpio_byte`` lines from ``source`` (default: stdin).

    Used for unit tests; no GPIO involvement. The 'time' axis is purely
    accumulated from the delays.

    Each non-blank, non-comment line must have exactly two whitespace-
    separated tokens. Inline ``#`` comments are stripped before parsing.
    Bad lines are warned to stderr and skipped, not fatal — keeps a long
    scenario file robust against typos.
    """
    src: IO[str] = source if source is not None else sys.stdin

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fp:
        fp.write(CSV_HEADER + "\n")
        t_us = 0
        for raw in src:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "#" in line:
                line = line.split("#", 1)[0].strip()
            parts = line.split()
            if len(parts) != 2:
                print(f"WARN: bad line: {raw!r}", file=sys.stderr)
                continue
            try:
                delay_ms = float(parts[0])
                gb = int(parts[1])
            except ValueError:
                print(f"WARN: parse error: {raw!r}", file=sys.stderr)
                continue
            if not 0 <= gb <= 0xFF:
                print(
                    f"WARN: gpio_byte out of range [0,255]: {gb}",
                    file=sys.stderr,
                )
                continue
            t_us += int(round(delay_ms * 1000))
            fp.write(f"{t_us},{gb}\n")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "GPIO transition logger: live lgpio capture or scripted replay."
        )
    )
    p.add_argument("--mode", choices=["real", "fake-stdin"], default="real")
    p.add_argument("--out", required=True, help="Output CSV path")
    p.add_argument(
        "--duration", type=float, default=10.0,
        help="Capture duration in seconds (real mode only)",
    )
    p.add_argument(
        "--chip", type=int, default=DEFAULT_CHIP,
        help="GPIO chip number (default 0)",
    )
    p.add_argument("--pa0", type=int, default=DEFAULT_PA0_BCM)
    p.add_argument("--pa1", type=int, default=DEFAULT_PA1_BCM)
    p.add_argument("--pa4", type=int, default=DEFAULT_PA4_BCM)
    args = p.parse_args(argv)

    if args.mode == "real":
        return run_real_mode(args)
    return run_fake_stdin_mode(args)


if __name__ == "__main__":
    sys.exit(main())
