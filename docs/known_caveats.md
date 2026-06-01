# Known Caveats - AmorE Energy Study

## Measurement uncertainty

PPK2 absolute accuracy is bounded at -5.0% based on a 33-ohm
reference resistor measurement at 3.300 V (expected 100.0 mA,
measured 95.0 mA median). All reported current and energy values
carry this bound. Calibration log:
`measurement/calibration-logs/calibration_20260528_clean_R33.txt`.

The PPK2 was operated in factory-default uncalibrated state
(`modifiers['Calibrated'] == '0'`). The -5% deviation is therefore
the systematic offset of this specific instrument; relative ratios
within a single measurement session are not affected by it.

## Statistical scope

All measurements are from a single STM32F407 board. Variance
reported is run-to-run on the same chip, not chip-to-chip.
Different boards may give slightly different absolute readings;
ratios are expected to be insensitive to chip selection.

Mode B replication is 10 cells per curve. Cycle-level reproducibility
is exact (`pair_min_cycles` standard deviation is 0 across 10
replicas, both curves). Current standard deviation across replicas
is below 0.5%. Mode A replication is 1-3 cells; replica count is
bounded by experiment time (the slow py_ecc server makes each Mode A
replica 76-91 minutes long), not by statistical need.

## Energy attribution

Energy is reported as compute-only:

    E = (compute_cycles / 168e6) x I x V

This excludes the time spent waiting for the server's response.
In this deployment, wall time is dominated (~99% for Mode A) by
busy-wait of `HAL_UART_Receive` while the slow py_ecc server
computes. The Cortex-M4 has no `WFI` in the receive loop, so wait
current equals compute current; reporting wall energy would couple
the result to the choice of server. Compute-only energy is the
apples-to-apples cost of the protocol, independent of the deployed
server.

## Temperature

The MCU heats during sustained pairing computation. We have not
characterized temperature-dependent effects (Vcore regulator
efficiency, clock drift). All cells ran to completion with
`status = 0x600D0000` and showed inter-replica current drift below
0.5%, suggesting any thermal effect is within the measurement noise
floor on the time scales used here.

## Server choice

The Raspberry Pi 3B with py_ecc 8.0.0 is a slow reference server
(73 ms/round for BN254, 87 ms/round for BLS12-381). A faster server
would not change any client-side cycle count, current, or
compute-only energy figure; only wall time would shrink. The choice
is intentional: it exercises the protocol's amortization advantage
and provides ample sampling time for the PPK2 to integrate current
over each round.
