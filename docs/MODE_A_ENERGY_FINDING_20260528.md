# Mode A Energy — Key Methodological Finding (2026-05-28)

## Run
BN254 Mode A, full 61 honest rounds + 1 security round.
`logs/full_regression_20260528_145444/` — status=0x600D0000, 61/61 verify_ok,
malicious correctly rejected. Pipeline fully validated.

## The finding: report COMPUTE-ONLY energy, not wall energy

The STM32 `uart_recv` uses `HAL_UART_Receive` — a **blocking busy-poll**
(no WFI/sleep). So the CPU draws full current (~109mA @ 3.3V = 360mW)
the entire time, whether computing crypto or waiting for the server.

The RPi server (py_ecc) is slow: ~73.7 s/round vs the client's ~0.42 s/round.
So wall-clock energy is 99.4% server-wait busy-loop:

| Quantity | Value | Use |
|----------|-------|-----|
| Total wall | 4604 s (76.7 min) | setup-dependent (py_ecc speed) |
| Client compute | 25.8 s (0.56%) | the AmorE crypto work |
| Server-wait (busy) | 4578 s (99.44%) | artifact of slow server |
| Power (constant) | 359.7 mW | measured, R33-calibrated -5% |
| **Compute-only energy** | **9.28 J** | **← report this (apples-to-apples)** |
| Total wall energy | 1656 J | setup-dependent, not comparable |

## Why this is correct, not a bug
- Cycle counts: exact (DWT hardware counter).
- Current 109mA: accurate (R33 calibration -5%).
- The flat current across phases is REAL: busy-wait = full CPU load always.
- D-channel phase split is too noisy at this sample rate AND meaningless
  energetically (same current in compute vs wait), so phase-resolved
  breakdown adds nothing for Mode A. Compute-only (cycles × power) is the
  clean, fair number.

## Per-batch client timing (amortization works)
| batch | blind/rnd | verify/rnd | total/rnd |
|-------|-----------|------------|-----------|
| N=1   | 214 ms    | 189 ms     | 403 ms    |
| N=10  | 223 ms    | 199 ms     | 421 ms    |
| N=50  | 225 ms    | 199 ms     | 423 ms    |

## Future option (NOT needed for compute-only reporting)
Add `__WFI()` in the UART wait path so the CPU sleeps (~20mA) while waiting.
Then wall energy would become meaningful. Firmware change; defer.

## Formula for reporting
Energy_compute = (client_cycles / 168e6) × V × I
             = 25.8 s × 3.3 V × 0.109 A = 9.28 J  (61 rounds, BN254)
Per round ≈ 9.28 / 61 = 0.152 J/round.
