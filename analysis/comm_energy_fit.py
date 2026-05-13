"""Linear fit of energy as a function of UART payload bytes.

This anchors the comm-energy model used in the AmorE crossover analysis
The model is::

    E_comm(payload_bytes) = a × payload_bytes + b

where ``b`` is per-message overhead (header/CRC framing) and ``a`` is
per-byte energy. Both fitted from anchor measurements.

A handful of anchor points suffices because UART energy is mostly
deterministic: at 921600 baud, 10 bits/byte → 86.8 µs per byte
transmit time, ServerWait current ~55 mA → 14.3 nJ/byte ideal.
Empirical fit may deviate due to DMA setup, Pi-side latency, etc.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class CommFit:
    a_J_per_byte: float      # slope
    b_J: float               # intercept (per-message overhead)
    r_squared: float
    n_points: int


def fit_linear(points: List[Tuple[float, float]]) -> CommFit:
    """Ordinary least squares y = a·x + b.

    points: list of (payload_bytes, energy_J).
    Returns slope, intercept, R².
    """
    n = len(points)
    if n < 2:
        raise ValueError(f"need at least 2 anchor points (got {n})")

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx = sum(xs) / n
    my = sum(ys) / n

    num = sum((x - mx) * (y - my) for x, y in points)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        raise ValueError("all anchor x-values are identical; cannot fit")
    a = num / den
    b = my - a * mx

    # R²
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in points)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    return CommFit(
        a_J_per_byte=a,
        b_J=b,
        r_squared=r2,
        n_points=n,
    )


def predict(fit: CommFit, payload_bytes: float) -> float:
    """E_comm prediction for a given payload size."""
    return fit.a_J_per_byte * payload_bytes + fit.b_J
