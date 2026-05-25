#!/usr/bin/env python3
"""
Road-quality map — GPX route as a colored polyline (green = smoothest,
red = harshest) with the N most severe hits marked as numbered stars.

Per-segment colour comes from local vibration RMS (|a| - g) computed
in a sliding window aligned to each GPS point's wall-clock timestamp.
Top-N hits are picked from the gravity-removed magnitude with a
5-second dedup window so one sharp bump isn't counted as several.

Wall-clock alignment: each ART file's start time is parsed from its
filename (ART-YYYYMMDDHHMM.csv) and combined with its monotonic
timestamp_ms to recover the true wall-clock time of each sample.
That time is then matched against the GPX point times. This lets the
script handle multiple sessions of one push (with arbitrary breaks
between them) without ever touching the merged file.

Usage:
    # one or many ART files, all from the same push:
    python road_quality_map.py --gpx GPS-*.gpx ART-*.csv [ART-*.csv ...]
    # optional:
        --top N        number of top hits to mark (default 5)
        --out PATH     output PNG path (default RQM-<earliest>.png)
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize

GRAVITY = 9.81
WINDOW_S = 4.0       # vibration RMS window centered on each GPS point
DEDUP_GAP_S = 5.0    # min spacing between two "top hits"

# The phone's filename clock is local time (currently +02:00). GPX times are
# UTC. We assume +02:00 unless overridden by --tz-offset.
DEFAULT_TZ_OFFSET_H = 2


def parse_gpx(path):
    """Return list of (utc_epoch_s, lat, lon)."""
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    pts = []
    for trkpt in ET.parse(path).getroot().iter("{%s}trkpt" % ns["g"]):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        t_el = trkpt.find("{%s}time" % ns["g"])
        t = datetime.fromisoformat(t_el.text.replace("Z", "+00:00"))
        pts.append((t.timestamp(), lat, lon))
    return pts


def parse_filename_start(path: Path, tz_offset_h: int) -> float:
    """ART-YYYYMMDDHHMM.csv → wall-clock UTC epoch seconds."""
    m = re.search(r'(\d{12})', path.name)
    if not m:
        raise ValueError(f"cannot parse YYYYMMDDHHMM from {path.name}")
    dt_local = datetime.strptime(m.group(1), '%Y%m%d%H%M')
    dt_utc = dt_local.replace(tzinfo=timezone(timedelta(hours=tz_offset_h)))
    return dt_utc.timestamp()


def load_accel_with_wallclock(path: Path, tz_offset_h: int):
    """Return (utc_s, mag, vib) numpy arrays for accel rows in the file."""
    df = pd.read_csv(path, low_memory=False)
    a = df[df["sensor"] == "accel"].sort_values("timestamp_ms").reset_index(drop=True)
    if a.empty:
        return np.array([]), np.array([]), np.array([])
    t_ms = a["timestamp_ms"].to_numpy()
    # Filename gives the wall-clock minute the recording started. The first
    # sample sits within ~1 s of that mark. timestamp_ms is monotonic from
    # phone boot, so we use deltas from t_ms[0] to recover wall-clock for
    # every sample.
    start_utc = parse_filename_start(path, tz_offset_h)
    utc_s = start_utc + (t_ms - t_ms[0]) / 1000.0
    mag = np.sqrt(a["x"].to_numpy(float) ** 2
                + a["y"].to_numpy(float) ** 2
                + a["z"].to_numpy(float) ** 2)
    vib = np.abs(mag - GRAVITY)
    return utc_s, mag, vib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpx", required=True, help="GPS-*.gpx path")
    ap.add_argument("art", nargs="+", help="one or more ART-*.csv paths from the same push")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--tz-offset", type=int, default=DEFAULT_TZ_OFFSET_H,
                    help="local-time offset from UTC in hours (default 2)")
    args = ap.parse_args()

    art_paths = sorted(Path(p).expanduser() for p in args.art)
    gpx_path = Path(args.gpx).expanduser()
    out_path = args.out or art_paths[0].with_name(
        "RQM-" + art_paths[0].stem.replace("ART-", "") + ".png")

    # Load every ART file with wall-clock timestamps and stitch into one
    # absolute-time series (sorted by UTC).
    chunks = []
    for p in art_paths:
        utc_s, mag, vib = load_accel_with_wallclock(p, args.tz_offset)
        if len(utc_s) == 0:
            print(f"warning: no accel rows in {p.name}", file=sys.stderr)
            continue
        chunks.append((utc_s, mag, vib, p))
        print(f"loaded {p.name}: {len(utc_s):,} accel rows, "
              f"{utc_s[0]:.0f}…{utc_s[-1]:.0f} UTC "
              f"(duration {(utc_s[-1] - utc_s[0])/60:.1f} min)")

    if not chunks:
        raise SystemExit("no accel data across all inputs")

    utc_all = np.concatenate([c[0] for c in chunks])
    mag_all = np.concatenate([c[1] for c in chunks])
    vib_all = np.concatenate([c[2] for c in chunks])
    order = np.argsort(utc_all)
    utc_all, mag_all, vib_all = utc_all[order], mag_all[order], vib_all[order]

    gpx_pts = parse_gpx(gpx_path)
    gpx_utc = np.array([p[0] for p in gpx_pts])
    lats    = np.array([p[1] for p in gpx_pts])
    lons    = np.array([p[2] for p in gpx_pts])
    print(f"loaded {gpx_path.name}: {len(gpx_pts):,} GPS points")

    # Per-GPS-point RMS — sliding window over the accel signal aligned by
    # absolute UTC seconds.
    half = WINDOW_S / 2.0
    rms_per_gps = np.zeros(len(gpx_utc))
    for i, gt in enumerate(gpx_utc):
        lo = np.searchsorted(utc_all, gt - half)
        hi = np.searchsorted(utc_all, gt + half)
        if hi > lo:
            chunk = vib_all[lo:hi]
            rms_per_gps[i] = np.sqrt(np.mean(chunk * chunk))

    # How many GPS points fell inside an ART-covered window? (Diagnostic so
    # you can see if alignment was sane.)
    covered = int(np.sum(rms_per_gps > 0))
    print(f"GPS points with sensor coverage: {covered:,} / {len(gpx_utc):,} "
          f"({100*covered/len(gpx_utc):.0f}%)")

    # Top-N severe hits across ALL sessions — peak-magnitude with dedup
    order2 = np.argsort(vib_all)[::-1]
    kept_idx = []
    kept_t = []
    for idx in order2:
        t_here = utc_all[idx]
        if all(abs(t_here - kt) > DEDUP_GAP_S for kt in kept_t):
            kept_idx.append(idx)
            kept_t.append(t_here)
            if len(kept_idx) >= args.top:
                break

    # Map each kept hit to the nearest GPS point in time
    hit_locs = []
    push_start = utc_all[0]
    for idx in kept_idx:
        t_here = utc_all[idx]
        j = int(np.argmin(np.abs(gpx_utc - t_here)))
        offset_s = t_here - push_start
        hit_locs.append((lats[j], lons[j], float(mag_all[idx]),
                         float(vib_all[idx]), offset_s))

    # Colour scale — clamp to 5th–95th percentile of NON-zero RMS so
    # break/uncovered points don't squash the spread.
    nonzero = rms_per_gps[rms_per_gps > 0]
    if len(nonzero) > 0:
        vmin = float(np.percentile(nonzero, 5))
        vmax = float(np.percentile(nonzero, 95))
    else:
        vmin, vmax = 0.0, 1.0
    if vmax - vmin < 0.05:
        vmax = vmin + 0.05

    fig, ax = plt.subplots(figsize=(11, 11))

    pts_xy = np.column_stack([lons, lats]).reshape(-1, 1, 2)
    segments = np.concatenate([pts_xy[:-1], pts_xy[1:]], axis=1)
    seg_vals = 0.5 * (rms_per_gps[:-1] + rms_per_gps[1:])
    # Mark uncovered segments (both ends had 0 RMS) in grey rather than
    # green, so you can see where data is missing.
    covered_seg = (rms_per_gps[:-1] > 0) & (rms_per_gps[1:] > 0)
    seg_vals_masked = np.where(covered_seg, seg_vals, np.nan)
    norm = Normalize(vmin=vmin, vmax=vmax)
    lc = LineCollection(segments, cmap="RdYlGn_r", norm=norm, linewidth=4)
    lc.set_array(seg_vals_masked)
    ax.add_collection(lc)

    # Uncovered segments as thin grey overlay
    uncov_segments = segments[~covered_seg]
    if len(uncov_segments):
        gc = LineCollection(uncov_segments, colors="#bbbbbb", linewidth=1.5,
                            linestyles="--", zorder=2)
        ax.add_collection(gc)

    ax.plot(lons[0],  lats[0],  "o", color="white", markersize=11,
            markeredgecolor="black", markeredgewidth=1.2, zorder=4)
    ax.plot(lons[-1], lats[-1], "s", color="white", markersize=11,
            markeredgecolor="black", markeredgewidth=1.2, zorder=4)
    ax.annotate("START", (lons[0],  lats[0]),  textcoords="offset points",
                xytext=(10, 10), fontsize=9, fontweight="bold")
    ax.annotate("END",   (lons[-1], lats[-1]), textcoords="offset points",
                xytext=(10, 10), fontsize=9, fontweight="bold")

    for rank, (lat, lon, m, v, ts) in enumerate(hit_locs, 1):
        ax.plot(lon, lat, "*", color="black", markersize=28, zorder=5)
        ax.plot(lon, lat, "*", color="yellow", markersize=20, zorder=6)
        ax.annotate(f"#{rank}\n{m:.0f} m/s²",
                    xy=(lon, lat), xytext=(14, 14),
                    textcoords="offset points",
                    fontsize=10, fontweight="bold", color="black",
                    bbox=dict(boxstyle="round,pad=0.35", fc="yellow",
                              ec="black", lw=0.8, alpha=0.92),
                    zorder=7)

    mean_lat = float(lats.mean())
    ax.set_aspect(1.0 / np.cos(np.radians(mean_lat)))
    pad = 0.0005
    ax.set_xlim(lons.min() - pad, lons.max() + pad)
    ax.set_ylim(lats.min() - pad, lats.max() + pad)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    inputs_label = " + ".join(p.name for p in art_paths)
    ax.set_title(f"Road quality map  —  {inputs_label}\n"
                 f"colour = local vibration RMS over {WINDOW_S:.0f} s window  "
                 f"(green = smoothest, red = harshest)   "
                 f"★ = top {args.top} hits   "
                 f"dashed grey = no sensor coverage")
    ax.grid(True, alpha=0.3)

    cbar = plt.colorbar(lc, ax=ax, shrink=0.7, pad=0.01)
    cbar.set_label("vibration RMS (m/s²)")

    plt.tight_layout()
    plt.savefig(out_path, dpi=130)
    print(f"\nwrote {out_path}")
    print(f"colour scale: {vmin:.2f} – {vmax:.2f} m/s² RMS "
          f"(5th–95th percentile of covered points)")
    print(f"top {args.top} hits:")
    for rank, (lat, lon, m, v, ts) in enumerate(hit_locs, 1):
        mm = int(ts // 60); ss = ts % 60
        print(f"  #{rank}: {lat:.6f}, {lon:.6f}   "
              f"peak |a|={m:.1f} m/s²   "
              f"at {mm:02d}:{ss:05.2f} after push start")


if __name__ == "__main__":
    main()
