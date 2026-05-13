"""Locked baseline constants test.

If any of these constants changes, the test fails. The point is to make
it impossible to silently drift the synthetic-data calibration away from
the actual measured values in doc/AmorE_*_Results.txt.

To legitimately change a constant:
  1. Re-run the measurement binary (relic_bench.elf or amore_*.elf)
  2. Update the constant here
  3. Update doc/AmorE_*_Results.txt to reflect the new measurement
  4. Update the docstring/comment with the new measurement date

See docs/decisions.md for the data-provenance policy and the
authority chain for each constant.
"""
import pytest
from analysis.fixtures.synthetic_cells import CURVES


# Source: doc/AmorE_BN128_Results.txt §11 (BN254, 2026-04-01, pre-O3)
# and  doc/AmorE_BLS12_381_Results.txt §5 (BLS12_381, 2026-05-07, pre-O3).
# DWT cycle counter at 168 MHz, CV<0.001%.

def test_bn254_direct_pairing_ms_matches_measurement():
    """BN254 single pp_map_oatep_k12 via RELIC. AmorE_BN128_Results.txt §11."""
    assert CURVES["BN254"]["direct_pairing_ms"] == 252.3, (
        "If you changed this, re-run relic_bench.elf and update "
        "doc/AmorE_BN128_Results.txt §11."
    )


def test_bls12_381_direct_pairing_ms_matches_measurement():
    """BLS12_381 single pp_map_oatep_k12. AmorE_BLS12_381_Results.txt §5.

    NOTE: This is the pre-O3 RELIC measurement (2026-05-07). When RELIC
    is rebuilt with CMAKE_BUILD_TYPE=Release for like-for-like
    comparison against the post-O3 AmorE numbers, this constant and
    test_amore_vs_direct_ratio_documented update in the same commit.
    See docs/future_work.md "RELIC re-measurement at -O3".
    """
    assert CURVES["BLS12_381"]["direct_pairing_ms"] == 523.4, (
        "If you changed this, re-run relic_bench.elf with the BLS12_381 "
        "build and update doc/AmorE_BLS12_381_Results.txt §5."
    )


def test_bn254_amort_per_round():
    """BN254 amort/round at N=50. AmorE_BN128_Results.txt §4.3."""
    p = CURVES["BN254"]
    assert p["blind_per_round_ms"] == 199.4
    assert p["verify_per_round_ms"] == 182.4
    # Total per-round = blind + verify (exclusive of OTS) = 381.8 ms
    assert p["blind_per_round_ms"] + p["verify_per_round_ms"] == pytest.approx(381.8)


def test_bls12_381_amort_per_round():
    """BLS12_381 amort/round at N=50. AmorE_BLS12_381_Results.txt §8.

    Post-O3 measurement (2026-05-12, CMAKE_BUILD_TYPE=Release).
    Binary SHA prefix 4e2df263, commit 0ecc6e8, tag
    measurement-O3-2026-05-12. Validated by 61/61 honest + 1/1
    malicious, status 0x600D0000 in
    logs/combined_report_20260512_090923.txt.
    """
    p = CURVES["BLS12_381"]
    assert p["blind_per_round_ms"] == 488.28
    assert p["verify_per_round_ms"] == 409.70
    # Total per-round = 897.98 ms (architect's reported number 898.0 ms
    # rounds to the same 3-sig-fig value).
    assert p["blind_per_round_ms"] + p["verify_per_round_ms"] == pytest.approx(897.98, abs=0.05)


def test_one_time_setup_costs():
    """OTS ms — same source documents.

    BN254: AmorE_BN128_Results.txt §4.2 (pre-O3, 2026-04-01).
    BLS12_381: AmorE_BLS12_381_Results.txt §8 (post-O3, 2026-05-12).
    """
    assert CURVES["BN254"]["ots_ms"] == 503.9
    assert CURVES["BLS12_381"]["ots_ms"] == 1151.2


def test_amore_vs_direct_ratio_documented():
    """Per-round AmorE BLS12_381 vs 3× direct pairing.

    As of 2026-05-12:
        AmorE (post-O3):    897.98 ms / round (488.28 + 409.70)
        3× direct (pre-O3): 3 × 523.4 = 1570.2 ms total
        Ratio:              897.98 / 1570.2 ≈ 0.572×

    WARNING: This is NOT a like-for-like comparison.
    AmorE benefits from GCC -O3 inner-loop unrolling in fp_mul
    (see Section 8 of doc/AmorE_BLS12_381_Results.txt). The
    RELIC baseline (523.4 ms) was measured pre-O3 in the
    2026-05-07 session. RELIC's Montgomery multiplication uses
    similar 12-limb arithmetic and would likely show a comparable
    ~2× speedup if rebuilt with CMAKE_BUILD_TYPE=Release.

    Estimated post-O3 RELIC range: ~220-300 ms per pairing.
    Resulting like-for-like ratio: ~898 / (3 × 250) ≈ 1.20× slower,
    consistent with the original pre-O3 conclusion of 1.22× slower.

    The "AmorE faster than 3×direct" claim must therefore wait for
    RELIC re-measurement. Until then, this test locks the
    mechanically-correct-but-comparison-unfair 0.572× ratio.

    BN254 ratio remains pre-O3 / pre-O3 (apples-to-apples):
        BN254: AmorE/direct = 381.8 / 252.3 = 1.51×

    See docs/future_work.md "RELIC re-measurement at -O3" (HIGH
    priority).
    """
    bn = CURVES["BN254"]
    bls = CURVES["BLS12_381"]

    # BN254: like-for-like (both pre-O3).
    bn_ratio = (bn["blind_per_round_ms"] + bn["verify_per_round_ms"]) / bn["direct_pairing_ms"]
    assert bn_ratio == pytest.approx(1.51, abs=0.01)

    # BLS12_381: AmorE post-O3 vs RELIC pre-O3 — mechanically correct,
    # NOT like-for-like.
    amore_amort = bls["blind_per_round_ms"] + bls["verify_per_round_ms"]
    assert amore_amort == pytest.approx(897.98, abs=0.5)

    three_direct = 3 * bls["direct_pairing_ms"]
    assert three_direct == pytest.approx(1570.2, abs=0.1)

    ratio = amore_amort / three_direct
    assert 0.55 <= ratio <= 0.60   # ~5% tolerance for variance / minor drift
