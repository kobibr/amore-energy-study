import pytest
from analysis.parse_traces import Phase
from analysis.compute_energy import phase_energy, compute_trace


def test_phase_energy_units():
    # 100 mA × 3.3 V × 1 second = 0.33 J
    # → 100_000 uA × 3.3 V × 1_000_000 us × 1e-12 = 0.33
    p = Phase(gpio_byte=1, start_us=0, end_us=1_000_000,
              samples=1, mean_current_uA=100_000.0, mean_voltage_V=3.3)
    assert phase_energy(p) == pytest.approx(0.33, rel=1e-9)


def test_aggregation_by_gpio_byte():
    # Bug #4 fix (silent-bias review 2026-05-23): the previous version
    # of this test had identical mean_current_uA values for both
    # phases of each gpio_byte (50_000 / 50_000 for idle, 85_000 /
    # 85_000 for setup), so any averaging scheme — arithmetic,
    # duration-weighted, sample-weighted, or median — produced the
    # same answer. A regression that switched compute_trace from
    # duration-weighted to arithmetic averaging would have slipped
    # through this test undetected.
    #
    # Below, the two idle phases have DIFFERENT currents (40 mA and
    # 55 mA) and DIFFERENT durations (100 µs and 200 µs). The two
    # averaging schemes disagree:
    #
    #     arithmetic       = (40_000 + 55_000) / 2          = 47_500 µA
    #     duration-weighted = (100 × 40_000 + 200 × 55_000) / 300
    #                       = (4_000_000 + 11_000_000) / 300
    #                       = 50_000 µA
    #
    # The assertion below requires the duration-weighted value, so
    # any regression to arithmetic averaging now fails this test.
    phases = [
        Phase(0,   0, 100, 10, 40_000.0, 3.3),      # Idle, 100 µs @ 40 mA
        Phase(1, 100, 200, 10, 85_000.0, 3.3),      # Setup,100 µs @ 85 mA
        Phase(0, 200, 400, 20, 55_000.0, 3.3),      # Idle, 200 µs @ 55 mA
        Phase(1, 400, 500, 10, 85_000.0, 3.3),      # Setup,100 µs @ 85 mA
    ]
    out = compute_trace(phases)

    assert 0 in out.by_gpio_byte
    assert 1 in out.by_gpio_byte
    idle = out.by_gpio_byte[0]
    setup = out.by_gpio_byte[1]
    assert idle.n_phases == 2
    assert setup.n_phases == 2
    assert idle.total_duration_us == 300  # 100 + 200
    assert setup.total_duration_us == 200  # 100 + 100

    # Bug #4 fix: duration-weighted means must come out to the
    # weighted values, NOT the arithmetic ones. With the bug, this
    # would have been 47_500 µA for idle (arithmetic mean of 40k+55k)
    # and 85_000 µA for setup (arithmetic and weighted coincide).
    assert idle.mean_current_uA == pytest.approx(50_000.0), (
        "idle gpio_byte aggregate must be duration-weighted "
        "(100 × 40k + 200 × 55k) / 300 = 50_000 µA. Got "
        f"{idle.mean_current_uA:.1f}; if it equals 47_500, "
        "compute_trace regressed to arithmetic averaging."
    )
    assert setup.mean_current_uA == pytest.approx(85_000.0)


def test_total_energy_equals_sum_of_phase_energies():
    phases = [
        Phase(0, 0, 1_000_000, 100, 50_000.0, 3.3),
        Phase(1, 1_000_000, 2_000_000, 100, 85_000.0, 3.3),
    ]
    out = compute_trace(phases)
    expected = phase_energy(phases[0]) + phase_energy(phases[1])
    assert out.total_energy_J == pytest.approx(expected, rel=1e-12)
