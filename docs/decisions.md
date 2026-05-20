# Architecture Decision Records — AmorE Energy Study

This document captures the major design and methodology decisions made
during this study. Each entry records what was decided, why, and what
alternatives were considered. The format is ADR-lite: enough context
that a reviewer (or future maintainer) can reconstruct the reasoning.

---

## ADR-001: Pairing curves — BN254 and BLS12-381

**Status:** Adopted

**Context:** AmorE accelerates pairing-based protocols by delegating a
batch of pairings to an untrusted server. We need representative curves
to demonstrate the energy savings.

**Decision:** Measure both BN254 (legacy 100-bit security) and BLS12-381
(modern 128-bit security). Use RELIC's reference implementations.

**Rationale:**
- BN254 is the curve in the original AmorE paper (Aranha et al.), so our
  results are directly comparable to that work.
- BLS12-381 is the curve in actual use today (Ethereum, Filecoin, ZCash).
  A 2026 paper that doesn't cover BLS12-381 has limited impact.
- Both curves use the same protocol code path on STM32, so the comparison
  is fair.

**Consequences:**
- Two parallel measurement campaigns. Mostly mechanized via run_benchmark.sh.
- BLS12-381 is markedly slower per pairing (~3-5× BN254 depending on
  optimization level). The energy story is qualitatively similar but
  numerically larger in the BLS12-381 case.

---

## ADR-002: -O3 for production benchmarks

**Status:** Adopted (revised — see ADR-002a)

**Context:** RELIC builds at -O2 by default. For a benchmark publication,
we want the compiler-optimization level that a real deployer would use.

**Decision:** Build firmware at -O3 for headline numbers. Keep -O2 results
in an appendix for completeness.

**Rationale:**
- -O3 enables loop unrolling and aggressive inlining that the RELIC
  internal fp_mul / fp_sqr inner loops benefit from substantially
  (measured 2.14× speedup vs -O2 on BLS12-381).
- A field-deployment energy study should reflect the best available
  performance.

**Consequences:**
- Section 8 of the BLS12-381 report annotates the -O3 vs -O2 asymmetry
  explicitly (commit 3e50c28).
- Disassembly evidence (doc/evidence/fp_mul_O*.asm) committed for
  reproducibility (commit 1e7533a).

---

## ADR-002a: 1:1 RELIC compiler-level baseline

**Status:** Adopted (revised from initial -O3 vs -O2 setup)

**Context:** Comparing our (-O3) STM32 numbers against a stale RELIC
benchmark file built with default (-O2) creates a 2× artifact unrelated
to AmorE.

**Decision:** Rebuild the RELIC reference benchmark with the same
optimization level (-O3) and publish that as the comparison anchor.

**Rationale:** AmorE paper Section 7.2 (Aranha et al.) compares like-for-
like compiler settings. We follow the same convention to make our
results directly comparable.

**Consequences:**
- relic_bench_O3_20260513.txt committed.
- BLS report Section 5 + 8.5 reflect this honest 1:1 comparison
  (commit a203a29).

---

## ADR-003: Nordic PPK2 for current measurement

**Status:** Adopted

**Context:** Energy measurement requires accurate current sensing with
fine time resolution (≤1 ms phase boundaries) and a wide dynamic range
(µA stop-mode to hundreds of mA active).

**Decision:** Nordic Power Profiler Kit II (PPK2) in Source mode, sourcing
3.3 V to the STM32F407 Discovery via Power side connector.

**Rationale:**
- ±10% factory-calibrated current accuracy across the 5 ranges
  (200 nA → 1 A) without per-unit calibration paperwork.
- Built-in 7 digital channels read GPIO trigger pins synchronously with
  the analog samples, enabling per-phase energy attribution.
- The ppk2-api Python library exposes everything we need.

**Alternatives considered:**
- Direct shunt + oscilloscope: more flexible but requires hand-built
  jig and post-processing.
- INA226 / INA228: cheaper but slower sample rate.

**Consequences:**
- PPK2's USB-serial bus shows occasional disconnect after rapid open/close.
- The library (0.9.2) has subtle state-handling limitations — see ADR-005.

---

## ADR-004: RPi GPIO bit-bang SWD instead of ST-LINK USB

**Status:** Adopted (rescue plan after ST-LINK USB cable failure)

**Context:** ST-LINK USB cable supplied 4.55 V → 2.93 V to the rail under
load, corrupting SWD communications. Six hours of debug confirmed cable
fault, not chip fault.

**Decision:** Use a Raspberry Pi 3B+ as SWD programmer/debugger via GPIO
pins (bit-banged OpenOCD). STM32 receives all power and programming via
PPK2 + RPi.

**Rationale:**
- We have a working Pi already (it hosts the AmorE server side).
- OpenOCD's bcm2835gpio driver is mature.
- 1.989 kHz SWD clock is plenty for our 22 KB ELF flash size and live
  debug needs.

**Wiring:**
- RPi GPIO 25 (pin 22) → SWCLK
- RPi GPIO 24 (pin 18) → SWDIO
- RPi GPIO 18 (pin 12) → NRST
- RPi pin 6 → GND
- ST-LINK USB DISCONNECTED entirely

**Consequences:**
- run_benchmark.sh auto-detects the available programmer (commit 150ad6c).
- STM32 powered exclusively by PPK2; no USB power conflict.
- A 30-second pre-build step ssh's the ELF to the Pi.

---

## ADR-005: Per-voltage fresh PPK2 instance pattern

**Status:** Adopted (after debug)

**Context:** Sweeping voltages within a single Python session (one
PPK2_API object that calls set_source_voltage() between captures)
produced wildly inconsistent current readings (mean 7600 mA, stdev
5800 mA — physically impossible since PPK2 max is 1 A).

**Investigation:**
- Multimeter validated VOUT was correct at each voltage (2.93 / 3.22 / 3.50 V).
- Bisect showed that even *same-voltage* repeated captures within one
  instance degraded across calls.
- Inspection of measurement/backends.py::PPK2Backend.measure_replica()
  revealed that the working code path always opens a fresh PPK2_API
  per measurement.

**Decision:** Mirror the PPK2Backend pattern in voltage_sensitivity.py:
fresh PPK2_API instance per voltage, 3-second sleep between, explicit
del + gc.collect() to force USB device close.

**Rationale:** ppk2-api 0.9.2 does not correctly handle repeated
start_measuring / stop_measuring or voltage transitions within a single
Python object: ADC calibration state goes stale.

**Consequences:**
- Voltage sweep takes ~13s instead of ~5s (3s settle per voltage).
- All future PPK2 measurement modules MUST follow this pattern.
- Documented in module docstring + this ADR.

**Validation:** First successful sweep produced neg=0 samples at 3.0V
and 3.6V, and only 7 negative samples out of 312,244 at 3.3V
(0.0022% — within ADC noise floor). Data trusted.

---

## ADR-006: Honest evidence — pending claims marked, not hidden

**Status:** Policy

**Context:** It is tempting to omit unfinished data points from the
final table. This is fine for an internal scratch but harmful for a
publication: reviewers will notice the gap, and we cannot revisit
exactly what was missing.

**Decision:** audit_table.py produces three statuses: measured,
computed, pending. Pending entries appear in the table with explicit
"·" markers and notes explaining what is needed to complete them.

**Rationale:**
- An incomplete table that says so is more useful than a complete-looking
  table that quietly drops the hard cases.
- Future maintainers can finish pending items without re-deriving what
  was missing.

**Consequences:**
- docs/audit_table.md shows the current state warts and all.
- HONEST_ROUNDS=61 sweep is the obvious next big task; it will fill the
  N=10 and N=50 amort/round entries.

---

## ADR-007: Public input — no privacy claim

**Status:** Adopted (correction)

**Context:** Early draft language referred to "input privacy" as a
benefit of delegation. This was incorrect: AmorE's threat model assumes
the server learns the inputs.

**Decision:** Removed all "input privacy" language from the BLS12-381
results document (commit 9d8b85f). AmorE provides verification, not
privacy.

**Rationale:** Accurate threat model description is non-negotiable.

---

## Index of related artifacts

- `docs/comm_anchors.md` — datasheet anchors for BLE/LoRa projections
- `docs/audit_table.md` — compiled paper deliverable (read this first)
- `analysis/` — per-module sources, each with its own docstring rationale
- `firmware/amore-fw/doc/` — protocol + report documents
