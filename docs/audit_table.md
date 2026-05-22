# Audit Table — AmorE Energy Study

Compiled by `analysis/audit_table.py`. Each claim is
annotated with status (measured / computed / pending)
and the source file it draws from.

## Comm

| Claim | Value | Status | Source | Notes |
|-------|-------|--------|--------|-------|
| Comm energy per round (AmorE, BLE_nRF52840) | 0.194 mJ | ≈ computed | `comm_projection.py (datasheet)` | constant across N |
| Comm energy N=50 (Direct, BLE_nRF52840) | 4.009 mJ | ≈ computed | `comm_projection.py (datasheet)` |  |
| Comm-only crossover N (BLE_nRF52840) | 2.42 pairings | ≈ computed | `comm_projection.py (datasheet)` |  |
| Comm energy per round (AmorE, LoRa_SX1276_SF7) | 135.291 mJ | ≈ computed | `comm_projection.py (datasheet)` | constant across N |
| Comm energy N=50 (Direct, LoRa_SX1276_SF7) | 2404.668 mJ | ≈ computed | `comm_projection.py (datasheet)` |  |
| Comm-only crossover N (LoRa_SX1276_SF7) | 2.81 pairings | ≈ computed | `comm_projection.py (datasheet)` |  |

## Electrical

| Claim | Value | Status | Source | Notes |
|-------|-------|--------|--------|-------|
| I @ 3.0V active | 103.67 ± 19.42 mA | ✓ measured | `voltage_20260520_143324.txt` |  |
| P @ 3.0V active | 311.01 mW | ✓ measured | `voltage_20260520_143324.txt` |  |
| I @ 3.3V active | 138.71 ± 25.01 mA | ✓ measured | `voltage_20260520_143324.txt` |  |
| P @ 3.3V active | 457.73 mW | ✓ measured | `voltage_20260520_143324.txt` |  |
| I @ 3.6V active | 145.28 ± 26.64 mA | ✓ measured | `voltage_20260520_143324.txt` |  |
| P @ 3.6V active | 523.01 mW | ✓ measured | `voltage_20260520_143324.txt` |  |
| I @ 3.3V active | 143.80 ± 25.27 mA | ✓ measured | `voltage_20260520_144105.txt` |  |
| P @ 3.3V active | 474.54 mW | ✓ measured | `voltage_20260520_144105.txt` |  |
| I @ 3.3V active | 141.09 ± 24.36 mA | ✓ measured | `voltage_20260520_144532.txt` |  |
| P @ 3.3V active | 465.59 mW | ✓ measured | `voltage_20260520_144532.txt` |  |
| I @ 3.3V active | 138.74 ± 23.83 mA | ✓ measured | `voltage_20260520_164513.txt` |  |
| P @ 3.3V active | 457.84 mW | ✓ measured | `voltage_20260520_164513.txt` |  |

## Metrology

| Claim | Value | Status | Source | Notes |
|-------|-------|--------|--------|-------|
| Calibration evidence on file | present | ✓ measured | `calibration_20260520_113657_R33.txt` | See file for resistor value + ratio |
