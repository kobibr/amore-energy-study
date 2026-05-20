# Future Work — AmorE Energy Study

Open items at the close of the 2026-05-20 work session. Each entry
states what's needed, why it matters, and where to start.

This file pairs with `docs/audit_table.md` (current state) and
`docs/decisions.md` (design rationale).

---

## High priority — paper-blocking

### FW-1. HONEST_ROUNDS=61 full sweep

**What:** Run `firmware/amore-fw/scripts/run_benchmark.sh` with the
default HONEST_ROUNDS=61 (no override) and capture amort/round at
N=10 and N=50 on top of the already-measured N=1.

**Why:** Two table rows in `docs/audit_table.md` remain `· pending`:
- amort/round @ N=10
- amort/round @ N=50

Without these, the paper's amortization claim is supported only at N=1,
which is the *least* favorable case for AmorE.

**Effort:** ~30 minutes wall time (61 rounds × ~30s pairing each).

**Risk:** Low. The pipeline is validated end-to-end at N=1. Watch for
UART retry storms at long batches (we saw 1 uart_err in N=1 — may
compound at N=50).

**Where to start:** `cd firmware/amore-fw && bash scripts/run_benchmark.sh`
(no HONEST_ROUNDS override). Then `python3 -m analysis.audit_table` to
re-compile the table with the new data.

---

### FW-2. IDD_STOP measurement against stop_test.elf

**What:** Flash `firmware/amore-fw/build/bn254_a/stop_test.elf` via RPi SWD,
then run `python3 -m analysis.stop_validation`. Expected target:
~0.5 µA (datasheet typical for STM32F407 Stop mode).

**Why:** One pending row in audit_table:
- IDD_STOP (Stop-mode current)

This anchor pins the energy-savings projection for sleep-between-rounds
scenarios. Without it, the paper's per-day battery-life claim is
ill-defined.

**Effort:** ~5 minutes.

**Where to start:**
 First flash the stop firmware:

```bash
cd firmware/amore-fw
bash scripts/run_benchmark.sh --no-server --no-build \
    --flash-via=rpi --elf=build/bn254_a/stop_test.elf
```

Then measure:
```bash
cd ~/amore-energy-study
python3 -m analysis.stop_validation --duration 10 --boot-skip 2 \
    --target-uA 0.5 --tolerance-uA 100
```

---

### FW-3. Mode C firmware (UART per-byte energy isolation)

**What:** Add a firmware build flag that wraps `uart_send_packet()` and
`uart_recv_packet()` calls in extra GPIO trigger toggles, so the PPK2
trace can attribute energy to UART activity vs pairing computation.

**Why:** Today the gpio_byte trace lumps UART idle waits into whichever
phase is active. Mode C would let us decompose round energy into
compute / comm components from measurement alone, instead of computing
comm separately via `analysis.comm_projection`.

**Risk:** Medium. Any firmware change risks affecting UART timing.
Server timeouts (UART_TIMEOUT_MS = 120s) are loose, so this should be
safe, but worth a sanity-check N=1 run before committing.

**Effort:** ~2-3 hours. Add `-DMODE_C` compile flag, wrap two callsites
in `amore.c`, add doc note in firmware results.

**Where to start:** `firmware/amore-fw/src/amore.c` — locate the two
`uart_recv_packet()` calls in `AmorE_Run_Benchmarks()`.

---

## Medium priority — paper hardening

### FW-4. BN254 byte-1 round-to-round drift investigation

**What:** Investigate the CV=50.35% drift observed by `spread_check.py`
in `measurement/traces/bn254__a__N3__r3__stop/run_002.csv` (gpio_byte=1,
energy ranged from 51 to 141 mJ across 7 instances).

**Hypothesis space (uninvestigated):**
- Thermal: high-power phase heats Vcore reg; later rounds slow.
- Cache: first iteration cold-cache; later iterations warm.
- UART retry: client retries inflate every other instance.
- Sleep-mode entry between rounds (the path has "stop" in the name;
  could be unrelated stop-mode wake glitch).

**Why it matters:** If any of these explain the CV, the paper needs a
footnote. If none do, it's an unexplained variance the paper must own.

**Blocker:** No thermistor instrumented on the board today. Could be
partially addressed by re-running with warm vs cold-boot conditions and
correlating UART log timestamps with PPK2 timestamps.

**Effort:** ~1 day if we're satisfied with "warm vs cold" comparison;
~1 week if we add real thermal instrumentation.

---

## Low priority — extended work

### FW-5. Real-radio comm energy validation

**What:** Cross-check the BLE / LoRa projections from
`analysis.comm_projection` against a real radio.

**Status:** `analysis/comm_projection.py` projects energy from
datasheet anchors (commit 0b09f5e). This is deliberate (see
`docs/decisions.md` ADR-04), but a real-radio cross-check would
harden the lower-bound claim.

**What's needed:**
- nRF52840 dev kit + SX1276 LoRa breakout (~$75).
- Second PPK2 (or current shunt on the existing one).
- Code to transmit the AmorE payload (576 B up / 1152 B down).
- Expected: real radios cost 30-80% more than the lower bound due
  to link-layer overhead.

**Why it matters:** Turns the projection from estimate into a
validated bound.

**Effort:** 1-2 weeks bring-up.

---

### FW-6. BLS12-381 hand-tuned assembly

**What:** Port the RELIC hand-tuned ARM Cortex-M4 assembly for `fp_mul`
(and possibly `fp_sqr`) into our firmware, preserving the GPIO
instrumentation hooks.

**Why it matters:** RELIC's BLS12-381 hand-tuned ASM gives ~1.75× over
our -O3 build (commit 5ac93b1). The energy study reports -O3 numbers;
the gap would shrink with ASM. Would tighten the AmorE-vs-direct ratio
on the compute side.

**Out of scope for this paper's "as compiled" baseline.** Worth a
follow-up paper or extended report.

**Effort:** 1-2 weeks of ARM Cortex-M4 assembly expertise.

---

### FW-7. Multi-board sample for cell-to-cell variability

**What:** Repeat the headline N=1 measurement on 3-5 additional
Discovery boards from the same MCU lot.

**Why it matters:** Today the reproducibility evidence is within-board
(CV = 0.22 % across replicas). The paper currently does not distinguish
"this MCU is stable" from "this MCU model is stable."

**Effort:** ~$50 hardware + 1 day measurement.

---

## Tracking

When closing an item:
- If the closure produced a design decision worth preserving, move the
  section into `docs/decisions.md`.
- If the closure was just running a measurement, delete the section
  and re-run `python3 -m analysis.audit_table` to refresh the table.

The next session's first priority is FW-1 (HONEST_ROUNDS=61), then
FW-2 (IDD_STOP), then re-run audit_table to confirm pending → measured.
