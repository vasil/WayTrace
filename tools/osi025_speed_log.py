#!/usr/bin/env python3
"""
osi025_speed_log.py — record the per-second wheelchair-pose speed time
series for every push, in a SQLite database alongside the WayTrace
analysis outputs.

Purpose: graphing speed alongside VDV, IRI, ISO 8608 class, RMS, jerk
etc. The HUD shows speed on screen; this script captures the same time
series in queryable form.

Source: the Strava GPX. Same data the dashboard reads for the HUD, so the
graphed line matches what was on screen.

Schema (single SQLite, default ~/Projects/WayTrace/data/metrics.db):

  speed_samples
    push_ts      TEXT NOT NULL   -- e.g. 202606280835
    t_unix       REAL NOT NULL   -- unix seconds (UTC) at the GPX sample
    art_t_s      REAL            -- seconds since push start (t_unix - first)
    lat          REAL
    lon          REAL
    elevation_m  REAL            -- from GPX <ele> (metres above sea level)
    speed_kmh    REAL
    PRIMARY KEY(push_ts, t_unix)

The DB file is gitignored. Methodology is public; data stays local.

Usage:
  osi025_speed_log.py --gpx GPS-202606280835.gpx [--push-ts 202606280835]
  osi025_speed_log.py --backfill ~/Downloads/GPS-*.gpx
  osi025_speed_log.py --query 202606280835    # dump rows for one push
"""
import argparse
import glob
import math
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

DEFAULT_DB = os.environ.get(
    "WAYTRACE_METRICS_DB",
    str(Path.home() / "Projects" / "WayTrace" / "data" / "metrics.db"))

GPX_NS = {"g": "http://www.topografix.com/GPX/1/1"}


def init_db(db_path=DEFAULT_DB):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS speed_samples (
        push_ts     TEXT NOT NULL,
        t_unix      REAL NOT NULL,
        art_t_s     REAL,
        lat         REAL,
        lon         REAL,
        elevation_m REAL,
        speed_kmh   REAL,
        PRIMARY KEY(push_ts, t_unix)
    )""")
    # Schema migration: add elevation_m to older DBs that don't have it.
    cols = {r[1] for r in con.execute("PRAGMA table_info(speed_samples)")}
    if "elevation_m" not in cols:
        con.execute("ALTER TABLE speed_samples ADD COLUMN elevation_m REAL")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_speed_push
        ON speed_samples(push_ts, art_t_s)""")
    con.commit()
    return con


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def parse_gpx_samples(path):
    """Return list of (t_unix, lat, lon, elev_m) sorted by time.
    elev_m is None if the GPX point has no <ele> tag."""
    root = ET.parse(path).getroot()
    out = []
    for trkpt in root.iter("{http://www.topografix.com/GPX/1/1}trkpt"):
        try:
            lat = float(trkpt.attrib["lat"])
            lon = float(trkpt.attrib["lon"])
        except (KeyError, ValueError):
            continue
        t_el = trkpt.find("g:time", GPX_NS)
        if t_el is None or t_el.text is None:
            continue
        t = datetime.fromisoformat(t_el.text.replace("Z", "+00:00"))
        ele_el = trkpt.find("g:ele", GPX_NS)
        elev = None
        if ele_el is not None and ele_el.text is not None:
            try:
                elev = float(ele_el.text)
            except ValueError:
                pass
        out.append((t.timestamp(), lat, lon, elev))
    out.sort()
    return out


def derive_push_ts(gpx_path):
    """Pull YYYYMMDDHHMM out of GPS-*.gpx filename. Fallback: first sample."""
    name = Path(gpx_path).name
    m = re.search(r"(\d{12})", name)
    if m:
        return m.group(1)
    return None


def speed_from_neighbours(samples, i):
    """Return km/h using midpoint difference (or edge) at index i.
    samples = [(t_unix, lat, lon, elev)]."""
    if len(samples) < 2:
        return None
    if i == 0:
        t0, la0, lo0, _ = samples[i]
        t1, la1, lo1, _ = samples[i + 1]
    elif i == len(samples) - 1:
        t0, la0, lo0, _ = samples[i - 1]
        t1, la1, lo1, _ = samples[i]
    else:
        t0, la0, lo0, _ = samples[i - 1]
        t1, la1, lo1, _ = samples[i + 1]
    dt = t1 - t0
    if dt <= 0:
        return None
    return haversine_m(la0, lo0, la1, lo1) / dt * 3.6


def ingest_gpx(con, gpx_path, push_ts=None):
    if push_ts is None:
        push_ts = derive_push_ts(gpx_path)
    if push_ts is None:
        sys.exit(f"cannot derive push_ts from {gpx_path} — pass --push-ts")
    samples = parse_gpx_samples(gpx_path)
    if not samples:
        return push_ts, 0
    t0 = samples[0][0]
    rows = []
    for i, (t_unix, lat, lon, elev) in enumerate(samples):
        rows.append((push_ts, t_unix, t_unix - t0, lat, lon, elev,
                     speed_from_neighbours(samples, i)))
    con.executemany("""INSERT OR REPLACE INTO speed_samples
        (push_ts, t_unix, art_t_s, lat, lon, elevation_m, speed_kmh)
        VALUES (?,?,?,?,?,?,?)""", rows)
    con.commit()
    return push_ts, len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--gpx",      help="ingest one GPX")
    g.add_argument("--backfill", nargs="+",
                   help="ingest many GPX (e.g. ~/Downloads/GPS-*.gpx)")
    g.add_argument("--query",    help="print rows for a push_ts")
    ap.add_argument("--push-ts", help="override push timestamp")
    ap.add_argument("--db",      default=DEFAULT_DB)
    args = ap.parse_args()

    con = init_db(args.db)

    if args.gpx:
        pt, n = ingest_gpx(con, args.gpx, args.push_ts)
        print(f"{pt}: ingested {n} samples → {args.db}")

    elif args.backfill:
        paths = []
        for p in args.backfill:
            if "*" in p or "?" in p:
                paths.extend(sorted(glob.glob(p)))
            else:
                paths.append(p)
        total_pushes = 0
        total_rows = 0
        for p in paths:
            try:
                pt, n = ingest_gpx(con, p)
            except Exception as e:
                print(f"  skip {p}: {e}")
                continue
            total_pushes += 1
            total_rows += n
            print(f"  {pt}: +{n} samples")
        print(f"\nbackfill complete: {total_pushes} pushes, {total_rows} samples")

    elif args.query:
        rows = con.execute("""SELECT art_t_s, speed_kmh FROM speed_samples
            WHERE push_ts = ? ORDER BY art_t_s""", (args.query,)).fetchall()
        if not rows:
            print(f"no rows for push_ts={args.query}")
            return
        print(f"# push_ts={args.query}  samples={len(rows)}")
        print("art_t_s,speed_kmh")
        for t, s in rows:
            print(f"{t:.2f},{s:.2f}" if s is not None else f"{t:.2f},")

    # Summary
    n = con.execute("SELECT COUNT(*) FROM speed_samples").fetchone()[0]
    p = con.execute("SELECT COUNT(DISTINCT push_ts) FROM speed_samples").fetchone()[0]
    print(f"\nDB total: {p} pushes, {n} speed samples  ({args.db})")


if __name__ == "__main__":
    main()
