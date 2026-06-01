"""Compute timing + energy statistics across all 23 cells from overnight."""
import csv, glob, statistics, os, re
from collections import defaultdict

F_MHZ = 168
LOG_DIRS = [
    "logs/full_regression_20260528_205020",  # BLS-A
    "logs/full_regression_20260528_222615",  # B × 10 reps
    "logs/full_regression_20260529_033031",  # BN254-A × 2
    "logs/full_regression_20260528_145444",  # BN254-A × 1 (from yesterday) - 3rd rep
]

def parse_telem(path):
    d = {}
    if not os.path.exists(path): return d
    for line in open(path):
        m = re.match(r"^\s*(\w+)\s*=\s*0x([0-9a-fA-F]+)", line)
        if m: d[m.group(1)] = int(m.group(2), 16)
        m = re.match(r"^\s*(\w+)\s*=\s*(\d+)", line)
        if m: d[m.group(1)] = int(m.group(2))
        m = re.match(r"^\s*\[N=(\d+)\]\s*blind_total=(\d+)\s+verify_total=(\d+)\s+amort=(\d+)", line)
        if m:
            n = int(m.group(1))
            d[f"blind_N{n}"]  = int(m.group(2))
            d[f"verify_N{n}"] = int(m.group(3))
            d[f"amort_N{n}"]  = int(m.group(4))
        m = re.match(r"^\s*pairing_min\s*=\s*(\d+)", line)
        if m: d["pairing_min"] = int(m.group(1))
    return d

def csv_median_mA(csv_path, max_rows=None):  # full-read by default
    """Median current in mA, filtered to <200mA (drop range-switch spikes)."""
    vals = []
    if not os.path.exists(csv_path): return None
    with open(csv_path) as f:
        r = csv.reader(f); next(r, None)
        for i, row in enumerate(r):
            if max_rows and i > max_rows: break
            try:
                c = float(row[1])
                if 0 < c < 200000: vals.append(c)
            except: pass
    if not vals: return None
    return statistics.median(vals) / 1000  # to mA

# Collect per-cell data
cells = defaultdict(list)  # key=(curve,mode) -> list of (telem, median_mA)
for LD in LOG_DIRS:
    tels = sorted(glob.glob(f"{LD}/telemetry/*.txt"))
    for tf in tels:
        cell = os.path.basename(tf).replace(".txt","")
        # cell name like "bn254__A__r1" or "bls12_381__B__r3"
        m = re.match(r"(\w+)__([AB])__r(\d+)", cell)
        if not m: continue
        curve, mode, rep = m.group(1), m.group(2), int(m.group(3))
        telem = parse_telem(tf)
        csv_path = f"{LD}/measurements/{cell}/run_001.csv"
        median = csv_median_mA(csv_path, max_rows=None)  # FIXED: full CSV (500k truncation biased current)
        cells[(curve.upper().replace("BLS12_381","BLS"), mode)].append({
            "rep": rep, "telem": telem, "median_mA": median
        })

print("="*70)
print(f"{'Cell':12} {'N':>3}  {'median mA (mean±stdev)':>26}  {'wall (mean±stdev)':>22}")
print("="*70)
for key in sorted(cells):
    curve, mode = key
    reps = cells[key]
    n = len(reps)
    if n == 0: continue
    medians = [r["median_mA"] for r in reps if r["median_mA"] is not None]
    walls = [r["telem"].get("wall_ms",0)/1000.0 for r in reps if r["telem"].get("wall_ms",0)>0]
    label = f"{curve}-{mode}"
    if medians:
        mm = statistics.mean(medians)
        ms = statistics.stdev(medians) if len(medians)>1 else 0
    else: mm=ms=0
    if walls:
        wm = statistics.mean(walls); ws = statistics.stdev(walls) if len(walls)>1 else 0
        wstr = f"{wm:>7.1f} ± {ws:>5.1f} s"
    else:
        wstr = "(no wall_ms)"
    print(f"  {label:10} {n:>3}  {mm:>11.2f} ± {ms:>5.2f} mA      {wstr:>22}")
print()

# Mode B per-pairing cycles
print("="*70)
print("MODE B: per-pairing cycles (10 reps mean ± stdev)")
print("="*70)
for curve in ["BN254", "BLS"]:
    reps = cells.get((curve,"B"), [])
    pms = [r["telem"].get("pairing_min") for r in reps if r["telem"].get("pairing_min")]
    if pms:
        mc = statistics.mean(pms); sc = statistics.stdev(pms) if len(pms)>1 else 0
        ms = mc/(F_MHZ*1e6)*1000
        ss = sc/(F_MHZ*1e6)*1000
        print(f"  {curve}-B: pair_min = {mc:>14,.0f} ± {sc:>8,.0f} cycles  "
              f"= {ms:>7.3f} ± {ss:>6.3f} ms")

# Mode A per-round cycles
print()
print("="*70)
print("MODE A: per-round cycles by batch (mean over replicas)")
print("="*70)
for curve in ["BN254", "BLS"]:
    reps = cells.get((curve,"A"), [])
    if not reps: continue
    print(f"  {curve}-A (n={len(reps)} replicas):")
    for N in [1, 10, 50]:
        bls = [r["telem"].get(f"blind_N{N}",0)/N for r in reps]
        vfs = [r["telem"].get(f"verify_N{N}",0)/N for r in reps]
        ams = [r["telem"].get(f"amort_N{N}",0)/(F_MHZ*1e6)*1000 for r in reps]
        if bls and bls[0]>0:
            bms = statistics.mean(bls)/(F_MHZ*1e6)*1000
            vms = statistics.mean(vfs)/(F_MHZ*1e6)*1000
            am  = statistics.mean(ams)
            print(f"    N={N:<3}  blind/rnd={bms:>7.2f} ms   verify/rnd={vms:>7.2f} ms   amort/rnd={am:>7.2f} ms")
