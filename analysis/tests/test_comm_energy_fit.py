import pytest
from analysis.comm_energy_fit import fit_linear, predict


def test_perfect_line():
    # E = 5e-9 × bytes + 1e-6
    pts = [(100, 5.0e-7 + 1e-6), (500, 25e-7 + 1e-6), (1000, 50e-7 + 1e-6)]
    fit = fit_linear(pts)
    assert fit.a_J_per_byte == pytest.approx(5e-9, rel=1e-6)
    assert fit.b_J == pytest.approx(1e-6, abs=1e-12)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-9)


def test_prediction_round_trip():
    pts = [(100, 1.0e-6), (1000, 10.0e-6)]
    fit = fit_linear(pts)
    assert predict(fit, 500) == pytest.approx(5.0e-6, rel=1e-6)


def test_need_two_points():
    with pytest.raises(ValueError):
        fit_linear([(100, 1e-6)])
