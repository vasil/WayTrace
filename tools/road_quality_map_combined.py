#!/usr/bin/env python3
"""
Combined road-quality map across multiple pushes.

Takes one or more (GPX, ART...) pairs and overlays them on a single map.
Each polyline is coloured by local vibration RMS so repeated traversals
of the same street average out into a stable per-street roughness view.

Usage:
    road_quality_map_combined.py \\
        --pair GPS-A.gpx ART-A.csv \\
        --pair GPS-B.gpx ART-B1.csv ART-B2.csv \\
        --out RQM-combined.png

Each --pair group is one push: one GPX plus one or more ART files from
that push.

Wall-clock alignment follows tools/road_quality_map.py.
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
WINDOW_S = 4.0
DEDUP_GAP_S = 5.0
DEFAULT_TZ_OFFSET_H = 2


def parse_gpx(path):
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    pts = []
    for trkpt in ET.parse(path).getroot().iter("{%s}trkpt" % ns["g"]):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        t_el = trkpt.find("{%s}time" % ns["g"])
        t = datetime.fromisoformat(t_el.text.replace("Z", "+00:00"))
        pts.append((t.timestamp(), lat, lon))
    return pts


def parse_filename_start(path, tz_offset_h):
    m = re.search(r'(\d{12})', path.name)
    if not m:
        raise ValueError(f"cannot parse YYYYMMDDHHMM from {path.name}")
    dt = datetime.strptime(m.group(1), '%Y%m%d%H%M')
    dt = dt.replace(tzinfo=timezone(timedelta(hours=tz_offset_h)))
    return dt.timestamp()


def load_accel(path, tz):
    df = pd.read_csv(path, low_memory=False)
    a = df[df["sensor"] == "accel"].sort_values("timestamp_ms").reset_index(drop=True)
    if a.empty:
        return np.array([]), np.array([]), np.array([])
    t_ms = a["timestamp_ms"].to_numpy()
    start = parse_filename_start(path, tz)
    utc_s = start + (t_ms - t_ms[0]) / 1000.0
    mag = np.sqrt(a["x"].to_numpy(float) ** 2
                + a["y"].to_numpy(float) ** 2
                + a["z"].to_numpy(float) ** 2)
    vib = np.abs(mag - GRAVITY)
    return utc_s, mag, vib


def push_rms_series(art_paths, gpx_path, tz):
    chunks = []
    for p in art_paths:
        u, m, v = load_accel(p, tz)
        if len(u):
            chunks.append((u, m, v))
    if not chunks:
        return None, None, None, None, None
    utc = np.concatenate([c[0] for c in chunks])
    mag = np.concatenate([c[1] for c in chunks])
    vib = np.concatenate([c[2] for c in chunks])
    order = np.argsort(utc)
    utc, mag, vib = utc[order], mag[order], vib[order]

    pts = parse_gpx(gpx_path)
    gpx_utc = np.array([p[0] for p in pts])
    lats = np.array([p[1] for p in pts])
    lons = np.array([p[2] for p in pts])

    half = WINDOW_S / 2.0
    rms = np.zeros(len(gpx_utc))
    for i, gt in enumerate(gpx_utc):
        lo = np.searchsorted(utc, gt - half)
        hi = np.searchsorted(utc, gt + half)
        if hi > lo:
            ch = vib[lo:hi]
            rms[i] = np.sqrt(np.mean(ch * ch))
    return lats, lons, rms, utc, vib


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", action="append", nargs="+", required=True,
                    metavar="GPX ART [ART ...]",
                    help="one GPX path then one+ ART paths (repeat for each push)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--title", default="Road quality map — combined Skopje pushes")
    ap.add_argument("--tz-offset", type=int, default=DEFAULT_TZ_OFFSET_H)
    args = ap.parse_args()

    pushes = []
    for group in args.pair:
        if len(group) < 2:
            sys.exit(f"--pair needs GPX + at least one ART (got {group})")
        gpx_p = Path(group[0]).expanduser()
        art_ps = [Path(p).expanduser() for p in group[1:]]
        lats, lons, rms, utc, vib = push_rms_series(art_ps, gpx_p, args.tz_offset)
        if lats is None:
            print(f"skip: no accel data in {[p.name for p in art_ps]}", file=sys.stderr)
            continue
        pushes.append({
            "gpx": gpx_p, "arts": art_ps,
            "lats": lats, "lons": lons, "rms": rms,
            "utc": utc, "vib": vib,
        })
        print(f"loaded {gpx_p.name}: {len(lats):,} GPS pts; "
              f"ART={','.join(p.name for p in art_ps)}; "
              f"covered={int(np.sum(rms > 0)):,}")

    if not pushes:
        sys.exit("no pushes loaded")

    all_rms = np.concatenate([p["rms"][p["rms"] > 0] for p in pushes])
    vmin = float(np.percentile(all_rms, 5))
    vmax = float(np.percentile(all_rms, 95))
    if vmax - vmin < 0.05:
        vmax = vmin + 0.05
    print(f"colour scale: {vmin:.2f}–{vmax:.2f} m/s² RMS "
          f"(5th–95th pct across {len(all_rms):,} covered points, all pushes)")

    fig, ax = plt.subplots(figsize=(14, 14))
    norm = Normalize(vmin=vmin, vmax=vmax)

    for p in pushes:
        lats, lons, rms = p["lats"], p["lons"], p["rms"]
        pts_xy = np.column_stack([lons, lats]).reshape(-1, 1, 2)
        segments = np.concatenate([pts_xy[:-1], pts_xy[1:]], axis=1)
        seg_vals = 0.5 * (rms[:-1] + rms[1:])
        covered = (rms[:-1] > 0) & (rms[1:] > 0)
        seg_vals_m = np.where(covered, seg_vals, np.nan)
        lc = LineCollection(segments, cmap="RdYlGn_r", norm=norm, linewidth=3.5,
                            alpha=0.85)
        lc.set_array(seg_vals_m)
        ax.add_collection(lc)

    # Top-N across all pushes combined
    rows = []
    for p in pushes:
        for i, v in enumerate(p["vib"]):
            rows.append((float(v), p["utc"][i], p["lats"], p["lons"], p["utc"], i,
                         p["gpx"].stem))
    # Sort by vib desc; dedup within DEDUP_GAP_S across pushes (using utc time)
    # but allow same lat/lon to appear once per push since they're separate days
    by_strength = sorted(((p, idx) for p in pushes for idx in range(len(p["vib"]))),
                        key=lambda x: -x[0]["vib"][x[1]])
    kept = []
    for p, idx in by_strength:
        t_here = p["utc"][idx]
        push_id = id(p)
        if any(pid == push_id and abs(t_here - kt) < DEDUP_GAP_S
               for pid, kt in kept):
            continue
        # Find nearest GPX point in time
        j = int(np.argmin(np.abs(p["utc"][idx] -
            np.interp(np.arange(len(p["lats"])),
                      np.arange(len(p["lats"])),
                      np.arange(len(p["lats"]))) * 0 +
            (p["utc"][idx]))))  # placeholder; we want GPX time alignment
        # Better: rebuild gpx_utc per push
        break
    # Re-do top-N cleanly per push then merge
    hits = []
    for p in pushes:
        gpx_pts = parse_gpx(p["gpx"])
        gpx_utc = np.array([gp[0] for gp in gpx_pts])
        order2 = np.argsort(p["vib"])[::-1]
        kept_t = []
        for idx in order2:
            t_here = p["utc"][idx]
            if all(abs(t_here - kt) > DEDUP_GAP_S for kt in kept_t):
                kept_t.append(t_here)
                j = int(np.argmin(np.abs(gpx_utc - t_here)))
                hits.append((float(p["vib"][idx]) + GRAVITY, p["lats"][j], p["lons"][j],
                             p["gpx"].stem))
                if len(kept_t) >= 3:  # 3 per push max
                    break
    hits.sort(key=lambda h: -h[0])
    hits = hits[:args.top]

    for rank, (peak, lat, lon, label) in enumerate(hits, 1):
        ax.plot(lon, lat, "*", color="black", markersize=24, zorder=5)
        ax.plot(lon, lat, "*", color="yellow", markersize=17, zorder=6)
        ax.annotate(f"#{rank}\n{peak:.0f}",
                    xy=(lon, lat), xytext=(10, 10),
                    textcoords="offset points",
                    fontsize=9, fontweight="bold", color="black",
                    bbox=dict(boxstyle="round,pad=0.3", fc="yellow",
                              ec="black", lw=0.7, alpha=0.92),
                    zorder=7)

    all_lats = np.concatenate([p["lats"] for p in pushes])
    all_lons = np.concatenate([p["lons"] for p in pushes])
    mean_lat = float(all_lats.mean())
    ax.set_aspect(1.0 / np.cos(np.radians(mean_lat)))
    pad = 0.001
    ax.set_xlim(all_lons.min() - pad, all_lons.max() + pad)
    ax.set_ylim(all_lats.min() - pad, all_lats.max() + pad)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(f"{args.title}\n"
                 f"{len(pushes)} pushes overlaid · colour = local vibration RMS "
                 f"(green = smoothest, red = harshest) · ★ = top {args.top} hits")
    ax.grid(True, alpha=0.3)

    sm = plt.cm.ScalarMappable(cmap="RdYlGn_r", norm=norm)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax, shrink=0.7, pad=0.01)
    cbar.set_label("vibration RMS (m/s²)")

    plt.tight_layout()
    plt.savefig(args.out, dpi=300)
    print(f"\nwrote {args.out}")
    print(f"top {len(hits)} hits across all pushes:")
    for rank, (peak, lat, lon, label) in enumerate(hits, 1):
        print(f"  #{rank}: {lat:.6f}, {lon:.6f}   peak |a|={peak:.1f} m/s²   ({label})")


if __name__ == "__main__":
    main()
