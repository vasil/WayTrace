#!/usr/bin/env python3
"""
Map of wheelie locations on the push route.

Reads an ART CSV + matching GPX, derives pitch from the gravity sensor,
detects wheelie events (≥15° above neutral, sustained ≥0.3 s), and plots
the route with each wheelie marked at its GPS coordinate.

Time alignment matches waytrace_locate.py: ART recording start = GPX first
timestamp.

Usage:
    python wheelie_map.py ~/Downloads/ART-202605242125.csv \\
                          ~/Downloads/GPS-202605242125.gpx \\
                          [output.png]
"""
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_gpx(path):
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    tree = ET.parse(path)
    root = tree.getroot()
    pts = []
    for trkpt in root.iter("{%s}trkpt" % ns["g"]):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        t_el = trkpt.find("{%s}time" % ns["g"])
        t = datetime.fromisoformat(t_el.text.replace("Z", "+00:00"))
        pts.append((t, lat, lon))
    return pts


def detect_events(signal, t, threshold=15.0, min_dur_s=0.3, min_gap_s=1.0):
    above = signal > threshold
    events = []
    i = 0
    n = len(signal)
    while i < n:
        if above[i]:
            j = i
            while j < n and above[j]:
                j += 1
            dur = t[j-1] - t[i]
            if dur >= min_dur_s:
                events.append((t[i], t[j-1], float(signal[i:j].max()), dur))
            i = j
        else:
            i += 1
    merged = []
    for e in events:
        if merged and (e[0] - merged[-1][1]) < min_gap_s:
            s, _, pk, _ = merged[-1]
            merged[-1] = (s, e[1], max(pk, e[2]), e[1] - s)
        else:
            merged.append(e)
    return merged


def gps_at_offset(gpx_pts, offset_s):
    t0 = gpx_pts[0][0]
    target = t0.timestamp() + offset_s
    # nearest neighbour search
    best = min(gpx_pts, key=lambda p: abs(p[0].timestamp() - target))
    return best[1], best[2]


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        sys.exit(2)
    art_path = Path(argv[1]).expanduser()
    gpx_path = Path(argv[2]).expanduser()
    out_path = Path(argv[3]) if len(argv) > 3 else art_path.with_name(
        art_path.stem.replace("ART-", "WHM-") + ".png")

    df = pd.read_csv(art_path)
    g = df[df["sensor"] == "gravity"].sort_values("timestamp_ms").reset_index(drop=True)
    t_ms = g["timestamp_ms"].to_numpy()
    t = (t_ms - t_ms[0]) / 1000.0
    pitch = np.degrees(np.arctan2(g["x"].to_numpy(float), g["y"].to_numpy(float)))
    pitch_rel = pitch - np.median(pitch)
    events15 = detect_events(pitch_rel, t, threshold=15.0)

    gpx_pts = parse_gpx(gpx_path)
    lats = np.array([p[1] for p in gpx_pts])
    lons = np.array([p[2] for p in gpx_pts])

    # Map wheelie midpoints to GPS coords
    wheelie_coords = []
    for s, e, pk, d in events15:
        mid = (s + e) / 2
        lat, lon = gps_at_offset(gpx_pts, mid)
        wheelie_coords.append((lat, lon, pk, d, s, e))

    fig, ax = plt.subplots(figsize=(10, 10))
    # Route as a faint blue line
    ax.plot(lons, lats, lw=1.4, color="C0", alpha=0.55, label="push route")
    # Start / end markers
    ax.plot(lons[0],  lats[0],  "o", color="green", markersize=10, label="start")
    ax.plot(lons[-1], lats[-1], "s", color="black", markersize=10, label="end")

    # Wheelie markers
    for k, (lat, lon, pk, d, s, e) in enumerate(wheelie_coords, 1):
        ax.plot(lon, lat, "*", color="C3", markersize=22,
                markeredgecolor="black", markeredgewidth=0.8, zorder=5)
        label = f"#{k}\n{d:.1f}s · {pk:.0f}°"
        ax.annotate(label, xy=(lon, lat),
                    xytext=(12, 12), textcoords="offset points",
                    fontsize=9, fontweight="bold", color="C3",
                    bbox=dict(boxstyle="round,pad=0.3", fc="white",
                              ec="C3", alpha=0.85))

    # Equal aspect so the map isn't squashed
    mean_lat = float(lats.mean())
    ax.set_aspect(1.0 / np.cos(np.radians(mean_lat)))

    # Pad bounds a bit
    pad = 0.0005
    ax.set_xlim(lons.min() - pad, lons.max() + pad)
    ax.set_ylim(lats.min() - pad, lats.max() + pad)
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(f"Wheelie locations  —  {art_path.name}\n"
                 f"{len(events15)} wheelies (≥15° back-tilt, sustained ≥0.3 s)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    print(f"wrote {out_path}")
    for k, (lat, lon, pk, d, s, e) in enumerate(wheelie_coords, 1):
        print(f"  #{k}: {lat:.6f}, {lon:.6f}   dur={d:.2f}s   peak={pk:+.1f}°   "
              f"(at {int(s//60):02d}:{s%60:05.2f} into push)")


if __name__ == "__main__":
    main(sys.argv)
