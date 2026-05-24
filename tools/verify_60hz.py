#!/usr/bin/env python3
"""
OSI-013 verification helper.

Reads an ART CSV and prints accel-row counts for the four SRS windows.
PASS if the count is within 20 % of the 60 Hz target, FAIL otherwise.

Usage:
    python verify_60hz.py ~/Downloads/ART-202605241850.csv
"""
import sys
from pathlib import Path
import pandas as pd

TARGETS = [
    ("first 10 s, screen on",       10,         600),
    ("first 60 s, screen on",       60,        3600),
    ("first 120 s, screen LOCKED", 120,        7200),
    ("first 600 s, screen LOCKED", 600,       36000),
]
TOLERANCE = 0.20  # ±20 % counts as PASS


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        sys.exit(2)
    path = Path(argv[1]).expanduser()
    if not path.exists():
        sys.exit(f"file not found: {path}")
    df = pd.read_csv(path)
    accel = df[df["sensor"] == "accel"].sort_values("timestamp_ms").reset_index(drop=True)
    if accel.empty:
        sys.exit("no accel rows in file")
    t0 = accel["timestamp_ms"].iloc[0]
    span_s = (accel["timestamp_ms"].iloc[-1] - t0) / 1000.0

    print(f"file:  {path.name}")
    print(f"total accel rows: {len(accel):,}    recording span: {span_s:.1f} s")
    print()
    print(f"{'window':<30} {'rows':>8} {'target':>8} {'deviation':>10}  result")
    print("-" * 72)

    overall_pass = True
    for label, window_s, target in TARGETS:
        if span_s < window_s:
            print(f"{label:<30} {'n/a':>8} {target:>8} {'(short)':>10}  SKIP "
                  f"— recording is only {span_s:.0f} s")
            continue
        count = int(((accel["timestamp_ms"] - t0) <= window_s * 1000).sum())
        dev = (count - target) / target
        ok = abs(dev) <= TOLERANCE
        verdict = "PASS" if ok else "FAIL"
        if not ok:
            overall_pass = False
        print(f"{label:<30} {count:>8,} {target:>8,} {dev:>+9.0%}  {verdict}")

    print()
    if overall_pass:
        print("OSI-013: all windows within tolerance — permission fix WORKED.")
    else:
        print("OSI-013: at least one window failed — sensor stream still throttled.")
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main(sys.argv)
