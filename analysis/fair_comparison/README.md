# AmorE fair comparison — reproducible computation (time + projected energy)

Reproduce the protocol-level result (AmorE client vs a local pairing) from
**raw measurements**, not summary numbers. One command:

    python3 compute_fair.py

## Provenance — what is measured vs derived vs projected

| label | applies to | how |
|---|---|---|
| **MEASURED** | `p_cyc` and every primitive (`m1_*`,`m2_*`,`mT_*`,`memT`); client current I | on-chip DWT cycle counts (min of 16) from `micro_bench.c` on STM32F407 @168 MHz, same `librelic_s.a` (ARITH=easy, -O3) as the pairing baseline; I from a real PPK2 capture of the RELIC pairing (Mode B): BN254 118 mA, BLS12-381 104 mA |
| **DERIVED** | the AmorE client cost (single & batch) | paper Table-1 formula applied to the measured primitives. The client (Setup+Verify) is **not implemented on RELIC**, so its cost is computed, not run |
| **PROJECTED** | energy (mJ) | `derived_cycles x measured_I x V`. Same RELIC backend both sides, so energy% == time%. **Not** a direct end-to-end measurement (no communication, no server-wait) |

Validation: `p_cyc` matches the published pairing baseline to 0.008% (BN254)
and 0.18% (BLS12-381) — same harness, same conditions.

## Formula assignment (paper Table 1; protocol Fig. 2)

    single (M=1): mT + N*(2*m1 + m2 + mbar2 + mbarT + memT)
    batch (M>1):  mT + N*(m1 + 2*m2 + M*(mbar1 + mbarT + memT))

The subtlety that matters: `2*m1 = m1_fix + m1_var`, because `U=[u]P` uses the
generator P (fixed-base) but `C=[..](U+A)` uses a non-generator point
(variable-base). Using `2*m1_fix` understates the BN254 single case by ~10
points (no effect on BLS, where fixed==variable). `m2=m2_fix`,
`2*m2=m2_fix+m2_var`, `mbar1=m1_short`, `mbar2=m2_short`, `mbarT=mT_short`.

## Result — TIME (derived from measured cycles), N=10

| | single | M=5 | M=10 | M=50 |
|---|---|---|---|---|
| BN254 | -38% | +24% | +31% | +37% |
| BLS12-381 | -0.8% | +46% | +52% | +57% |

## Result — ENERGY for 50 pairings (PROJECTED, compute-only)

50 local pairings vs AmorE delegating 50 (batch M=50). Energy =
derived_cycles x measured_current x 3.3 V. **Compute-only**: excludes
communication and server-wait.

| | 50x local pairing | AmorE batch (50) | AmorE saves |
|---|---|---|---|
| BN254 | 4,262 mJ | 2,669 mJ | **37%** (1,593 mJ) |
| BLS12-381 | 8,998 mJ | 3,880 mJ | **57%** (5,117 mJ) |

This is the question the paper targets — savings grow with batch size M,
because the one-time and per-delegation costs amortize and each extra
pairing costs only short operations instead of a full pairing.

## Interpretation

- **Batch (M>=5) wins** clearly, in time and in projected compute energy.
- **Single (M=1) does not win** on Cortex-M4 (BN254 -38%, BLS -0.8%), for two
  measured reasons (not a bug): fixed-base barely accelerates in RELIC
  easy-C here (`m1_fix/m1_var=1.00` on BLS), and short 90-bit scalars save
  only ~25-40% (`mT_short/mT_full~0.7`) — no Frobenius/special-form.

## Limitations / future work (Level 2)

The energy figures are **projected** (derived cycles x measured current),
compute-only. A **direct end-to-end energy measurement** of the batch —
implementing batch Setup+Verify on RELIC and measuring with the PPK2,
including real communication and server-wait — is **left as future work
(Level 2)**. The current proxy (measured RELIC pairing current) is sound for
compute energy because both sides share the RELIC backend, but it does not
capture comm/idle energy.
