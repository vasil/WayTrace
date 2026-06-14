#!/usr/bin/env python3
"""
Analyse where the worst bumps land within a push:

  1. Temporal position — start (0–25%), early (25–50%), late (50–75%),
     end (75–100%) of total push duration.
  2. Intersection proximity — distance to nearest road-road junction
     in OpenStreetMap. Junctions are queried live from Overpass for the
     bbox covering the hits.

Usage:
    bump_position_analysis.py \\
        --pair GPS-A.gpx ART-A.csv \\
        --pair GPS-B.gpx ART-B1.csv ART-B2.csv \\
        --top 15 \\
        --label "Skopje"
"""
import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from math import cos, radians
import numpy as np
import pandas as pd
import requests

GRAVITY = 9.81
DEDUP_GAP_S = 5.0
DEFAULT_TZ = 2
INTERSECTION_RADIUS_M = 25.0  # what counts as "at an intersection"


def parse_gpx(path):
    ns = "http://www.topografix.com/GPX/1/1"
    pts = []
    for tp in ET.parse(path).getroot().iter(f"{{{ns}}}trkpt"):
        lat = float(tp.attrib["lat"]); lon = float(tp.attrib["lon"])
        t = datetime.fromisoformat(
            tp.find(f"{{{ns}}}time").text.replace("Z", "+00:00"))
        pts.append((t.timestamp(), lat, lon))
    return pts


def parse_start(path, tz):
    m = re.search(r'(\d{12})', path.name)
    dt = datetime.strptime(m.group(1), '%Y%m%d%H%M')
    return dt.replace(tzinfo=timezone(timedelta(hours=tz))).timestamp()


def load_accel(path, tz):
    df = pd.read_csv(path, low_memory=False)
    a = df[df["sensor"] == "accel"].sort_values("timestamp_ms").reset_index(drop=True)
    if a.empty:
        return np.array([]), np.array([]), np.array([])
    t_ms = a["timestamp_ms"].to_numpy()
    start = parse_start(path, tz)
    utc = start + (t_ms - t_ms[0]) / 1000.0
    mag = np.sqrt(a["x"].to_numpy(float)**2 + a["y"].to_numpy(float)**2
                + a["z"].to_numpy(float)**2)
    return utc, mag, np.abs(mag - GRAVITY)


def push_data(arts, gpx, tz):
    chunks = [load_accel(p, tz) for p in arts]
    chunks = [c for c in chunks if len(c[0])]
    if not chunks:
        return None
    utc = np.concatenate([c[0] for c in chunks])
    mag = np.concatenate([c[1] for c in chunks])
    vib = np.concatenate([c[2] for c in chunks])
    o = np.argsort(utc); utc, mag, vib = utc[o], mag[o], vib[o]
    pts = parse_gpx(gpx)
    return {
        "utc": utc, "mag": mag, "vib": vib,
        "gpx_utc": np.array([p[0] for p in pts]),
        "lats":    np.array([p[1] for p in pts]),
        "lons":    np.array([p[2] for p in pts]),
        "name": gpx.stem.replace("GPS-", ""),
    }


def collect_hits(pushes, top):
    hits = []
    for p in pushes:
        order = np.argsort(p["vib"])[::-1]
        kept_t = []
        for idx in order:
            t = p["utc"][idx]
            if all(abs(t - kt) > DEDUP_GAP_S for kt in kept_t):
                kept_t.append(t)
                j = int(np.argmin(np.abs(p["gpx_utc"] - t)))
                push_total_s = p["utc"][-1] - p["utc"][0]
                t_into = t - p["utc"][0]
                pct = t_into / push_total_s if push_total_s > 0 else 0.0
                hits.append({
                    "peak": float(p["mag"][idx]),
                    "vib":  float(p["vib"][idx]),
                    "lat":  float(p["lats"][j]),
                    "lon":  float(p["lons"][j]),
                    "t_into_s": t_into,
                    "push_total_s": push_total_s,
                    "pct": pct,
                    "push": p["name"],
                })
                if len(kept_t) >= 5:
                    break
    hits.sort(key=lambda h: -h["peak"])
    return hits[:top]


def fetch_intersections(min_lat, max_lat, min_lon, max_lon):
    """Overpass: nodes that are part of >=2 different highway ways."""
    # A bit of margin
    q = f"""
[out:json][timeout:60];
(
  way["highway"]({min_lat-0.002},{min_lon-0.002},{max_lat+0.002},{max_lon+0.002});
);
out body;
>;
out skel qt;
"""
    print(f"querying Overpass for intersections in "
          f"({min_lat:.4f},{min_lon:.4f})–({max_lat:.4f},{max_lon:.4f})…",
          file=sys.stderr)
    r = requests.post("https://overpass-api.de/api/interpreter",
                      data={"data": q}, timeout=90,
                      headers={"User-Agent": "WayTrace-bump-analysis/1.0 "
                                             "(vasil@taneski.com)"})
    r.raise_for_status()
    data = r.json()

    node_lookup = {}
    way_nodes = []
    for el in data["elements"]:
        if el["type"] == "node":
            node_lookup[el["id"]] = (el["lat"], el["lon"])
        elif el["type"] == "way" and "nodes" in el:
            way_nodes.append(el["nodes"])

    node_way_count = {}
    for nodes in way_nodes:
        for nid in nodes:
            node_way_count[nid] = node_way_count.get(nid, 0) + 1
    junction_coords = [node_lookup[n] for n, c in node_way_count.items()
                       if c >= 2 and n in node_lookup]
    print(f"  {len(junction_coords):,} junction nodes", file=sys.stderr)
    return junction_coords


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1 = np.radians(lat1); p2 = np.radians(lat2)
    dp = np.radians(lat2 - lat1); dl = np.radians(lon2 - lon1)
    a = (np.sin(dp/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2)
    return 2 * R * np.arcsin(np.sqrt(a))


def nearest_junction_m(lat, lon, junctions):
    jlats = np.array([j[0] for j in junctions])
    jlons = np.array([j[1] for j in junctions])
    d = haversine_m(lat, lon, jlats, jlons)
    i = int(np.argmin(d))
    return float(d[i]), (jlats[i], jlons[i])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", action="append", nargs="+", required=True)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--label", default="region")
    ap.add_argument("--tz-offset", type=int, default=DEFAULT_TZ)
    args = ap.parse_args()

    pushes = []
    for grp in args.pair:
        gpx = Path(grp[0]).expanduser()
        arts = [Path(p).expanduser() for p in grp[1:]]
        d = push_data(arts, gpx, args.tz_offset)
        if d is not None:
            pushes.append(d)

    hits = collect_hits(pushes, args.top)
    print(f"\n=== {args.label} — top {len(hits)} hits ===")

    # Temporal-position buckets
    buckets = {"start (0–25%)": 0, "early (25–50%)": 0,
               "late (50–75%)": 0, "end (75–100%)": 0}
    for h in hits:
        if h["pct"] < 0.25: buckets["start (0–25%)"] += 1
        elif h["pct"] < 0.50: buckets["early (25–50%)"] += 1
        elif h["pct"] < 0.75: buckets["late (50–75%)"] += 1
        else:                 buckets["end (75–100%)"] += 1

    print("\nTemporal position within push:")
    for k, v in buckets.items():
        bar = "█" * v
        pct = 100 * v / len(hits) if hits else 0
        print(f"  {k:<20}  {v:>2}  {pct:4.0f}%  {bar}")

    # Intersection proximity
    lats = [h["lat"] for h in hits]; lons = [h["lon"] for h in hits]
    junctions = fetch_intersections(min(lats), max(lats), min(lons), max(lons))

    print(f"\nDistance to nearest road junction (< {INTERSECTION_RADIUS_M:.0f} m "
          "= 'at intersection'):")
    at_intersection = 0
    print(f"  {'rank':<5} {'peak':>6} {'lat':>10} {'lon':>10} "
          f"{'dist(m)':>8} {'pct':>5}  push")
    for rank, h in enumerate(hits, 1):
        d_m, _ = nearest_junction_m(h["lat"], h["lon"], junctions)
        flag = "★" if d_m < INTERSECTION_RADIUS_M else " "
        if d_m < INTERSECTION_RADIUS_M:
            at_intersection += 1
        print(f"  {flag}{rank:<3} {h['peak']:6.1f} "
              f"{h['lat']:10.6f} {h['lon']:10.6f} {d_m:8.1f} "
              f"{100*h['pct']:4.0f}%  {h['push']}")

    print(f"\n  {at_intersection}/{len(hits)} hits ({100*at_intersection/len(hits):.0f}%) "
          f"sit within {INTERSECTION_RADIUS_M:.0f} m of a road junction.")


if __name__ == "__main__":
    main()
