# Smoke 2026-05-28 — PASSED (4/4 cells)

## Reproduce
```bash
RPI_HOST=192.168.1.69 bash scripts/full_regression.sh --smoke --skip-analysis
```

## Commits (all pushed)
- orchestrator: `2a7f2e4`
- firmware:     `42fdefd`

## Works ✓
- Build 4 ELFs (BN254/BLS × A/B)
- Mode B both curves: status=0x600D0000, phase=0xff (124mA / 110mA)
- Mode A UART: honest accepted, malicious rejected (~73s/round)
- PPK2 accurate -5% (R33 cal), D-channels toggle {0,1,2,3}
- NRST hold-high (no reset-loop)

## NOT done yet
- Mode A FULL: smoke used --honest-rounds 1; firmware wants 61 (N={1,10,50}).
  status=0xDEAD0051 is EXPECTED in smoke, not a bug. Needs --honest-rounds 61 (~75min/cell).
- Multi-replica stats (smoke=1), phase-resolved energy analysis (--skip-analysis).

## Logs
`logs/full_regression_20260528_133440/`
- MASTER.log, FINAL_REPORT.txt, cell_*.log, telemetry/*.txt
- measurements/<cell>/run_001.csv (114-209MB, gitignored)

## Binary hashes (smoke build, regenerated each run)
- BN254-A  d1a2824e… amore_bn254.elf (g_results @0x20000030)
- BN254-B  a27fc0ee… relic_bench_bn254.elf (g_pb_results @0x200001bc)
- BLS-A    1de547a5… amore_bls12_381.elf (g_results @0x20000030)
- BLS-B    e4e57d25… relic_bench_bls12_381.elf (g_pb_results @0x200051e4)

## 3 gotchas
1. NRST must be HELD HIGH (gpioset setsid), never float → else 2.7ms reset-loop. (firmware doc/NRST_DISCOVERY.md)
2. PPK2 get_samples() digital byte broken (0xFF) → use decode_logic_bytes(). Current is fine.
3. RPi=192.168.1.69; server.py in /home/pi/amore-bn254-cortex-m4/rpi/
