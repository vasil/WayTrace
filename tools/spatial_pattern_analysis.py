#!/usr/bin/env python3
"""
Grid pooled RMS samples from multiple pushes into a regular cell grid,
then surface the WORST and BEST cells along with the OpenStreetMap
highway/surface tags at each. Reveals which streets are reliably bad
vs reliably good across repeated visits.

Usage (same --pair convention as road_quality_map_combined.py):
    spatial_pattern_analysis.py \\
        --pair GPS-A.gpx ART-A.csv \\
        --pair GPS-B.gpx ART-B1.csv ART-B2.csv \\
        --label "Skopje" \\
        --top 10 \\
        --cell-m 40 \\
        --min-samples 20
"""
import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import requests

GRAVITY = 9.81
WINDOW_S = 4.0
DEFAULT_TZ = 2
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
UA = {"User-Agent": "WayTrace-pattern/1.0 (vasil@taneski.com)"}


def parse_gpx(path):
    ns = "http://www.topografix.com/GPX/1/1"
    pts = []
    for tp in ET.parse(path).getroot().iter(f"{{{ns}}}trkpt"):
        lat = float(tp.attrib["lat"]); lon = float(tp.attrib["lon"])
        t = datetime.fromisoformat(
            tp.find(f"{{{ns}}}time").text.replace("Z", "+00:00"))
        pts.append((t.timestamp(), lat, lon))
    return pts


def parse_start(p, tz):
    m = re.search(r'(\d{12})', p.name)
    dt = datetime.strptime(m.group(1), '%Y%m%d%H%M')
    return dt.replace(tzinfo=timezone(timedelta(hours=tz))).timestamp()


def push_rms(art_paths, gpx_path, tz):
    chunks = []
    for art in art_paths:
        df = pd.read_csv(art, low_memory=False)
        a = df[df["sensor"] == "accel"].sort_values("timestamp_ms").reset_index(drop=True)
        if a.empty:
            continue
        t_ms = a["timestamp_ms"].to_numpy()
        utc = parse_start(art, tz) + (t_ms - t_ms[0]) / 1000.0
        mag = np.sqrt(a["x"].to_numpy(float)**2 + a["y"].to_numpy(float)**2
                    + a["z"].to_numpy(float)**2)
        chunks.append((utc, np.abs(mag - GRAVITY)))
    if not chunks:
        return None, None, None
    utc = np.concatenate([c[0] for c in chunks])
    vib = np.concatenate([c[1] for c in chunks])
    o = np.argsort(utc); utc, vib = utc[o], vib[o]

    pts = parse_gpx(gpx_path)
    gpx_utc = np.array([p[0] for p in pts])
    lats = np.array([p[1] for p in pts]); lons = np.array([p[2] for p in pts])
    half = WINDOW_S / 2.0
    rms = np.zeros(len(gpx_utc))
    for i, gt in enumerate(gpx_utc):
        lo = np.searchsorted(utc, gt - half)
        hi = np.searchsorted(utc, gt + half)
        if hi > lo:
            ch = vib[lo:hi]
            rms[i] = np.sqrt(np.mean(ch * ch))
    keep = rms > 0
    return lats[keep], lons[keep], rms[keep]


def query_ways_near(lat, lon, radius=25):
    q = f"""
[out:json][timeout:30];
way(around:{radius},{lat},{lon})["highway"];
out tags;
"""
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=60, headers=UA)
    r.raise_for_status()
    out = []
    for el in r.json().get("elements", []):
        t = el.get("tags", {})
        out.append((t.get("name", ""), t.get("highway", ""),
                    t.get("surface", ""), t.get("smoothness", "")))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", action="append", nargs="+", required=True,
                    metavar="GPX ART [ART ...]")
    ap.add_argument("--label", default="region")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--cell-m", type=float, default=40.0)
    ap.add_argument("--min-samples", type=int, default=20)
    ap.add_argument("--tz-offset", type=int, default=DEFAULT_TZ)
    ap.add_argument("--no-osm", action="store_true",
                    help="skip Overpass queries (offline)")
    args = ap.parse_args()

    all_lat = []; all_lon = []; all_rms = []
    for grp in args.pair:
        gpx = Path(grp[0]).expanduser()
        arts = [Path(p).expanduser() for p in grp[1:]]
        l, n, r = push_rms(arts, gpx, args.tz_offset)
        if l is None:
            print(f"skip: {gpx.name}", file=sys.stderr); continue
        all_lat.append(l); all_lon.append(n); all_rms.append(r)
    if not all_lat:
        sys.exit("no data")
    lats = np.concatenate(all_lat); lons = np.concatenate(all_lon)
    rms = np.concatenate(all_rms)
    print(f"pooled {len(rms):,} covered points across {len(args.pair)} pushes")

    mean_lat = lats.mean()
    cell_lat = args.cell_m / 111111.0
    cell_lon = args.cell_m / (111111.0 * np.cos(np.radians(mean_lat)))
    ix = np.floor((lats - lats.min()) / cell_lat).astype(int)
    iy = np.floor((lons - lons.min()) / cell_lon).astype(int)
    df = pd.DataFrame({"ix": ix, "iy": iy, "lat": lats, "lon": lons, "rms": rms})
    agg = df.groupby(["ix", "iy"]).agg(
        mean_rms=("rms", "mean"), max_rms=("rms", "max"),
        n=("rms", "size"), lat=("lat", "mean"), lon=("lon", "mean"),
    ).reset_index()
    agg = agg[agg["n"] >= args.min_samples].sort_values("mean_rms", ascending=False)
    print(f"{len(agg):,} grid cells with ≥{args.min_samples} samples")

    print(f"\n=== {args.label} — WORST {args.top} cells "
          f"({args.cell_m:.0f} m × {args.cell_m:.0f} m) ===")
    worst = agg.head(args.top)
    print(worst[["lat","lon","mean_rms","max_rms","n"]].to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))

    print(f"\n=== {args.label} — BEST {args.top} cells ===")
    best = agg.tail(args.top).iloc[::-1]
    print(best[["lat","lon","mean_rms","max_rms","n"]].to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))

    if args.no_osm:
        return

    print(f"\n=== Overpass: streets at WORST 5 cells ===")
    for _, row in worst.head(5).iterrows():
        ways = query_ways_near(row["lat"], row["lon"])
        print(f"  {row['lat']:.5f}, {row['lon']:.5f}  (mean {row['mean_rms']:.2f} "
              f"max {row['max_rms']:.1f}, n={int(row['n'])})")
        for name, hwy, surf, smooth in ways:
            print(f"     ↳ {hwy:<12} surface={surf or '?':<14} "
                  f"smoothness={smooth or '?':<11}  {name}")
        time.sleep(0.5)

    print(f"\n=== Overpass: streets at BEST 5 cells ===")
    for _, row in best.head(5).iterrows():
        ways = query_ways_near(row["lat"], row["lon"])
        print(f"  {row['lat']:.5f}, {row['lon']:.5f}  (mean {row['mean_rms']:.2f} "
              f"max {row['max_rms']:.1f}, n={int(row['n'])})")
        for name, hwy, surf, smooth in ways:
            print(f"     ↳ {hwy:<12} surface={surf or '?':<14} "
                  f"smoothness={smooth or '?':<11}  {name}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
