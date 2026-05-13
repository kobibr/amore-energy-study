import pytest
from analysis.parse_traces import Phase
from analysis.compute_energy import compute_trace
from analysis.variance_summary import summarize_replicas


def test_three_replicas():
    # Build 3 fake traces with slight variation
    def mk(idle_cur, setup_cur):
        phases = [
            Phase(0, 0, 1_000_000, 100, idle_cur, 3.3),
            Phase(1, 1_000_000, 2_000_000, 100, setup_cur, 3.3),
        ]
        return compute_trace(phases)
    traces = [mk(50_000.0, 85_000.0), mk(50_100.0, 85_200.0), mk(49_900.0, 84_800.0)]

    summary = summarize_replicas(traces)
    assert summary.n_replicas == 3
    # Setup should have mean energy ≈ 85_000 × 3.3 × 1e6 × 1e-12 = 0.2805 J
    setup_stats = summary.by_gpio_byte_energy_J[1]
    assert setup_stats.mean == pytest.approx(0.2805, rel=1e-3)
    assert setup_stats.stdev > 0  # nonzero variation
    assert setup_stats.cv < 0.01  # less than 1% CV
