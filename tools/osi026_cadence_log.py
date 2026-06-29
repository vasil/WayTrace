#!/usr/bin/env python3
"""
osi026_cadence_log.py — wheelchair push-cadence (pushes per minute) from
the ART accelerometer time series. One row per second of ART time, written
to the same SQLite metrics database as osi025_speed_log.py.

Algorithm:
  • Load accelerometer rows (sensor=='accelerometer') from the ART CSV.
  • Resample to a uniform 50 Hz grid (most ART files run 100-200 Hz; 50 Hz
    is plenty for a 0.5–2.5 Hz signal and 4× faster).
  • Take the magnitude (|a|) so we are insensitive to phone orientation
    (back-pocket vs frame mount don't matter; new-mount tests too).
  • Bandpass 0.5–2.5 Hz (Butterworth order 4) — the documented "push
    rhythm" band already used in waytrace_analysis.dominant_band.
  • scipy.signal.find_peaks with height threshold + minimum distance.
    Each positive peak = one push stroke.
  • For each second, count peaks in the surrounding 10 s window, multiply
    by 60/10 to get pushes per minute.

Joins to osi025 speed_samples by (push_ts, art_t_s) — same column name.
The two time axes are not perfectly aligned (ART starts a few seconds
after Strava typically), but on the scale of a 100-min push that drift
is invisible. If you need precise alignment, use the sync chime offset.

Usage:
  osi026_cadence_log.py --art ART-202606280835.csv [--push-ts 202606280835]
  osi026_cadence_log.py --backfill ~/Downloads/ART-*.csv*
"""
import argparse
import csv
import glob
import gzip
import os
import re
import sqlite3
import sys
from pathlib import Path

import numpy as np
from scipy import signal

# Same metrics DB as osi025.
sys.path.insert(0, str(Path(__file__).parent))
from osi025_speed_log import DEFAULT_DB  # noqa: E402

BAND_LO = 0.5           # Hz
BAND_HI = 2.5           # Hz
RESAMPLE_HZ = 50.0      # Hz
PEAK_MIN_HEIGHT = 0.5   # m/s² above filtered baseline
PEAK_MIN_DIST_S = 0.30  # seconds (max effective cadence ≈ 200 ppm)
# Sliding-window length for the per-second cadence reading. 10 s is too
# jumpy at the per-second view; 60 s smooths over individual push pauses
# and matches Vasil's "rate over one or two minutes" preference.
# Override per-run with env OSI026_WINDOW_S or CLI flag --window-s.
WINDOW_S = float(os.environ.get("OSI026_WINDOW_S", "60.0"))


def init_cadence_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS cadence_samples (
        push_ts     TEXT NOT NULL,
        art_t_s     REAL NOT NULL,
        cadence_ppm REAL,
        PRIMARY KEY(push_ts, art_t_s)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_cadence_push
        ON cadence_samples(push_ts, art_t_s)""")
    con.commit()


def open_maybe_gz(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", newline="")
    return open(path, "r", newline="")


def load_accel(art_path):
    """Return (t_s, ax, ay, az) ndarrays of just accelerometer rows.
    t_s is seconds since the first row of the ART CSV (not just first accel)."""
    t_arr, x_arr, y_arr, z_arr = [], [], [], []
    t0 = None
    with open_maybe_gz(art_path) as f:
        r = csv.reader(f)
        header = next(r)
        # Expected header: timestamp_ms,sensor,x,y,z,rotvec_w
        # We only need cols 0..4
        for row in r:
            if not row:
                continue
            try:
                ts = int(row[0])
            except ValueError:
                continue
            if t0 is None:
                t0 = ts
            # ART logger uses short sensor names: accel / gyro / gravity /
            # mag / rotvec / sync_pulse. Be tolerant of both shapes.
            if row[1] not in ("accel", "accelerometer"):
                continue
            try:
                ax = float(row[2]); ay = float(row[3]); az = float(row[4])
            except (ValueError, IndexError):
                continue
            t_arr.append((ts - t0) / 1000.0)
            x_arr.append(ax); y_arr.append(ay); z_arr.append(az)
    return (np.asarray(t_arr), np.asarray(x_arr),
            np.asarray(y_arr), np.asarray(z_arr))


def compute_cadence(t_s, ax, ay, az, window_s=WINDOW_S):
    """Return (t_grid_s, cadence_ppm) where t_grid is 1-second steps and
    cadence_ppm is the pushes-per-minute estimate centred on each second
    over a `window_s` second window. Default 60 s = one-minute rate."""
    if len(t_s) < 100:
        return np.array([]), np.array([])
    # 1) magnitude — orientation-invariant
    mag = np.sqrt(ax * ax + ay * ay + az * az)
    # 2) uniform resample @ RESAMPLE_HZ
    t_uniform = np.arange(t_s[0], t_s[-1], 1.0 / RESAMPLE_HZ)
    mag_u = np.interp(t_uniform, t_s, mag)
    # 3) high-pass to drop gravity DC, then bandpass to push rhythm
    sos = signal.butter(4, [BAND_LO, BAND_HI], btype="bandpass",
                        fs=RESAMPLE_HZ, output="sos")
    filt = signal.sosfiltfilt(sos, mag_u)
    # 4) peak detect — positive peaks of the push spike
    min_dist_samples = max(1, int(PEAK_MIN_DIST_S * RESAMPLE_HZ))
    peaks, _ = signal.find_peaks(filt, height=PEAK_MIN_HEIGHT,
                                 distance=min_dist_samples)
    peak_times_s = t_uniform[peaks]
    # 5) per-second sliding window count
    t_min = int(np.ceil(t_s[0]))
    t_max = int(np.floor(t_s[-1]))
    t_grid = np.arange(t_min, t_max + 1, dtype=float)
    half = window_s / 2.0
    cadence_ppm = np.zeros_like(t_grid)
    # Use np.searchsorted for O(N log N) sliding count
    for i, t in enumerate(t_grid):
        lo = np.searchsorted(peak_times_s, t - half, side="left")
        hi = np.searchsorted(peak_times_s, t + half, side="right")
        cadence_ppm[i] = (hi - lo) * 60.0 / window_s
    return t_grid, cadence_ppm


def derive_push_ts(art_path):
    name = Path(art_path).name
    m = re.search(r"(\d{12})", name)
    return m.group(1) if m else None


def ingest_art(con, art_path, push_ts=None):
    if push_ts is None:
        push_ts = derive_push_ts(art_path)
    if push_ts is None:
        sys.exit(f"cannot derive push_ts from {art_path} — pass --push-ts")
    t_s, ax, ay, az = load_accel(art_path)
    if len(t_s) == 0:
        return push_ts, 0, 0
    t_grid, cad = compute_cadence(t_s, ax, ay, az)
    init_cadence_table(con)
    rows = [(push_ts, float(t), float(c)) for t, c in zip(t_grid, cad)]
    con.executemany("""INSERT OR REPLACE INTO cadence_samples
        (push_ts, art_t_s, cadence_ppm) VALUES (?,?,?)""", rows)
    con.commit()
    # Summary stats
    total_pushes = int(cad.sum() * (WINDOW_S / 60.0) / WINDOW_S * len(cad))
    # Simpler: count distinct peaks across the whole signal
    peaks_total = int(round(cad.mean() * (t_grid[-1] - t_grid[0]) / 60.0))
    return push_ts, len(rows), peaks_total


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--art",      help="ingest one ART CSV (or .csv.gz)")
    g.add_argument("--backfill", nargs="+",
                   help="ingest many ART CSVs (e.g. ~/Downloads/ART-*.csv*)")
    g.add_argument("--query",    help="dump cadence rows for a push_ts")
    global WINDOW_S
    ap.add_argument("--push-ts", help="override push timestamp")
    ap.add_argument("--db",      default=DEFAULT_DB)
    ap.add_argument("--window-s", type=float, default=WINDOW_S,
                    help=f"sliding window seconds (default {WINDOW_S})")
    args = ap.parse_args()
    WINDOW_S = args.window_s

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(args.db)
    init_cadence_table(con)

    if args.art:
        pt, n, total = ingest_art(con, args.art, args.push_ts)
        print(f"{pt}: {n} cadence rows, ≈{total} total pushes  → {args.db}")

    elif args.backfill:
        paths = []
        for p in args.backfill:
            if "*" in p or "?" in p:
                paths.extend(sorted(glob.glob(p)))
            else:
                paths.append(p)
        # ART files matching merged ones can be skipped to avoid double-counting
        seen = set()
        total_pushes = 0
        for p in paths:
            try:
                pt, n, total = ingest_art(con, p)
            except Exception as e:
                print(f"  skip {p}: {e}")
                continue
            if pt in seen:
                print(f"  {pt}: already ingested (skipped)")
                continue
            seen.add(pt)
            total_pushes += total
            print(f"  {pt}: +{n} rows  ≈{total} pushes")
        print(f"\nbackfill complete: {len(seen)} pushes  ≈{total_pushes} pushes total")

    elif args.query:
        rows = con.execute("""SELECT art_t_s, cadence_ppm FROM cadence_samples
            WHERE push_ts=? ORDER BY art_t_s""", (args.query,)).fetchall()
        if not rows:
            print(f"no rows for push_ts={args.query}")
            return
        print(f"# push_ts={args.query}  samples={len(rows)}")
        print("art_t_s,cadence_ppm")
        for t, c in rows:
            print(f"{t:.0f},{c:.1f}")

    n = con.execute("SELECT COUNT(*) FROM cadence_samples").fetchone()[0]
    p = con.execute("SELECT COUNT(DISTINCT push_ts) FROM cadence_samples").fetchone()[0]
    print(f"\nDB total: {p} pushes, {n} cadence samples  ({args.db})")


if __name__ == "__main__":
    main()
