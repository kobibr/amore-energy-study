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
    phases = [
        Phase(0, 0, 100,  10, 50_000.0, 3.3),      # Idle
        Phase(1, 100, 200, 10, 85_000.0, 3.3),     # Setup
        Phase(0, 200, 400, 20, 50_000.0, 3.3),     # Idle
        Phase(1, 400, 500, 10, 85_000.0, 3.3),     # Setup
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
    assert idle.mean_current_uA == pytest.approx(50_000.0)
    assert setup.mean_current_uA == pytest.approx(85_000.0)


def test_total_energy_equals_sum_of_phase_energies():
    phases = [
        Phase(0, 0, 1_000_000, 100, 50_000.0, 3.3),
        Phase(1, 1_000_000, 2_000_000, 100, 85_000.0, 3.3),
    ]
    out = compute_trace(phases)
    expected = phase_energy(phases[0]) + phase_energy(phases[1])
    assert out.total_energy_J == pytest.approx(expected, rel=1e-12)
