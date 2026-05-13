"""Synthetic current model for the Mock PPK2.

Maps the STM32 GPIO trigger byte (the ``gpio_byte`` field in CSV rows,
see ``csv_format.py``) to a plausible current draw in microamps, with
Gaussian noise.

This is a **pure function** — it has no notion of time or history. The
server in ``mock_ppk2_server.py`` composes this with temporal logic
(wake-up burst overlay, sample-rate interpolation) to produce the final
sample stream.

See ``docs/MOCK_PPK2_SPEC.md`` §7.3 for the full table.

Spec table (mean current, 1σ noise — both in milliamps)
-------------------------------------------------------
  gpio_byte = 0b<PA4><PA1><PA0>     phase                  mean    σ
  0b000                              Idle                   50.0   1.0
  0b001                              Setup (PA0)            85.0   1.5
  0b010                              ServerWait (PA1)       55.0   1.0
  0b011                              Setup+SW (illegal)     85.0   1.5
  0b100                              UART (PA4)             88.0   1.5
  0b101                              Setup+UART (illegal)   90.0   1.5
  0b110                              SW+UART (illegal)      88.0   1.5
  0b111                              All three (illegal)    90.0   1.5

Special cases
-------------
- **Stop mode** — when ``gpio_byte == 0`` and the ``stop_mode`` flag is
  set (passed via the wire protocol's ``set_stop_mode`` command per the
  reconciliation note: keeps ``start_measuring`` upstream-identical),
  the model returns the quiescent current of STM32 Stop mode: 0.5 µA
  mean, 0.1 µA σ. If any trigger is high the CPU is awake by definition,
  so stop-mode override only applies when the trigger byte is zero.

- **Wake-up burst** — temporal, *not* implemented here. The constants
  ``WAKEUP_BURST_PEAK_UA`` and ``WAKEUP_BURST_DURATION_US`` are exposed
  so the server can overlay 80 mA, 13 µs spikes on rising edges of PA0
  during the burst-measurement firmware variant (PRD §5.4.3).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CurrentModel:
    """Steady-state current parameters for a given GPIO state.

    Both fields are in microamps. The model is a Gaussian:
    samples ~ N(mean_uA, sigma_uA²).
    """

    mean_uA: float
    sigma_uA: float


# ---------------------------------------------------------------------------
# Spec §7.3 phase table (stored in mA for human readability)
# ---------------------------------------------------------------------------

# gpio_byte → (mean_mA, sigma_mA). Multiplied by 1000 to µA on use.
_PHASE_TABLE_MA: dict[int, tuple[float, float]] = {
    0b000: (50.0, 1.0),    # Idle (between phases)
    0b001: (85.0, 1.5),    # Setup / Blind / Verify (PA0)
    0b010: (55.0, 1.0),    # ServerWait (PA1, CPU mostly waiting)
    0b011: (85.0, 1.5),    # Setup + ServerWait (illegal but synthesizable)
    0b100: (88.0, 1.5),    # Mode C UART (PA4)
    0b101: (90.0, 1.5),    # Setup + UART (illegal)
    0b110: (88.0, 1.5),    # ServerWait + UART (illegal)
    0b111: (90.0, 1.5),    # All three (illegal)
}

# Stop-mode quiescent current (spec §7.3 sub-section "Stop-mode special case")
STOP_MODE_MEAN_UA: float = 0.5
STOP_MODE_SIGMA_UA: float = 0.1

# Wake-up burst constants (spec §7.3 sub-section "Wake-up burst special case")
# Exposed for the server's temporal overlay; this module is pure & stateless.
WAKEUP_BURST_PEAK_UA: float = 80_000.0   # 80 mA peak during the latency window
WAKEUP_BURST_DURATION_US: int = 13        # ~13 µs spike width

# Validation
GPIO_BYTE_MAX: int = 0xFF
RESERVED_MASK: int = 0xF8  # bits 3..7 must be 0 (csv_format.py mirrors this)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def model_for(gpio_byte: int, *, stop_mode: bool = False) -> CurrentModel:
    """Return the steady-state CurrentModel for ``gpio_byte``.

    Stop-mode override only applies when ``gpio_byte == 0`` (no trigger
    asserted). A non-zero trigger means firmware is driving a GPIO,
    which means the CPU is awake — so the steady-state table wins.

    Raises
    ------
    ValueError
        If ``gpio_byte`` is out of [0, 255] or has any reserved bit set.
    """
    if not 0 <= gpio_byte <= GPIO_BYTE_MAX:
        raise ValueError(f"gpio_byte out of range [0, 255]: {gpio_byte}")
    if gpio_byte & RESERVED_MASK:
        raise ValueError(
            "reserved bits 3..7 must be 0, got "
            f"gpio_byte=0x{gpio_byte:02x}"
        )

    if stop_mode and gpio_byte == 0:
        return CurrentModel(STOP_MODE_MEAN_UA, STOP_MODE_SIGMA_UA)

    mean_mA, sigma_mA = _PHASE_TABLE_MA[gpio_byte]
    return CurrentModel(mean_mA * 1000.0, sigma_mA * 1000.0)


def sample_current(
    gpio_byte: int,
    *,
    stop_mode: bool = False,
    rng: random.Random | None = None,
) -> float:
    """Draw one Gaussian-noised current sample in µA.

    Parameters
    ----------
    gpio_byte
        Trigger state byte (see ``csv_format.py``).
    stop_mode
        If True and ``gpio_byte == 0``, draw from the Stop-mode model.
    rng
        Optional seeded ``random.Random`` for reproducibility. If None,
        the module-global ``random`` state is used.

    Returns
    -------
    float
        Current sample in microamps.
    """
    m = model_for(gpio_byte, stop_mode=stop_mode)
    gauss = (rng or random).gauss
    return gauss(m.mean_uA, m.sigma_uA)
