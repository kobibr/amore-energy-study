# Communication-energy anchors

This document records the datasheet anchors used by `analysis/comm_projection.py`
to project the communication-energy cost of AmorE vs direct pairing.

The anchors are **not measured by us** — they come from manufacturer datasheets
and are used as published lower-bounds. Real-world deployment will see higher
energy (link-layer overhead, retransmissions, channel hopping, advertising
intervals, etc.). The projection therefore represents a best-case scenario for
the radio's published numbers.

## Why anchors

The on-device measurement (PPK2 on STM32F407) captures **compute** energy.
The total energy of one AmorE round is `compute + communication`. The
communication term depends on the chosen radio. Since we are not measuring
specific radios, we project from documented current draw at a fixed voltage,
fixed throughput, and fixed payload sizes.

## Payload sizes (from firmware/amore-fw/inc/amore_uart.h)

The AmorE protocol exchanges fixed-size packets per round:

| Direction      | Payload | Bytes |
|----------------|---------|-------|
| Client → Server | CMD_SETUP (A, B, C, D)             | 576  |
| Server → Client | CMD_RESULT (gamma, rho)            | 1152 |
| Client → Server | CMD_STATUS                          | 1    |
| Server → Client | CMD_READY                           | 1    |
| **Total / round (uplink + downlink)** |               | **1730** |

For *direct* pairing, the client performs N independent pairings, each
needing its own pair of group elements transmitted both ways. The
direct-pairing communication cost scales with N; AmorE's stays constant at
1730 B regardless of N. This is the comm-side amortization that drives
the per-round trade-off.

## Anchor #1 — Nordic nRF52840 (BLE 1 Mbps PHY)

Source: Nordic nRF52840 Product Specification v1.7 (June 2021), §6.18
"Radio Specifications".

| Parameter            | Value      | Source                                     |
|----------------------|------------|--------------------------------------------|
| TX current @ 0 dBm   | 4.8 mA     | Datasheet Table 27, TX1M, 0 dBm output     |
| RX current (1 Mbps)  | 4.6 mA     | Datasheet Table 27, RX1M                   |
| Idle (System ON)     | 1.5 µA    | Datasheet Table 31, System ON, RAM-on      |
| Supply voltage       | 3.0 V       | Operating point                            |
| Effective throughput | 1 Mbps      | Datasheet, PHY rate (link-layer ~700 kbps) |

**Notes & caveats:**
- 0 dBm is the lowest reasonable TX power; higher TX power costs more.
- The 1 Mbps PHY is the standard BLE rate; 2 Mbps consumes more current per
  active second but transmits twice as fast, so the per-byte energy is
  similar.
- We do **not** model GAP advertising or connection overhead.
- Idle current does not include sensor or MCU activity; it's radio-only.

## Anchor #2 — Semtech SX1276 (LoRa, SF7, BW 125 kHz)

Source: Semtech SX1276/77/78/79 Datasheet v7 (June 2020), §2.5
"Power Consumption", and §6.1.1 "Modulation Parameters".

| Parameter            | Value       | Source                                     |
|----------------------|-------------|--------------------------------------------|
| TX current @ +14 dBm | 28 mA       | Datasheet Table 7, PA_BOOST off            |
| RX current           | 10.3 mA     | Datasheet Table 7, LnaBoostHf on           |
| Idle                 | 0.2 µA    | Sleep mode (LongRangeMode=1, OpMode=Sleep)|
| Supply voltage       | 3.3 V       | Operating point                            |
| Effective throughput | 5.47 kbps   | SF=7, BW=125 kHz, no FEC (Datasheet §4.1)  |

**Notes & caveats:**
- SF=7 is the fastest spreading factor; SF=12 is much lower throughput
  and would worsen AmorE's case.
- PA_BOOST off limits TX to +14 dBm; PA_BOOST on can reach +20 dBm
  but pulls more current.
- Real LoRa networks (LoRaWAN) add MAC overhead, ACKs, duty-cycle limits.
  We model only the air time.

## How comm_projection.py uses these anchors

```python
ANCHORS = {
    "BLE_nRF52840": {
        "tx_current_mA": 4.8,
        "rx_current_mA": 4.6,
        "idle_current_uA": 1.5,
        "voltage_V": 3.0,
        "throughput_bps": 1_000_000,
    },
    "LoRa_SX1276_SF7": {
        "tx_current_mA": 28.0,
        "rx_current_mA": 10.3,
        "idle_current_uA": 0.2,
        "voltage_V": 3.3,
        "throughput_bps": 5470,
    },
}
```

## Energy per direction per round

```
E_TX = I_TX * V * (bytes_uplink * 8 / throughput_bps)    [Joules]
E_RX = I_RX * V * (bytes_downlink * 8 / throughput_bps)
E_round_comm = E_TX + E_RX
```

The projection is intentionally optimistic for the radio (excluding all
link-layer overhead). The point is to provide a lower bound of comm
energy that the paper's total = compute + comm calculation can use.

## Adding a new anchor

To add a new radio:

1. Record the datasheet values in this file (TX, RX, idle, V, throughput).
2. Cite the datasheet version + section.
3. Add an entry to the ANCHORS dict in analysis/comm_projection.py.
4. Run the projection: `python3 -m analysis.comm_projection --radio NEW_NAME`.
