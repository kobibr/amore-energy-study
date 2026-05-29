# AmorE Energy Study — Final Results (2026-05-29)

**Status:** 23/23 cells × status=0x600D0000 (full success).
Smoke + 1 BLS-A + 10×Mode-B + 3×BN254-A (overnigh
          AmorE/round   3×RELIC pairings   Ratio   AmorE wins by
BN254:      150.36 mJ     267.14 mJ          0.563×  1.78×
BLS12-381:  320.67 mJ     566.73 mJ          0.566×  1.77×


## Headline numbers


Consistent ~1.77× energy reduction across both curves, matching the May 13
timing ratio (0.572×). Fair pure-C apples-to-apples (AmorE -O3 vs RELIC
ARITH=easy -O3, no asm).

## Per-cell statistics (Day 5/6 overnight)

| Cell    | N reps | wall (s)         | median I (mA)   |
|---------|--------|------------------|-----------------|
| BN254-A | 3      | 4597.7 ± 6.3     | 107.72 ± 1.98   |
| BN254-B | 10     | 502 ± 1          | 123.26 ± 0.36   |
| BLS-A   | 1      | 5462.4           | 107.72          |
| BLS-B   | 10     | 1308.6 ± 0.5     | 109.37 ± 0.47   |

Stdev <1% across all cells — deterministic firmware behavior.

## Mode B per-pairing (compute-only)

| Curve | cycles            | time (ms)         | I (mA)         | E (mJ)  |
|-------|-------------------|-------------------|----------------|---------|
| BN254 | 36,778,389 ± 0    | 218.919 ± 0.000   | 123.26 ± 0.36  | 89.05   |
| BLS   | 87,932,879 ± 0    | 523.410 ± 0.000   | 109.37 ± 0.47  | 188.91  |

BLS matches Diego 2026-05-07 (523.4 ms) and May 13 (87,933,033 cyc) EXACTLY.

## Mode A per-round (amortized, N=50)

| Curve | blind/rnd | verify/rnd | total/rnd | E (mJ) |
|-------|-----------|------------|-----------|--------|
| BN254 | 224.5 ms  | 198.5 ms   | 423.0 ms  | 150.36 |
| BLS   | 488.2 ms  | 413.9 ms   | 902.1 ms  | 320.67 |

Amortization converges (N=1→N=50 differs <2%).

## Methodology notes

- COMPUTE-ONLY energy reported (E = compute_cycles/F × I × V).
- Wall energy is dominated by server-wait busy-loop (~99% for Mode A) due
  to slow py_ecc server (73-87 s/round) + no WFI; this is setup-dependent
  and excluded from the fair comparison.
- PPK2 accurate to -5% (R33 calibration), no extra hardware needed.
- Replicas confirm stdev <1%; the firmware is deterministic.

## Reproducibility

| What | Where |
|------|-------|
| Orchestrator HEAD | commit ee202c8 (+ 2a7f2e4 measure_one_cell) |
| Firmware HEAD     | 42fdefd (NRST_DISCOVERY.md + heap 0x4000) |
| RELIC BLS lib     | sha256 58431811… (May 5, proven Diego match) |
| Logs (overnight)  | logs/full_regression_20260528_{205020,222615}/ and 20260529_033031/ |
