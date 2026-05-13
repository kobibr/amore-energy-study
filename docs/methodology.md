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
   ▼
4-column CSV : timestamp_us, current_uA, voltage_V, gpio_byte
   │
   ▼
Analysis pipeline:
  parse_traces.py     → list[Phase]   (one record per gpio_byte run)
  compute_energy.py   → TraceEnergy   (E = I × V × t, aggregated by gpio_byte)
  variance_summary.py → CellSummary   (mean ± stderr across replicas)
  sleep_model.py      → BatchModel    (analytic E(N), crossover)
```

## Channel assignment

| Pin (STM32) | Pi BCM | gpio_byte bit | Phase             | Active current |
|-------------|--------|---------------|-------------------|----------------|
| PA0         | 17     | bit 0         | Setup / Verify    | ~85 mA         |
| PA1         | 27     | bit 1         | ServerWait        | ~55 mA         |
| PA4         | 22     | bit 2         | Mode-C UART burst | ~88 mA         |
| (none)      | —      | —             | Idle              | ~50 mA         |

Pin choice rationale: PA0, PA1, PA4 are general-purpose I/O pins not
allocated to peripherals on the STM32F407G-DISC1 board. PA2 and PA3 are
reserved for USART2 (TX/RX to the Pi) and cannot be repurposed for
triggers without breaking the protocol UART. The 50 mA idle baseline is
the powered-MCU quiescent draw on this hardware.

## Cell definition

A **cell** is one experimental condition: a tuple of `(curve, mode, N)`
with `replicas` independent runs for variance estimation.

| Curve     | Mode | N            | Replicas | Purpose                              |
|-----------|------|--------------|----------|--------------------------------------|
| BN254     | A    | 1, 3, 10, 30 | 3        | AmorE per-round amortization sweep   |
| BLS12_381 | A    | 1, 3, 10, 30 | 3        | Same, for the larger-security curve  |
| BN254     | B    | 10           | 3        | Direct RELIC pp_map_oatep_k12 baseline |
| BLS12_381 | B    | 10           | 3        | Same                                 |

Mode A = AmorE protocol end-to-end.
Mode B = N consecutive direct pairings, no protocol overhead.
Mode C (deferred to a future iteration) = AmorE with UART-isolation
trigger on PA4, used to attribute UART energy precisely.

## Replicas and statistics

3 replicas per cell. The mean is reported with standard error of the mean
(stderr = stdev / √n). For n=3, stderr is roughly 0.6× stdev — sufficient
when CV (coefficient of variation) is <1%, which matches the determinism
of the underlying compute (BN254 CV measured at 0.115% on real hardware,
see `doc/AmorE_BN128_Results.txt` §5).

If inter-replica variance during real-PPK2 measurement is dominated by
power supply ripple rather than compute jitter, replica count will be
revisited.

## Compiler optimization (-O2 vs -O3)

The firmware is built with `CMAKE_BUILD_TYPE=Release`, which adds
`-O3 -DNDEBUG` to the compiler flags. At `-O3`, GCC fully unrolls the
inner CIOS Montgomery multiplication loops in `fp_mul`, eliminating
per-iteration overhead and yielding a 2.14× speedup on all phases of
the BLS12-381 measurement compared to the prior `-O2` build.

The number of UMLAL operations executed per `fp_mul` call is identical
(~288) in both binaries; only per-iteration overhead differs. See
`doc/AmorE_BLS12_381_Results.txt` Section 8 for the disassembly evidence
and reproducible build commands for both optimization variants.

All measurements reported in this study use the `-O3` (Release) build
(tag `measurement-O3-2026-05-12`, fp_mul = 1300 bytes, binary SHA prefix
`4e2df263`).

## Two scenarios: baseline firmware vs proposed Stop-mode optimization

The AmorE thesis — that delegation saves energy beyond a crossover batch
size — depends critically on what the MCU does while waiting for the
server's response. We model two scenarios:

**BASELINE** (current firmware): `uart_recv_packet()` busy-waits with a
running HSI clock and full HAL interrupt handling. ServerWait current
stays at ~55 mA (close to compute current). With this firmware, AmorE
per-round energy is *higher* than a single direct pairing — no crossover
exists in any reasonable N range.

**WITH_STOP** (proposed): MCU enters Stop mode immediately after sending
the request, waking via RTC alarm or external interrupt when the
response arrives. Stop mode quiescent current is in the sub-microamp
range (STM32F407 datasheet: 0.4–0.6 µA typical). ServerWait energy
becomes negligible, and the per-round energy asymptote shifts down by
several orders of magnitude.

Figure 4 displays both scenarios side-by-side. The two-row layout makes
the delta unambiguous: top row = AmorE-loses, bottom row = AmorE-wins.
Firmware support for the Stop-mode optimization is the gate to
validating it on real PPK2 — will be detailed below.

## Crossover analysis

The central question — "is AmorE cheaper than direct pairing?" — is
answered by `sleep_model.find_crossover(model, e_direct, k)`. It finds
the smallest N where:

```
E_AmorE_per_round(N) ≤ k × E_direct_pairing
```

where k is the number of pairings AmorE replaces. For the protocol k=1
(one verification pairing per round, served by N rounds of blinding).
k=3 is reported as a sensitivity check (the original protocol cost
analysis at higher operational budgets).

A measured ratio outside the predicted range would warrant
investigation: too high suggests overhead not accounted for; too low
suggests unmeasured energy gains (e.g., LSI vs HSI clock during
ServerWait).

## Honest limitations

- **No temperature or battery-curve effects.** Pi power supply is
  assumed steady. PPK2 reports voltage per-sample, so any deviation
  will be visible in the trace.

- **The 100 µs-scale wake-up transient** is not always synthesized in
  the analytical model. Real PPK2 at 100 ksps should resolve a ~13 µs
  / 80 mA spike on each Stop→Run transition; the model overlays this
  only when `stop_mode=True`.

- **RELIC direct-pairing baseline** is currently the pre-O3 measurement
  (BLS12-381: 523.4 ms per pairing, from the 2026-05-07 measurement
  session). A like-for-like comparison against the post-O3 AmorE
  numbers requires rebuilding RELIC with `CMAKE_BUILD_TYPE=Release` and
  re-measuring. Headline ratios involving the RELIC baseline are
  flagged "pending RELIC re-measurement at -O3" until that work
  completes. See `docs/future_work.md`.

- **BN254 baseline** is the 2026-04-01 measurement at the
  `pre-port-bn254-working` firmware tag (also pre-O3). BN254
  re-measurement on the unified-curves branch is blocked by an
  fp12_mul curve-specificity issue; see `docs/future_work.md`.
