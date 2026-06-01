<!-- INTERNAL ONLY - DO NOT COMMIT TO PUBLIC GIT REPO -->

# AmorE Energy Study — Methodology

## Overview

This study measures the energy cost of the AmorE protocol — amortized
remote pairing evaluation — on a Cortex-M4 STM32F407 client. The protocol
delegates expensive bilinear pairings to a remote server (Raspberry Pi 3B)
and verifies the result cheaply on the client. The energy question:
**when does delegation save more energy than it costs to communicate?**

## Measurement chain

```
STM32F407 (client)
   │ PA0/PA1/PA4 trigger GPIOs → phase boundary markers
   │ PA2/PA3 USART2             → protocol UART to Pi
   ▼
PPK2 (Nordic Power Profiler Kit II)
   │ samples I(t), V(t), gpio_byte at 100 kHz
   │ Pre-PPK2 phase: Mock PPK2 in software (25 kHz)
   ▼
4-column CSV : timestamp_us, current_uA, voltage_V, gpio_byte
   │
   ▼
Analysis pipeline:
  parse_traces.py     → list[Phase]   (one record per gpio_byte run)
  compute_energy.py   → TraceEnergy   (E = I × V × t, aggregated by gpio_byte)
  variance_summary.py → CellSummary   (mean ± stderr across replicas)
```

## Channel assignment (per PIN_DIAGRAM.md)

| Pin (STM32) | Pi BCM | gpio_byte bit | Phase             | Mean I |
|-------------|--------|---------------|-------------------|--------|
| PA0         | 17     | bit 0         | Setup / Verify    | 85 mA  |
| PA1         | 27     | bit 1         | ServerWait        | 55 mA  |
| PA4         | 22     | bit 2         | Mode-C UART burst | 88 mA  |
| (none)      | —      | —             | Idle              | 50 mA  |

## Cell definition

A **cell** is one experimental condition: a tuple of `(curve, mode, N)`
with `replicas` independent runs for variance estimation.

| Curve     | Mode | N    | Replicas | Purpose                              |
|-----------|------|------|----------|--------------------------------------|
| BN254     | A    | 1, 3, 10, 30 | 3 | AmorE per-round amortization sweep     |
| BLS12_381 | A    | 1, 3, 10, 30 | 3 | Same, for the larger-security curve     |
| BN254     | B    | 10           | 3 | Direct RELIC pp_map_oatep_k12 baseline  |
| BLS12_381 | B    | 10           | 3 | Same                                    |

Mode A = AmorE protocol end-to-end.
Mode B = N consecutive direct pairings, no protocol overhead.
Mode C (deferred to a future iteration) = AmorE with UART-isolation trigger
on PA4, used to attribute UART energy precisely.

## Replicas and statistics

3 replicas per cell. The mean is reported with standard error of the mean
(stderr = stdev / √n). For n=3, stderr is roughly 0.6× stdev — sufficient
when CV (coefficient of variation) is <1%, which matches the determinism
of the underlying compute (BN254 CV measured at 0.115% on real hardware,
see doc/AmorE_BN128_Results.txt §5).

When real PPK2 measurements arrive, we will revisit replica count — if
inter-replica variance is dominated by power supply ripple rather than
compute jitter, more replicas may be warranted.

## Pre-PPK2 vs PPK2

For the Pre-PPK2 phase (this code is being written before the device
arrives), traces are generated synthetically from the firmware's known
cycle counts (doc/AmorE_*_Results.txt) combined with the current model
in `measurement/ppk2-control/current_synthesis.py`.

The synthesis preserves three properties that matter for energy analysis:

1. **Phase duration** — derived from cycle counts at 168 MHz.
2. **Phase mean current** — from `current_synthesis.py` spec table.
3. **Gaussian noise** with σ = 1.5–2.0% of mean, matching reported PPK2
   noise floor.

When PPK2 arrives, the change is purely upstream of the analysis layer:
`scripts/run_cell.py` swaps its mock client for a real driver (one-line
change, see `# IMPORT-SWITCH` comment). All downstream code is identical.



### Important caveat: server compute time is compressed in Mock data

`analysis/fixtures/synthetic_cells.py` uses `MOCK_SERVER_COMPRESS = 100`
to shrink the server's pairing compute time by 100× (from ~87 sec on
the Pi to ~870 ms in the synthetic trace). This keeps trace files
manageable in size — without it, BLS12_381 at N=30 would produce a
~2 GB CSV per replica.

**Consequence**: absolute ServerWait energy in the synthetic figures
is ~100× smaller than reality. Per-round Compute energy is *not*
affected (compute durations are at full size). The ratio of compute
to wait is what the synthetic data is calibrated to represent.

When real PPK2 traces arrive, set `MOCK_SERVER_COMPRESS = 1` (or use
the real-PPK2 backend, which writes traces at their natural rate) and
the absolute numbers will match the firmware.

This caveat **corrects** an earlier (ADR-005 v1) claim that the
synthetic data was "rate-agnostic". It is rate-agnostic *for compute*
phases only; ServerWait energy magnitude in the figures should not
be read literally.



## ServerWait and the energy result

While waiting for the server's response, the current firmware
(`uart_recv_packet()`) busy-waits with a running clock and full HAL
interrupt handling, so ServerWait current stays close to the compute
current. Under this firmware the AmorE per-round energy is *higher*
than a single direct pairing on the same MCU: the measured compute
energy ratios are 1.88× (BN254) and 1.96× (BLS12-381), and the time
ratios 1.93× / 1.72×. AmorE on this Cortex-M4 is not an energy or
speed optimization; its value is elsewhere — memory footprint
(21× less working SRAM, 4.2× less Flash, apples-to-apples), avoidance
of a pairing library on the client, and verifiability of the delegated
result. See doc/AmorE_BN128_Results.txt §11 and the results document.

## Honest limitations

- **Synthetic phase durations are derived from cycle counts**, not from
  the as-measured PPK2 traces. They will be replaced 1:1 when real
  measurements are available.
- **Mock PPK2 quiescent current is 0 µA** (no current sensor). Real PPK2
  measurements add a quiescent floor of ~50 mA (powered MCU).
- **No temperature or battery-curve effects** — Pi power supply is
  assumed steady. PPK2 reports voltage per-sample, so any deviation
  will be visible in the trace.
