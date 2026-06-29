#!/usr/bin/env python3
"""
osi027_gpx_enrich.py — write a single self-contained "push digest" GPX
that carries Strava GPS + WayTrace cadence + road-quality numbers, all
keyed to the same track-point times.

Design (locked 2026-06-29):
  • The enriched GPX becomes the canonical per-push artefact.
    Anyone with this file can graph or share the push without the
    raw ART CSV. ART stays around zipped as archive.
  • Original Strava GPX is NEVER mutated. Output is
    GPS-<push_ts>-enriched.gpx alongside the original.
  • Two extension namespaces:
      gpxtpx (Garmin TrackPointExtension v1)
        - cad        : cadence in pushes per minute (ppm)
        - speed      : speed in m/s
      wt (WayTrace custom)
        - speed_kmh        : speed in km/h (Vasil-friendly)
        - cadence_ppm      : redundant with gpxtpx:cad, but discoverable
        - rms_wk           : ISO 2631-1 Wk-weighted vertical RMS [m/s²]
                             over the centred 10 s window
        - vdv_cum_wk       : VDV cumulative to this point [m/s^1.75]
        - bumps_per_min    : bumps/min over the surrounding 60 s
        - iso_class        : ISO 8608 class A..F (per-window estimate)

Time alignment (GPX trkpt time → ART time):
  We assume ART starts within a few seconds of the GPX start (user
  presses Strava then WayTrace in quick succession). Drift over a
  100-minute push is typically < 5 s, which is invisible at the GPX
  trkpt cadence (1 Hz). Good enough for graphing; not good enough for
  frame-level video sync (use the sync chime offset for that).

Inputs:
  --gpx       path to GPS-YYYYMMDDHHMM.gpx
  --art       path to ART CSV (.csv or .csv.gz)
  --push-ts   override the timestamp (derived from filename otherwise)
  --out       output path (default: GPS-<push_ts>-enriched.gpx alongside)
"""
import argparse
import csv
import gzip
import math
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from xml.dom import minidom
from xml.etree import ElementTree as ET

import numpy as np
from scipy import signal

sys.path.insert(0, str(Path(__file__).parent))
from osi025_speed_log import DEFAULT_DB as METRICS_DB  # noqa: E402

GPX_NS = "http://www.topografix.com/GPX/1/1"
GPXTPX_NS = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
WT_NS = "https://github.com/vasiltaneski/waytrace/gpx/v1"

# OSI WayTrace signature — written into every enriched GPX so anyone
# opening the file knows what produced these extensions.
WT_CREATOR = "OSI WayTrace — Open Streets Initiative wheelchair sensor + analysis"
WT_HOMEPAGE = "https://github.com/vasiltaneski/waytrace"
WT_TOOL_VERSION = "osi027 v1"

ET.register_namespace("", GPX_NS)
ET.register_namespace("gpxtpx", GPXTPX_NS)
ET.register_namespace("wt", WT_NS)


# ── ART helpers ─────────────────────────────────────────────────────────────

def open_maybe_gz(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", newline="")
    return open(path, "r", newline="")


def load_accel(art_path):
    """Return (t_s, ax, ay, az) numpy arrays for accel rows only.
    t_s is seconds since the first ART row (not just first accel)."""
    ts_list = []
    x = []; y = []; z = []
    t0 = None
    with open_maybe_gz(art_path) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if not row:
                continue
            try:
                ts = int(row[0])
            except ValueError:
                continue
            if t0 is None:
                t0 = ts
            if row[1] not in ("accel", "accelerometer"):
                continue
            try:
                ax = float(row[2]); ay = float(row[3]); az = float(row[4])
            except (ValueError, IndexError):
                continue
            ts_list.append((ts - t0) / 1000.0)
            x.append(ax); y.append(ay); z.append(az)
    return (np.asarray(ts_list), np.asarray(x),
            np.asarray(y), np.asarray(z))


def per_second_road_quality(t_s, ax, ay, az, fs=50.0,
                            window_s=10.0, bump_window_s=60.0,
                            bump_threshold=12.0):
    """Return (t_grid_s, rms_wk, vdv_cum_wk, bumps_per_min) — each a
    numpy array on a 1 Hz grid centred on each integer second.

    - rms_wk        : ISO 2631-1 Wk-weighted vertical RMS over the
                      centred `window_s` window. Uses a Butterworth
                      bandpass approximation per the project SRS.
    - vdv_cum_wk    : cumulative VDV from the start of the recording
                      up to this second.
    - bumps_per_min : count of |a|-g spikes above `bump_threshold`
                      m/s² in the centred `bump_window_s` window,
                      scaled to per-minute.
    """
    if len(t_s) < int(fs):
        return (np.array([]),) * 4
    # 1) Uniform 50 Hz resample
    t_u = np.arange(t_s[0], t_s[-1], 1.0 / fs)
    ax_u = np.interp(t_u, t_s, ax)
    ay_u = np.interp(t_u, t_s, ay)
    az_u = np.interp(t_u, t_s, az)
    mag = np.sqrt(ax_u**2 + ay_u**2 + az_u**2)
    vib_raw = np.abs(mag - 9.81)
    # 2) Wk-weighted approx: 4-pole Butterworth bandpass [0.4 – 20] Hz
    nyq = fs / 2.0
    sos = signal.butter(4, [0.4 / nyq, min(20.0, nyq * 0.99) / nyq],
                        btype="band", output="sos")
    wk = signal.sosfiltfilt(sos, mag - 9.81)
    # 3) per-second grids
    t_min = int(np.ceil(t_s[0]))
    t_max = int(np.floor(t_s[-1]))
    t_grid = np.arange(t_min, t_max + 1, dtype=float)
    rms = np.zeros_like(t_grid)
    vdv = np.zeros_like(t_grid)
    bumps = np.zeros_like(t_grid)
    half_rms = window_s / 2.0
    half_bump = bump_window_s / 2.0
    # bump indices (in t_u) — count once via peak detection
    bump_idx = signal.find_peaks(vib_raw, height=bump_threshold,
                                 distance=int(0.1 * fs))[0]
    bump_times = t_u[bump_idx]
    # Pre-compute cumulative VDV integrand (sum of wk^4 * dt)
    dt = 1.0 / fs
    vdv4_cum = np.cumsum(wk ** 4) * dt
    # Iterate seconds
    for i, t in enumerate(t_grid):
        lo = np.searchsorted(t_u, t - half_rms, side="left")
        hi = np.searchsorted(t_u, t + half_rms, side="right")
        if hi > lo:
            rms[i] = float(np.sqrt(np.mean(wk[lo:hi] ** 2)))
        # cumulative VDV up to t
        idx_t = np.searchsorted(t_u, t, side="right") - 1
        if idx_t >= 0:
            vdv[i] = float(vdv4_cum[idx_t] ** 0.25)
        b_lo = np.searchsorted(bump_times, t - half_bump, side="left")
        b_hi = np.searchsorted(bump_times, t + half_bump, side="right")
        bumps[i] = (b_hi - b_lo) * 60.0 / bump_window_s
    return t_grid, rms, vdv, bumps


def iso_8608_class_from_rms(rms_wk):
    """Approximate ISO 8608 class from Wk-weighted RMS in m/s².
    These thresholds are empirically tuned in waytrace_analysis.py for
    wheelchair pushes (not road vehicles). They are class-LIKE,
    not literal ISO 8608 boundaries. Per-50 m method is OSI task #53.
    """
    if rms_wk is None or rms_wk <= 0 or not np.isfinite(rms_wk):
        return None
    # Wheelchair-tuned mapping based on existing OSI heuristics.
    if rms_wk < 0.30: return "A"
    if rms_wk < 0.60: return "B"
    if rms_wk < 1.00: return "C"
    if rms_wk < 1.50: return "D"
    if rms_wk < 2.50: return "E"
    return "F"


# ── metrics.db lookups ─────────────────────────────────────────────────────

def load_speed_cadence(db_path, push_ts):
    """Return dict push_t_s -> (speed_kmh, cadence_ppm). Sparse on either
    field. art_t_s is the canonical key; speed uses GPX-derived art_t_s
    so we approximate cadence's t-axis as the same."""
    con = sqlite3.connect(db_path)
    speed = {}
    for t, s in con.execute(
            "SELECT art_t_s, speed_kmh FROM speed_samples "
            "WHERE push_ts=? ORDER BY art_t_s", (push_ts,)):
        speed[round(float(t))] = s
    cadence = {}
    for t, c in con.execute(
            "SELECT art_t_s, cadence_ppm FROM cadence_samples "
            "WHERE push_ts=? ORDER BY art_t_s", (push_ts,)):
        cadence[round(float(t))] = c
    return speed, cadence


# ── GPX read/write ─────────────────────────────────────────────────────────

def parse_gpx_with_times(gpx_path):
    """Return (tree, list of (trkpt_element, t_unix, art_t_s)) where
    art_t_s is seconds since the first trkpt."""
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    pts = []
    t0 = None
    for trkpt in root.iter(f"{{{GPX_NS}}}trkpt"):
        time_el = trkpt.find(f"{{{GPX_NS}}}time")
        if time_el is None or time_el.text is None:
            continue
        t_unix = datetime.fromisoformat(
            time_el.text.replace("Z", "+00:00")).timestamp()
        if t0 is None:
            t0 = t_unix
        pts.append((trkpt, t_unix, t_unix - t0))
    return tree, pts


def inject_extensions(trkpt, speed_kmh=None, cadence_ppm=None,
                      rms_wk=None, vdv_cum_wk=None, bumps_per_min=None,
                      iso_class=None):
    """Add/replace <extensions> on a trkpt. Idempotent (rewrites if present)."""
    # Remove any existing extensions block so re-runs are clean
    for old in list(trkpt.findall(f"{{{GPX_NS}}}extensions")):
        trkpt.remove(old)
    ext = ET.SubElement(trkpt, f"{{{GPX_NS}}}extensions")

    # Garmin TrackPointExtension — interoperable with Strava/Garmin
    tpx = ET.SubElement(ext, f"{{{GPXTPX_NS}}}TrackPointExtension")
    have_garmin = False
    if cadence_ppm is not None and not (isinstance(cadence_ppm, float)
                                        and math.isnan(cadence_ppm)):
        cad = ET.SubElement(tpx, f"{{{GPXTPX_NS}}}cad")
        cad.text = str(int(round(cadence_ppm)))
        have_garmin = True
    if speed_kmh is not None and not (isinstance(speed_kmh, float)
                                      and math.isnan(speed_kmh)):
        sp = ET.SubElement(tpx, f"{{{GPXTPX_NS}}}speed")
        sp.text = f"{speed_kmh / 3.6:.3f}"   # m/s per Garmin spec
        have_garmin = True
    if not have_garmin:
        ext.remove(tpx)

    # WayTrace custom values — full precision, plus the road-quality block
    def sub(name, val, fmt="{:.3f}"):
        if val is None: return
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return
        el = ET.SubElement(ext, f"{{{WT_NS}}}{name}")
        el.text = fmt.format(val)

    sub("speed_kmh",     speed_kmh)
    sub("cadence_ppm",   cadence_ppm, "{:.1f}")
    sub("rms_wk",        rms_wk)
    sub("vdv_cum_wk",    vdv_cum_wk)
    sub("bumps_per_min", bumps_per_min, "{:.2f}")
    if iso_class is not None:
        el = ET.SubElement(ext, f"{{{WT_NS}}}iso_class")
        el.text = iso_class


def add_waytrace_signature(root, push_ts, gpx_src, art_src):
    """Re-stamp the GPX with an OSI WayTrace signature so the enriched
    file is self-identifying. Sets creator= on the root, replaces
    <metadata> with an OSI WayTrace block, and adds a <wt:source> child
    pointing at the original GPX + ART filenames."""
    # creator attribute on <gpx>
    root.set("creator", WT_CREATOR)
    # Make sure xsi:schemaLocation is preserved if it was there

    # Rebuild metadata: remove existing, then prepend a fresh block
    for old in list(root.findall(f"{{{GPX_NS}}}metadata")):
        root.remove(old)
    md = ET.Element(f"{{{GPX_NS}}}metadata")
    name = ET.SubElement(md, f"{{{GPX_NS}}}name")
    name.text = f"OSI WayTrace push {push_ts}"
    desc = ET.SubElement(md, f"{{{GPX_NS}}}desc")
    desc.text = ("Enriched with WayTrace cadence (pushes per minute), "
                 "speed, elevation, Wk-weighted RMS, cumulative VDV, "
                 "bumps per minute, and ISO 8608 class estimate.")
    author = ET.SubElement(md, f"{{{GPX_NS}}}author")
    author_name = ET.SubElement(author, f"{{{GPX_NS}}}name")
    author_name.text = "OSI WayTrace"
    author_link = ET.SubElement(author, f"{{{GPX_NS}}}link",
                                href=WT_HOMEPAGE)
    author_link_text = ET.SubElement(author_link, f"{{{GPX_NS}}}text")
    author_link_text.text = "WayTrace on GitHub"
    link = ET.SubElement(md, f"{{{GPX_NS}}}link", href=WT_HOMEPAGE)
    link_text = ET.SubElement(link, f"{{{GPX_NS}}}text")
    link_text.text = "OSI WayTrace"
    t = ET.SubElement(md, f"{{{GPX_NS}}}time")
    t.text = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    # Custom signature block
    ext = ET.SubElement(md, f"{{{GPX_NS}}}extensions")
    def sig(name, value):
        el = ET.SubElement(ext, f"{{{WT_NS}}}{name}")
        el.text = value
    sig("application",       "OSI WayTrace")
    sig("source",            "WayTrace Android sensor logger (ART CSV) "
                             "+ Strava GPX")
    sig("enricher",          WT_TOOL_VERSION)
    sig("homepage",          WT_HOMEPAGE)
    sig("push_ts",           push_ts)
    sig("source_gpx",        Path(gpx_src).name)
    if art_src:
        sig("source_art",    Path(art_src).name)
    sig("enriched_at_utc",   datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    root.insert(0, md)


def write_gpx(tree, out_path):
    """Pretty-print to keep human-readability when Vasil opens the file."""
    rough = ET.tostring(tree.getroot(), encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ",
                                                    encoding="utf-8")
    Path(out_path).write_bytes(b"\n".join(
        ln for ln in pretty.splitlines() if ln.strip()))


# ── main ───────────────────────────────────────────────────────────────────

def derive_push_ts(path):
    m = re.search(r"(\d{12})", Path(path).name)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gpx",      required=True)
    ap.add_argument("--art",      help="ART CSV (or .csv.gz) — REQUIRED for "
                                       "road-quality fields")
    ap.add_argument("--push-ts",  help="override push timestamp")
    ap.add_argument("--out",      help="output path (default: alongside GPX)")
    ap.add_argument("--db",       default=METRICS_DB)
    args = ap.parse_args()

    push_ts = args.push_ts or derive_push_ts(args.gpx)
    if push_ts is None:
        sys.exit(f"cannot derive push_ts from {args.gpx} — pass --push-ts")
    out = args.out or str(Path(args.gpx).parent /
                          f"GPS-{push_ts}-enriched.gpx")

    print(f"push: {push_ts}")
    print(f"gpx : {args.gpx}")
    print(f"out : {out}")

    tree, pts = parse_gpx_with_times(args.gpx)
    print(f"trkpts: {len(pts)}")
    if not pts:
        sys.exit("GPX has no trkpts with <time>")

    # Speed + cadence from metrics.db (already keyed by art_t_s)
    speed_map, cadence_map = load_speed_cadence(args.db, push_ts)
    print(f"db speed samples   : {len(speed_map)}")
    print(f"db cadence samples : {len(cadence_map)}")

    # Road quality from ART (per-second arrays)
    if args.art:
        print(f"art : {args.art}  (computing road quality...)")
        t_s, ax, ay, az = load_accel(args.art)
        t_grid, rms_arr, vdv_arr, bumps_arr = per_second_road_quality(
            t_s, ax, ay, az)
        rq_map = {int(t): (float(r), float(v), float(b))
                  for t, r, v, b in zip(t_grid, rms_arr, vdv_arr, bumps_arr)}
        print(f"road-quality seconds: {len(rq_map)}")
    else:
        rq_map = {}
        print("no --art: skipping road quality (cadence/speed only)")

    n_with_speed = 0; n_with_cad = 0; n_with_rq = 0
    for trkpt, t_unix, art_t_s in pts:
        key = round(art_t_s)
        speed = speed_map.get(key)
        cad = cadence_map.get(key)
        rms = vdv = bumps = iso_cls = None
        if key in rq_map:
            rms, vdv, bumps = rq_map[key]
            iso_cls = iso_8608_class_from_rms(rms)
        inject_extensions(trkpt, speed_kmh=speed, cadence_ppm=cad,
                          rms_wk=rms, vdv_cum_wk=vdv,
                          bumps_per_min=bumps, iso_class=iso_cls)
        if speed is not None: n_with_speed += 1
        if cad is not None:   n_with_cad   += 1
        if rms is not None:   n_with_rq    += 1

    # Brand the file as OSI WayTrace before writing
    add_waytrace_signature(tree.getroot(), push_ts, args.gpx, args.art)
    write_gpx(tree, out)

    sz = os.path.getsize(out)
    print()
    print(f"wrote: {out}  ({sz//1024} KB)")
    print(f"  trkpts with speed   : {n_with_speed}/{len(pts)}")
    print(f"  trkpts with cadence : {n_with_cad}/{len(pts)}")
    print(f"  trkpts with road-Q  : {n_with_rq}/{len(pts)}")


if __name__ == "__main__":
    main()
