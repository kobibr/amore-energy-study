"""comm_projection.py — Project communication-energy of AmorE vs direct.

Computes per-round comm energy for AmorE and direct pairing across
configured radio anchors (BLE, LoRa). Anchors are pulled from
datasheet values documented in docs/comm_anchors.md.

The projection is intentionally optimistic (no link-layer overhead,
no retransmissions, no advertising). It produces a lower bound of
the comm-energy contribution to total per-round energy.

Output:
- Stdout table: per-radio, per-N comm energy for AmorE and direct
- Optional CSV via --csv-out

Usage:
    python3 -m analysis.comm_projection
    python3 -m analysis.comm_projection --radio BLE_nRF52840
    python3 -m analysis.comm_projection --N-values 1 10 50
    python3 -m analysis.comm_projection --csv-out comm_projection.csv

Exit codes:
    0 = projection produced
    2 = invalid input (unknown radio, etc.)
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# Datasheet anchors (see docs/comm_anchors.md for sources)
# ─────────────────────────────────────────────────────────────────────
ANCHORS: dict[str, dict[str, float]] = {
    "BLE_nRF52840": {
        "tx_current_mA": 4.8,         # 0 dBm TX
        "rx_current_mA": 4.6,         # 1 Mbps RX
        "idle_current_uA": 1.5,
        "voltage_V": 3.0,
        "throughput_bps": 1_000_000,  # 1 Mbps PHY
    },
    "LoRa_SX1276_SF7": {
        "tx_current_mA": 28.0,        # +14 dBm, PA_BOOST off
        "rx_current_mA": 10.3,
        "idle_current_uA": 0.2,
        "voltage_V": 3.3,
        "throughput_bps": 5470,       # SF=7, BW=125 kHz
    },
}

# ─────────────────────────────────────────────────────────────────────
# Payload model (from firmware/amore-fw/inc/amore_uart.h)
# ─────────────────────────────────────────────────────────────────────
# IMPORTANT (Bug H2): these are PER-ROUND payloads for AmorE Mode A.
# Each round transmits one Setup message and receives one Result.
# OneTimeSetup is computed client-side, not communicated, so it adds
# no comm cost. Total AmorE comm for N rounds = N × these payloads
# (just like Direct).
#
# Consequence: there is NO true "comm-only crossover" between AmorE
# and Direct — both grow linearly with N, and Direct's per-pairing
# payload is smaller (720 B vs AmorE's 1730 B per round), so Direct
# is always cheaper on comm alone. The compute-side amortization is
# what makes AmorE win on TOTAL energy, not comm.
#
# The "Comm-only crossover" output below is therefore re-framed as
# an OVERHEAD RATIO: how many Direct pairings cost the same in comm
# as one AmorE round. Useful for sizing comm-link budget but NOT a
# break-even point for AmorE.
AMORE_SETUP_BYTES = 576       # per round, one Setup message
AMORE_RESULT_BYTES = 1152     # per round, one Result message (γ + ρ)
AMORE_STATUS_BYTES = 1
AMORE_READY_BYTES = 1

AMORE_UPLINK_BYTES = AMORE_SETUP_BYTES + AMORE_STATUS_BYTES
AMORE_DOWNLINK_BYTES = AMORE_RESULT_BYTES + AMORE_READY_BYTES
AMORE_TOTAL_BYTES = AMORE_UPLINK_BYTES + AMORE_DOWNLINK_BYTES

# Direct pairing payload (BLS12-381).
#
# Bug #5 fix (silent-bias review 2026-05-23): the previous comment
# called these "compressed group elements" but 576 B is the size of
# an UNCOMPRESSED Fp12 element (12 × 48 B), i.e. the full pairing
# result, not a compressed group element. For reference:
#   compressed G1 = 48 B    ← appears in DIRECT_PAIRING_UPLINK_BYTES
#   compressed G2 = 96 B    ← appears in DIRECT_PAIRING_UPLINK_BYTES
#   Fp12 uncompressed = 576 B = DIRECT_PAIRING_DOWNLINK_BYTES
#   Fp12 half-compressed (Edwards form) ≈ 288 B  ← NOT used here
#
# Compression policy: we model the direct pairing as RETURNING the
# full uncompressed Fp12 result. This is what RELIC's pp_map_oatep_k12
# emits over UART in our reference implementation. It is conservative
# vs. half-compressed Fp12 (288 B) in the sense that it OVERSTATES
# Direct's downlink bytes by 2×, which makes Direct look more comm-
# expensive than it might be with compression. If you switch the
# reference impl to half-compressed Fp12 downlink:
#   DIRECT_PAIRING_DOWNLINK_BYTES = 288
#   AMORE_RESULT_BYTES            = 576  (= 2 × 288, γ + ρ)
# and rerun the comm projection. The AmorE-vs-Direct comm conclusion
# below (Direct always cheaper) does not flip — the change just
# narrows the gap.
DIRECT_PAIRING_UPLINK_BYTES = 48 + 96
DIRECT_PAIRING_DOWNLINK_BYTES = 576


def comm_energy_J(uplink_bytes: int, downlink_bytes: int,
                  anchor: dict[str, float]) -> tuple[float, float]:
    """Returns (E_TX_J, E_RX_J)."""
    V = anchor["voltage_V"]
    I_tx_A = anchor["tx_current_mA"] / 1000.0
    I_rx_A = anchor["rx_current_mA"] / 1000.0
    throughput_Bps = anchor["throughput_bps"] / 8.0
    t_tx_s = uplink_bytes / throughput_Bps
    t_rx_s = downlink_bytes / throughput_Bps
    E_tx_J = I_tx_A * V * t_tx_s
    E_rx_J = I_rx_A * V * t_rx_s
    return (E_tx_J, E_rx_J)


def project_amore(anchor: dict[str, float]) -> dict[str, float]:
    """Per-round AmorE comm energy (independent of N)."""
    E_tx, E_rx = comm_energy_J(AMORE_UPLINK_BYTES, AMORE_DOWNLINK_BYTES, anchor)
    return {
        "uplink_bytes": float(AMORE_UPLINK_BYTES),
        "downlink_bytes": float(AMORE_DOWNLINK_BYTES),
        "E_tx_mJ": E_tx * 1000.0,
        "E_rx_mJ": E_rx * 1000.0,
        "E_total_mJ": (E_tx + E_rx) * 1000.0,
    }


def project_direct(N: int, anchor: dict[str, float]) -> dict[str, float]:
    """Per-batch direct-pairing comm energy for N pairings."""
    uplink = DIRECT_PAIRING_UPLINK_BYTES * N
    downlink = DIRECT_PAIRING_DOWNLINK_BYTES * N
    E_tx, E_rx = comm_energy_J(uplink, downlink, anchor)
    return {
        "uplink_bytes": float(uplink),
        "downlink_bytes": float(downlink),
        "E_tx_mJ": E_tx * 1000.0,
        "E_rx_mJ": E_rx * 1000.0,
        "E_total_mJ": (E_tx + E_rx) * 1000.0,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--radio", choices=list(ANCHORS.keys()), default=None,
        help="Limit projection to one radio (default: all)",
    )
    p.add_argument(
        "--N-values", type=int, nargs="+", default=[1, 10, 50],
        help="Batch sizes N to project (default: 1 10 50)",
    )
    p.add_argument(
        "--csv-out", type=Path, default=None,
        help="Write projection table to CSV",
    )
    args = p.parse_args(argv)

    radios = [args.radio] if args.radio else list(ANCHORS.keys())
    rows: list[dict] = []

    for radio in radios:
        if radio not in ANCHORS:
            print(f"FATAL: unknown radio: {radio}", file=sys.stderr)
            return 2
        anchor = ANCHORS[radio]
        amore = project_amore(anchor)

        print()
        print(f"━━━ {radio} ━━━")
        print(f"  TX: {anchor['tx_current_mA']} mA"
              f"   RX: {anchor['rx_current_mA']} mA"
              f"   V = {anchor['voltage_V']} V"
              f"   throughput: {anchor['throughput_bps']:,} bps")
        print()
        print(f"  AmorE per round (constant, any N):")
        print(f"    payload: {amore['uplink_bytes']:.0f} B up / "
              f"{amore['downlink_bytes']:.0f} B down")
        print(f"    E_TX = {amore['E_tx_mJ']:.3f} mJ   "
              f"E_RX = {amore['E_rx_mJ']:.3f} mJ   "
              f"E_total = {amore['E_total_mJ']:.3f} mJ")
        rows.append({
            "radio": radio,
            "scheme": "AmorE",
            "N": "any",
            "uplink_bytes": amore["uplink_bytes"],
            "downlink_bytes": amore["downlink_bytes"],
            "E_tx_mJ": amore["E_tx_mJ"],
            "E_rx_mJ": amore["E_rx_mJ"],
            "E_total_mJ": amore["E_total_mJ"],
        })

        print()
        print(f"  Direct pairings (N pairings per batch):")
        print(f"    {'N':>5}  {'up_B':>8}  {'down_B':>8}  "
              f"{'E_TX_mJ':>10}  {'E_RX_mJ':>10}  {'E_total_mJ':>12}")
        for N in args.N_values:
            direct = project_direct(N, anchor)
            print(f"    {N:>5}  {direct['uplink_bytes']:>8.0f}  "
                  f"{direct['downlink_bytes']:>8.0f}  "
                  f"{direct['E_tx_mJ']:>10.3f}  {direct['E_rx_mJ']:>10.3f}  "
                  f"{direct['E_total_mJ']:>12.3f}")
            rows.append({
                "radio": radio,
                "scheme": "Direct",
                "N": str(N),
                "uplink_bytes": direct["uplink_bytes"],
                "downlink_bytes": direct["downlink_bytes"],
                "E_tx_mJ": direct["E_tx_mJ"],
                "E_rx_mJ": direct["E_rx_mJ"],
                "E_total_mJ": direct["E_total_mJ"],
            })

        # Bug H2 fix: the so-called "crossover" is actually a per-round
        # overhead ratio. Both AmorE and Direct comm costs grow linearly
        # with N, so the lines never actually cross in N. What we CAN
        # show is the per-round overhead: how many Direct pairings cost
        # the same comm energy as one AmorE round.
        per_pairing = project_direct(1, anchor)
        if per_pairing["E_total_mJ"] > 0:
            overhead_ratio = amore["E_total_mJ"] / per_pairing["E_total_mJ"]
            print(f"\n  AmorE per-round comm overhead vs Direct per-pairing:")
            print(f"  one AmorE round ≈ {overhead_ratio:.2f} Direct pairings (in comm energy)")
            print(f"  Direct is always cheaper on comm alone; AmorE wins on")
            print(f"  total energy via compute-side amortization (OTS).")

    # CSV output
    if args.csv_out:
        args.csv_out.parent.mkdir(parents=True, exist_ok=True)
        with args.csv_out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"\nCSV: {args.csv_out}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
