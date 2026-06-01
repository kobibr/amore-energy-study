# AmorE Energy Study

Energy measurement of the AmorE protocol (amortized remote pairing
evaluation) on a Cortex-M4 STM32F407 client, compared 1:1 against a
single RELIC local pairing, in BN254 and BLS12-381.

## Headline result

    Curve       AmorE energy/round   1 x RELIC pairing   Ratio   Result
    ---------   ------------------   -----------------   -----   ----------------------
    BN254          160.16 mJ            85.27 mJ          1.88x   AmorE costs 1.88x more
    BLS12-381      354.04 mJ           180.42 mJ          1.96x   AmorE costs 1.96x more

Compute-only, phase-aware energy on STM32F407 at 168 MHz, pure-C build
(AmorE -O3, RELIC ARITH=easy -O3, no assembly). Current is measured
during the compute phase (GPIO bit0), not the full-trace median.
Comparison is 1:1 per the AmorE paper (one delegation vs one local
pairing). On a Cortex-M4 without assembly, AmorE costs ~1.9x the energy
and ~1.7-1.9x the time of a local pairing; its value is memory
footprint, pairing-library avoidance, and verifiable outsourcing - not
speed or energy. All 24 measurement cells terminated with
`status = 0x600D0000`. Full results in `docs/FINAL_RESULTS_20260531.md`.

## Hardware setup

    Client    STM32F407G-DISC1 (168 MHz, Cortex-M4 + FPU)
    Server    Raspberry Pi 3B (py_ecc 8.0.0)
    UART      STM32 USART2 (PA2/PA3) to RPi GPIO 14/15, 921600 baud
    SWD       RPi GPIO 25/24 to STM32 SWCLK/SWDIO
    NRST      RPi GPIO 18 to STM32 NRST (held high, see
              firmware/amore-fw/doc/NRST_DISCOVERY.md)
    Power     Nordic Power Profiler Kit II (PPK2), source-meter at
              3.300 V, R33 calibration -5%

## Repository layout

    amore-energy-study/
    +-- analysis/             Python analysis pipeline (reads logs/ only)
    |   +-- compute_energy.py           phase-aware energy from logs/
    +-- docs/
    |   +-- FINAL_RESULTS_20260531.md   results
    |   +-- methodology.md              how the measurements work
    |   +-- known_caveats.md            measurement uncertainty bounds
    |   +-- audit_table.md              per-claim source mapping
    +-- firmware/amore-fw/    submodule, github.com/kobibr/amore-bn254-cortex-m4
    +-- scripts/
    |   +-- full_regression.sh          orchestrator entry point
    |   +-- measure_one_cell.py         per-cell PPK2 + SWD owner
    +-- measurement/
    |   +-- calibration-logs/           R33 calibration evidence
    +-- logs/                 per-run captures (CSVs gitignored)

## Reproducing the measurement

PPK2 connected to host USB, STM32 wired to RPi via SWD + UART per
above, RPi reachable as `pi@192.168.1.69` with passwordless SSH:

    RPI_HOST=192.168.1.69 bash scripts/full_regression.sh \
        --replicas=6 --curves=BN254,BLS12_381 --modes=A,B --honest-rounds=61

Then compute energy from the captured logs:

    python3 analysis/compute_energy.py logs/full_regression_<timestamp>

Each cell writes to `logs/full_regression_<timestamp>/measurements/
<cell>/run_001.csv` and `telemetry.txt`. The `state.json` checkpoint
allows `--resume` after interruption.

## Related repositories

    Firmware    github.com/kobibr/amore-bn254-cortex-m4    HEAD 42fdefd
                AmorE protocol implementation and RELIC bench harnesses
                for both BN254 and BLS12-381.

## License

See LICENSE.
