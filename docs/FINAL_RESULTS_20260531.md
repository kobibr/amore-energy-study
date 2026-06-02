# AmorE Energy Study - Final Results

Date: 2026-05-31.
All 24 measurement cells (2 curves x 2 modes x 6 replicas) terminated
with `status = 0x600D0000` (full protocol success). Pure-C
apples-to-apples comparison: AmorE built at `-O3`, RELIC built at `-O3`
with `ARITH=easy` (no assembly), both on STM32F407 at 168 MHz.

The comparison is 1:1 per the AmorE paper (Definition 4 / Section 7.2):
one AmorE delegation vs one local pairing. Energy is phase-aware: the
current is measured during the compute phase (GPIO bit0), not the
full-trace median, which would be dominated by the busy-wait phase.

## Headline — batch delegation (the question the AmorE paper targets)

AmorE exists to delegate MANY pairings. Delegating 50 pairings (batch),
client compute energy, RELIC primitives on both sides:

    Curve       50x local pairing   AmorE batch(50)   AmorE saves
    ---------   -----------------   ---------------   -----------------
    BN254          4,262 mJ            2,669 mJ        37%  (1,593 mJ)
    BLS12-381      8,998 mJ            3,880 mJ        57%  (5,117 mJ)

Cycles MEASURED (microbench); batch client cost DERIVED (paper formula);
energy PROJECTED (derived cycles x measured current); compute-only (no
comm, no server-wait). Reproducible: analysis/fair_comparison/.

The section below is a DIFFERENT, least-favorable quantity: SINGLE
delegation (M=1) with the hand-written HOME Fp12 (not batch, not RELIC).
Kept for completeness, NOT the protocol value proposition.

## Single delegation, home implementation (least favorable)

                  AmorE/round    1 x RELIC pairing    Ratio    Result
    -----------   ------------   ------------------   ------   ------------------
    BN254          160.16 mJ        85.27 mJ          1.88x    AmorE costs 1.88x more
    BLS12-381      354.04 mJ       180.42 mJ          1.96x    AmorE costs 1.96x more

On a Cortex-M4 without assembly acceleration, AmorE costs ~1.9x the
energy of computing one pairing locally. AmorE's value on this platform
is not energy or speed (see "Interpretation" below); it is memory
footprint, pairing-library avoidance, and verifiable outsourcing.

## Per-cell statistics (6 replicas each)

    Cell      Replicas    Compute I (mA, R33 cal)   Pairing I (mA)
    -------   --------    -----------------------   --------------
    BN254-A   6           115.14 +/- 1.28           -
    BN254-B   6           -                         118.04 +/- 0.36
    BLS-A     6           119.08 +/- 2.18           -
    BLS-B     6           -                         104.45 +/- 0.00

Mode A current is the median over compute-phase samples (GPIO bit0 =
blind + verify). Mode B (relic_bench) has no phase markers and is pure
pairing, so its full-trace median IS the pairing current. Cycle-level
reproducibility is exact: `pair_min_cycles` standard deviation is 0
across all Mode B replicas on both curves.

## Mode B - direct RELIC pairing, 1 pairing (compute-only)

    Curve       cycles           time (ms)          I (mA)            E (mJ)
    ---------   --------------   ----------------   ---------------   ------
    BN254       36,778,389 +/-0  218.92  +/- 0.00   118.04 +/- 0.36    85.27
    BLS12-381   87,932,879 +/-0  523.41  +/- 0.00   104.45 +/- 0.00   180.42

BLS reproduces the 2026-05-07 and 2026-05-13 independent measurements
(523.4 ms, 87,933,033 cycles) to 1.8 ppm. RELIC is ARITH=easy (pure C)
at -O3 on both curves; -O2 is within 0.3% (easy-C is O-level insensitive).

## Mode A - AmorE per-round (amortized at N=50)

    Curve       amort cycles     time (ms)   I_compute (mA)    E (mJ)
    ---------   --------------   ---------   ---------------   ------
    BN254       70,813,093       421.5       115.14 +/- 1.28   160.16
    BLS12-381   151,357,860      901.0       119.08 +/- 2.18   354.04

Amortization converges from N=1 to N=50 within 2% (telemetry reports
N=1, N=10, N=50 in every Mode A cell).

## Time vs energy

    Curve   time ratio (A/B)   energy ratio (A/B)
    -----   ----------------   ------------------
    BN254   1.92x              1.88x
    BLS     1.72x              1.96x

The energy ratio equals the time ratio scaled by the current ratio
(I_AmorE / I_RELIC). For BN254 the compute currents are nearly equal
(115 vs 118 mA), so energy ratio ~= time ratio. For BLS, AmorE's CIOS
compute draws ~14% more current than RELIC's COMBA pairing (119 vs
104 mA), so the energy ratio (1.96x) exceeds the time ratio (1.72x):
AmorE is both slower and more current-intensive on BLS.

## Methodology summary

Energy is reported as compute-only and phase-aware:

    E = (compute_cycles / 168e6) x I_compute x V

where I_compute is the median current during the compute phase (GPIO
bit0), NOT the full-trace median. The compute phase is ~0.4% of wall
time for Mode A; the remaining ~99.6% is busy-wait of
`HAL_UART_Receive` while the slow py_ecc server computes. The busy-wait
current (~103 mA) is lower than the compute current (~115-119 mA);
using the full-trace median would understate compute energy by ~10%.
Compute-only is the apples-to-apples figure independent of server
speed. PPK2 absolute accuracy is bounded at -5% (33-ohm resistor
reference, 3.300 V).

Full methodology in `docs/methodology.md`. Calibration evidence in
`measurement/calibration-logs/`. Known caveats in
`docs/known_caveats.md`.

## Interpretation

AmorE on a Cortex-M4 (no assembly) costs ~1.9x the energy and ~1.7-1.9x
the time of a single local pairing. It is not a speed or energy
optimization on a part where RELIC already fits. Its value is:

  - Memory: AmorE client is far lighter than a full pairing library
    (no Fp12 work areas; see firmware memory footprint).
  - Pairing-library avoidance: the client needs no pairing implementation.
  - Verifiability: a malicious server's result is rejected with prob ~1.
  - Feasibility on parts where a pairing library does not fit at all.

## Reproducibility

    Orchestrator    github.com/kobibr/amore-energy-study
    Firmware        github.com/kobibr/amore-bn254-cortex-m4    HEAD 42fdefd
    RELIC library   ARITH=easy (pure C), FP_PRIME per curve, -O3
    Run             logs/full_regression_20260530_092609 (24 cells)
    Energy pipeline analysis/compute_energy.py -> energy_real.json
                    (phase-aware, reads logs/ only, no synthetic data)

Per-cell measurement logs, telemetry, and CSVs (large) are retained
in `logs/`.

## 50-round batch energy: local vs AmorE (added 2026-06-02)

The protocol question the AmorE paper targets: run 50 pairings locally vs
delegate 50 via AmorE (batch M=50). Built from the microbench raw g_micro
(measured) + the paper's Table-1 formula (derived) + the measured RELIC
pairing current (projected energy). Compute-only; reproducible via
`analysis/fair_comparison/compute_fair.py`.

    Curve       50x local pairing   AmorE batch(50)    AmorE saves
    ---------   -----------------   ---------------    -----------
    BN254          4,262 mJ            2,669 mJ        37%  (1,593 mJ)
    BLS12-381      8,998 mJ            3,880 mJ        57%  (5,117 mJ)

Provenance: cycles MEASURED (g_micro, DWT, min-of-16); batch client cost
DERIVED (paper formula; batch client not implemented on RELIC); energy
PROJECTED (derived cycles x measured pairing current x 3.3 V). This is NOT a
direct end-to-end measurement — it excludes communication and server-wait.
A direct end-to-end measurement (implement batch on RELIC, measure with
PPK2) is LEVEL 2 / FUTURE WORK.

Note: this is distinct from the §"Headline" 1.88x/1.96x, which is the
single-delegation HOME implementation (M=1, amortized over N=50 rounds) —
a different quantity (single vs batch, home Fp12 vs RELIC).
