# Audit Table - AmorE Energy Study

Each row is a numerical claim that appears in the results report.
Status: `measured` = direct PPK2 capture; `computed` = derived from
measured cycles and current; `referenced` = independent measurement
reproduced here. Run: full_regression_20260530_092609 (24 cells,
6 replicas per cell). Energy is phase-aware (compute-phase current,
GPIO bit0), produced by analysis/compute_energy.py from logs/ only.

## Pairing timing (both curves)

    Claim                                       Value                  Status         Source
    -----------------------------------------   --------------------   ------------   --------------------------------
    BN254  RELIC pair_min cycles (n=6)          36,778,389 +/- 0       measured       Mode B telemetry, 6 replicas
    BN254  RELIC pairing time                   218.92  +/- 0.00 ms    computed       cycles / 168 MHz
    BLS    RELIC pair_min cycles (n=6)          87,932,879 +/- 0       measured       Mode B telemetry, 6 replicas
    BLS    RELIC pairing time                   523.41  +/- 0.00 ms    computed       cycles / 168 MHz
    BLS    RELIC reference (Diego 2026-05-13)   87,933,033 cyc avg     referenced     independent measurement
    BN254  AmorE amort/round (N=50, n=6)        70,813,093 cyc / 421.5 ms  measured   Mode A telemetry
    BLS    AmorE amort/round (N=50, n=6)        151,357,860 cyc / 901.0 ms measured   Mode A telemetry

## Current (median, after R33 calibration -5%)

    Claim                                       Value                  Status         Source
    -----------------------------------------   --------------------   ------------   --------------------------------
    BN254  RELIC pairing current (Mode B, n=6)  118.04 +/- 0.36 mA     measured       PPK2 CSV (full-stream median)
    BLS    RELIC pairing current (Mode B, n=6)  104.45 +/- 0.00 mA     measured       PPK2 CSV (full-stream median)
    BN254  AmorE  compute current (Mode A, n=6) 115.14 +/- 1.28 mA     measured       PPK2 CSV (GPIO bit0 = compute)
    BLS    AmorE  compute current (Mode A, n=6) 119.08 +/- 2.18 mA     measured       PPK2 CSV (GPIO bit0 = compute)
    --     AmorE  busy-wait current (Mode A)    103.03 mA              measured       PPK2 CSV (GPIO bit1 = wait)

Note: AmorE energy uses the compute-phase current (bit0), not the
full-trace median (~105 mA, dominated by busy-wait). The busy-wait
current is ~103 mA, distinctly lower than the compute current.

## Energy (compute-only, phase-aware, E = compute_cycles/F x I_compute x V at V=3.300 V)

    Claim                                       Value             Status         Source
    -----------------------------------------   ---------------   ------------   --------------------------------
    BN254  RELIC energy per pairing              85.27 mJ         computed       cycles x I_pairing x V
    BLS    RELIC energy per pairing             180.42 mJ         computed       cycles x I_pairing x V
    BN254  AmorE  energy per amortized round    160.16 mJ         computed       cycles x I_compute x V
    BLS    AmorE  energy per amortized round    354.04 mJ         computed       cycles x I_compute x V

## Comparison ratios (1:1, per AmorE paper Definition 4 / Section 7.2)

    Claim                                       Value         Status         Notes
    -----------------------------------------   -----------   ------------   --------------------------------
    BN254  AmorE / 1 RELIC  energy ratio         1.88x        computed       AmorE costs 1.88x more
    BLS    AmorE / 1 RELIC  energy ratio         1.96x        computed       AmorE costs 1.96x more
    BN254  AmorE / 1 RELIC  time ratio           1.92x        computed       421.5 / 218.92
    BLS    AmorE / 1 RELIC  time ratio           1.72x        computed       901.0 / 523.41
    BLS / BN254  RELIC pairing ratio             2.39x        computed       limb-count expansion
    BLS / BN254  AmorE  amortized round ratio    2.14x        computed       same backend both sides

## Calibration

    Claim                                       Value         Status         Source
    -----------------------------------------   -----------   ------------   --------------------------------
    PPK2 absolute current accuracy              n/a           not measured   no reference resistor; ratios robust
                                                                             measurement/calibration-logs/
                                                                             calibration_20260528_clean_R33.txt
