"""Watermark helper for figures rendered from synthetic baseline data.

Single source of truth for the disclaimer text — every plot script
calls add_watermark(fig) so the figures cannot be silently misread as
real-PPK2 measurements.

Two scenarios are modeled:

* BASELINE — current firmware uses busy-wait UART recv during
             ServerWait; MCU stays at ~55 mA (HSI clock running).
             Synthetic data with stop_mode=False corresponds to this.
* WITH_STOP — proposed optimization: MCU enters Stop mode during
             ServerWait; current drops to ~0.5 µA. Synthetic data
             with stop_mode=True corresponds to this. Requires
             firmware changes .
"""
from __future__ import annotations

import matplotlib.pyplot as plt


WATERMARK_TEXT_BASELINE = (
    "SYNTHETIC DATA — baseline firmware\n"
    "ServerWait is busy-wait at 55 mA (no Stop mode)\n"
    "See methodology document"
)

WATERMARK_TEXT_WITH_STOP = (
    "SYNTHETIC DATA — proposed optimization\n"
    "ServerWait at 0.5 µA (Stop mode entry)\n"
    "Requires firmware changes (see methodology)"
)


def add_watermark(fig, *, scenario: str = "BASELINE") -> None:
    """Add a corner watermark to the figure.

    Parameters
    ----------
    fig
        The matplotlib Figure to annotate.
    scenario
        Either "BASELINE" or "WITH_STOP". Selects the disclaimer text.
        Bug M5 fix: rejects anything else with ValueError. The whole
        point of this helper is "figures cannot be silently misread";
        a quiet fallback to WITH_STOP on a typo defeats that.

    The watermark is placed at the bottom-right of the figure in a
    semi-transparent box. Small enough not to dominate the figure, big
    enough not to be overlooked.
    """
    if scenario == "BASELINE":
        text = WATERMARK_TEXT_BASELINE
    elif scenario == "WITH_STOP":
        text = WATERMARK_TEXT_WITH_STOP
    else:
        raise ValueError(
            f"unknown scenario: {scenario!r}. "
            "Expected 'BASELINE' or 'WITH_STOP' (case-sensitive)."
        )
    fig.text(
        0.99, 0.01, text,
        fontsize=7, color="darkred",
        ha="right", va="bottom",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                  edgecolor="darkred", alpha=0.85),
    )
