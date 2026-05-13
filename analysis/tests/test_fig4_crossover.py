"""Unit tests for the corrected Figure 4 crossover model.

Locked numerical values for fig4_crossover regression:
locks the "no crossover at any (N, T)" finding so a future refactor
cannot silently regress it, and sanity-checks that the function isn't
always returning NaN due to a bug (by verifying that an artificially
inflated E_wakeup DOES produce a crossover).
"""
import math

import pytest

from analysis.baseline_data import (
    IDD_STOP_RANGE_UA, E_WAKEUP_RANGE_UJ,
    V_NOMINAL, DIRECT_PAIRING_MS,
)
from analysis.figures.fig4_crossover import (
    total_session_energy_mJ,
    find_crossover_T,
    amore_active_time_s, amore_active_energy_mJ,
    direct_active_time_s, direct_active_energy_mJ,
    sleep_energy_mJ,
)


# ---------------------------------------------------------------------------
# Building-block functions
# ---------------------------------------------------------------------------

class TestSleepEnergy:
    def test_zero_sleep_zero_energy(self):
        assert sleep_energy_mJ(0.0, 0.5) == 0.0

    def test_negative_sleep_clipped_to_zero(self):
        # If the strategy doesn't fit in T, sleep time is negative;
        # we clip to zero (the infeasibility is signalled by the caller
        # returning inf, not here).
        assert sleep_energy_mJ(-5.0, 0.5) == 0.0

    def test_linear_in_time(self):
        # E = t * I * V (with unit conversion µA → A, mJ → J)
        e1 = sleep_energy_mJ(10.0, 0.5)
        e2 = sleep_energy_mJ(20.0, 0.5)
        assert e2 == pytest.approx(2 * e1)

    def test_linear_in_current(self):
        e_low  = sleep_energy_mJ(60.0, 0.4)
        e_high = sleep_energy_mJ(60.0, 0.6)
        assert e_high / e_low == pytest.approx(0.6 / 0.4)

    def test_formula_value(self):
        # 60 s × 0.5 µA × 3.3 V = 99 µJ = 0.099 mJ
        expected = 60.0 * 0.5e-6 * 3.3 * 1000  # = 0.099 mJ
        assert sleep_energy_mJ(60.0, 0.5) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Total session energy
# ---------------------------------------------------------------------------

class TestTotalSessionEnergy:
    def test_amore_short_T_infeasible(self):
        # T = 0.1 s is shorter than any AmorE session
        e = total_session_energy_mJ(
            "BN254", n=10, T_s=0.1,
            idd_stop_uA=0.5, e_wakeup_uJ=20.0,
            strategy="amore",
        )
        assert e == float("inf")

    def test_direct_short_T_infeasible(self):
        # 50 × 252.3 ms = 12.6 s; T=1s is infeasible
        e = total_session_energy_mJ(
            "BN254", n=50, T_s=1.0,
            idd_stop_uA=0.5, e_wakeup_uJ=20.0,
            strategy="direct",
        )
        assert e == float("inf")

    def test_amore_one_wakeup_charge(self):
        # At very long T, the active energy and one wake-up charge
        # should dominate over sleep
        T = 3600.0
        e = total_session_energy_mJ(
            "BN254", n=10, T_s=T,
            idd_stop_uA=0.5, e_wakeup_uJ=20.0,
            strategy="amore",
        )
        # Active + 1 × wakeup + sleep(T - t_active)
        e_active = amore_active_energy_mJ("BN254", 10)
        t_active = amore_active_time_s("BN254", 10)
        e_sleep  = sleep_energy_mJ(T - t_active, 0.5)
        e_wake   = 20.0 / 1000.0  # uJ → mJ
        assert e == pytest.approx(e_active + e_sleep + e_wake)

    def test_direct_N_wakeup_charges(self):
        # Direct strategy pays N wake-up overheads, not 1
        T = 3600.0
        n = 10
        e_w_uJ = 20.0
        e = total_session_energy_mJ(
            "BN254", n=n, T_s=T,
            idd_stop_uA=0.5, e_wakeup_uJ=e_w_uJ,
            strategy="direct",
        )
        e_active = direct_active_energy_mJ("BN254", n)
        t_active = direct_active_time_s("BN254", n)
        e_sleep  = sleep_energy_mJ(T - t_active, 0.5)
        e_wake_total = n * e_w_uJ / 1000.0
        assert e == pytest.approx(e_active + e_sleep + e_wake_total)


# ---------------------------------------------------------------------------
# Crossover finding — the headline locked finding
# ---------------------------------------------------------------------------

class TestCrossoverFinding:
    @pytest.mark.parametrize("curve", ["BN254", "BLS12_381"])
    @pytest.mark.parametrize("n", [1, 3, 10, 30, 50])
    def test_no_crossover_with_datasheet_values_low(self, curve, n):
        """At LOW estimates (best case for AmorE), still no crossover."""
        T = find_crossover_T(
            curve, n,
            idd_stop_uA=IDD_STOP_RANGE_UA[0],   # 0.4 µA
            e_wakeup_uJ=E_WAKEUP_RANGE_UJ[0],   # 10 µJ
        )
        assert math.isnan(T), (
            f"Expected no crossover for {curve} N={n} at datasheet-low "
            f"parameters, but found T={T}. If this test fails, the model "
            f"behaviour has changed — investigate before updating the test."
        )

    @pytest.mark.parametrize("curve", ["BN254", "BLS12_381"])
    @pytest.mark.parametrize("n", [1, 3, 10, 30, 50])
    def test_no_crossover_with_datasheet_values_high(self, curve, n):
        """At HIGH estimates (still in datasheet range), still no crossover."""
        T = find_crossover_T(
            curve, n,
            idd_stop_uA=IDD_STOP_RANGE_UA[1],   # 0.6 µA
            e_wakeup_uJ=E_WAKEUP_RANGE_UJ[1],   # 30 µJ
        )
        assert math.isnan(T)


class TestSanityCheck:
    """If we inflate E_wakeup enough, crossover MUST appear.

    Otherwise find_crossover_T might be buggy and always returning NaN
    regardless of input — which would mean our 'no crossover' finding
    is meaningless. This test rules that out.

    Math for choosing the threshold E_wakeup:
      At N=50, AmorE active premium over direct ≈ 1958 mJ (BN254).
      For crossover to exist, wake-up savings must exceed this:
        (N-1) × E_wakeup_mJ > 1958
        E_wakeup_mJ > 40 mJ
      So we use E_wakeup_uJ = 50000 (= 50 mJ) for the sanity check.
      Physically unrealistic (~2000× datasheet) but the point is
      to verify the function CAN detect a crossover when one exists.
    """

    def test_artificially_high_wakeup_does_produce_crossover(self):
        # 50 mJ per wake-up overwhelms AmorE's active premium at N=50,
        # so find_crossover_T MUST find a T where AmorE wins.
        T = find_crossover_T(
            "BN254", n=50,
            idd_stop_uA=0.5,
            e_wakeup_uJ=50000.0,   # 50 mJ in µJ → 49 wakes saved = 2450 mJ
            T_max=3600.0,
        )
        assert not math.isnan(T), (
            "Expected a crossover with E_wakeup=50mJ × 49 saved wakes = "
            "2450 mJ savings — must beat AmorE's ~1958 mJ active premium "
            "at N=50. If this is NaN, the crossover search is broken."
        )
        # The crossover T should be in a sensible range
        assert 60.0 < T < 3600.0  # sleep must be long enough to dominate

    def test_no_crossover_with_modest_wakeup_inflation(self):
        """Regression guard: 5 mJ wake-up is NOT enough to flip the result.

        The architecture-lead reply states the savings are 100-3000× too
        small. This test locks the lower end of that range: even 5 mJ
        (~250× datasheet) doesn't suffice, because at N=50 the active
        premium is ~1958 mJ while savings would only be 245 mJ.
        """
        T = find_crossover_T(
            "BN254", n=50,
            idd_stop_uA=0.5,
            e_wakeup_uJ=5000.0,   # 5 mJ in µJ → savings 245 mJ < 1958 premium
            T_max=3600.0,
        )
        assert math.isnan(T), (
            "5 mJ wake-up should NOT produce crossover at N=50: savings "
            "245 mJ < 1958 mJ active premium. If a T was found, the model "
            "math has changed unexpectedly."
        )


class TestFormulaValues:
    """Locked numerical values for the figure regression.

    These are the locked reference values that appear
    Changing them indicates a regression that needs investigation.
    """

    def test_bn254_n1_at_T60s_amore(self):
        e = total_session_energy_mJ(
            "BN254", n=1, T_s=60.0,
            idd_stop_uA=IDD_STOP_RANGE_UA[0],
            e_wakeup_uJ=E_WAKEUP_RANGE_UJ[0],
            strategy="amore",
        )
        # Reference value: BN254 N=1 @T=60s, AmorE = 245.9 mJ (low estimates)
        assert e == pytest.approx(245.9, abs=1.0)

    def test_bn254_n10_at_T60s_direct(self):
        e = total_session_energy_mJ(
            "BN254", n=10, T_s=60.0,
            idd_stop_uA=IDD_STOP_RANGE_UA[0],
            e_wakeup_uJ=E_WAKEUP_RANGE_UJ[0],
            strategy="direct",
        )
        # Reply: direct = 707.9 mJ
        assert e == pytest.approx(707.9, abs=1.0)
