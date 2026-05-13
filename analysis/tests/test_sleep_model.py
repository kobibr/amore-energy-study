import pytest
from analysis.sleep_model import BatchModel, find_crossover, analyze


def test_e_batch_grows_linearly_with_n():
    m = BatchModel(1e-3, 2e-3, 1e-3, 10e-3)
    assert m.e_batch(1)   == pytest.approx(14e-3, rel=1e-9)
    assert m.e_batch(10)  == pytest.approx(50e-3, rel=1e-9)
    assert m.e_batch(100) == pytest.approx(410e-3, rel=1e-9)


def test_per_round_amortizes():
    m = BatchModel(1e-3, 2e-3, 1e-3, 10e-3)
    assert m.e_per_round(1)   == pytest.approx(14e-3, rel=1e-9)
    assert m.e_per_round(10)  == pytest.approx(5e-3, rel=1e-9)
    assert m.asymptote()      == pytest.approx(4e-3, rel=1e-9)


def test_find_crossover_simple():
    m = BatchModel(1e-3, 2e-3, 1e-3, 10e-3)
    assert find_crossover(m, 6e-3, k=1) == 5


def test_no_crossover_returns_none():
    m = BatchModel(100e-3, 100e-3, 100e-3, 10e-3)
    assert find_crossover(m, 1e-3, n_max=100) is None


def test_analyze_full():
    m = BatchModel(1e-3, 2e-3, 1e-3, 10e-3)
    out = analyze(m, 6e-3)
    assert out.asymptote_J == pytest.approx(4e-3, rel=1e-9)
    assert out.crossover_n_for_k1 == 5
