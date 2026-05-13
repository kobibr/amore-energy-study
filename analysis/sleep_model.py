"""Energy model: aggregate phases into per-batch and per-round cost.

E_batch(N) = E_OneTimeSetup + N × (E_Setup + E_ServerWait + E_Verify) + E_idle
E_round(N) = E_batch(N) / N  — approaches asymptote as N grows.

Crossover (AmorE vs direct pairing):
    smallest N where E_AmorE_per_round(N) <= k × E_pairing_direct
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class BatchModel:
    e_setup_per_round_J: float
    e_serverwait_per_round_J: float
    e_verify_per_round_J: float
    e_one_time_setup_J: float
    e_idle_overhead_J: float = 0.0

    def e_batch(self, n: int) -> float:
        per_round = self.e_setup_per_round_J + self.e_serverwait_per_round_J + self.e_verify_per_round_J
        return self.e_one_time_setup_J + n * per_round + self.e_idle_overhead_J

    def e_per_round(self, n: int) -> float:
        if n <= 0:
            raise ValueError(f"n must be >= 1, got {n}")
        return self.e_batch(n) / n

    def asymptote(self) -> float:
        return self.e_setup_per_round_J + self.e_serverwait_per_round_J + self.e_verify_per_round_J


def find_crossover(model: BatchModel, e_direct_pairing_J: float,
                   k: int = 1, n_max: int = 1000) -> Optional[int]:
    threshold = k * e_direct_pairing_J
    for n in range(1, n_max + 1):
        if model.e_per_round(n) <= threshold:
            return n
    return None


@dataclass
class CrossoverAnalysis:
    e_per_round_at_N: Dict[int, float]
    crossover_n_for_k1: Optional[int]
    crossover_n_for_k3: Optional[int]
    asymptote_J: float


def analyze(model: BatchModel, e_direct_pairing_J: float,
            ns=(1, 3, 10, 30, 100)) -> CrossoverAnalysis:
    return CrossoverAnalysis(
        e_per_round_at_N={n: model.e_per_round(n) for n in ns},
        crossover_n_for_k1=find_crossover(model, e_direct_pairing_J, k=1),
        crossover_n_for_k3=find_crossover(model, e_direct_pairing_J, k=3),
        asymptote_J=model.asymptote(),
    )
