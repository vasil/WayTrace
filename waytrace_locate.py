#!/usr/bin/env python3
"""
WayTrace Locate — pin road problems to actual streets.

Aligns an ART sensor CSV (millisecond-since-boot timestamps) with a GPX
GPS track (wall-clock timestamps) for the same ride. Finds bad spots by
RMS vibration, jerk peaks, and recorded bump events, then plots them on
a map of the route and writes a ranked text report.

Usage:
    python3 waytrace_locate.py <ART-*.csv> <GPS-*.gpx> [--chair NAME]

Outputs (in the same folder as the ART file):
    LOC-YYYYMMDDHHMM.png   route map colored by RMS, top-N pins
    LOC-YYYYMMDDHHMM.txt   ranked bad-spot list with lat/lon
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm

from waytrace_analysis import (
    GRAVITY,
    RMS_UNCOMFORTABLE, BUMP_MAG, HEAVY_BUMP_MAG,
    load_csv, split_sensors,
    detect_generation, generation_banner,
    detect_events_offline,
    detect_sample_rate,
)

LOCAL_TZ        = ZoneInfo("Europe/Skopje")
WINDOW_SECONDS  = 2.0
JERK_NORMALIZER = 200.0     # m/s³ — divides jerk in severity, so 200 m/s³ ≈ score 1
SEVERITY_CUTOFF = 1.0       # windows with score above this are "bad"
TOP_N           = 10

# Wolf 2005 sidewalk-vibration standard speed window. Larivière 2021
# review identifies uncontrolled speed as the single biggest confound;
# we keep segments inside this window for cross-segment ranking.
SPEED_MIN_MS    = 0.8
SPEED_MAX_MS    = 1.5
SPEED_PAUSE_MS  = 0.3       # below this is treated as a stop, excluded entirely

CHAIRS_DIR      = Path.home() / ".config" / "waytrace" / "chairs"


# ── Loaders ──────────────────────────────────────────────────────────────

def parse_art_start_from_filename(path: Path) -> datetime:
    """ART-YYYYMMDDHHMM.csv  → local datetime (Europe/Skopje)."""
    m = re.search(r'(\d{12})', path.name)
    if not m:
        sys.exit(f"Could not parse YYYYMMDDHHMM from ART filename: {path.name}")
    dt = datetime.strptime(m.group(1), "%Y%m%d%H%M")
    return dt.replace(tzinfo=LOCAL_TZ)


@dataclass
class GpxTrack:
    times: np.ndarray   # UTC datetime64[s]
    lats:  np.ndarray
    lons:  np.ndarray
    eles:  np.ndarray
    name:  str


def load_gpx(path: Path) -> GpxTrack:
    NS = {"g": "http://www.topografix.com/GPX/1/1"}
    tree = ET.parse(path)
    root = tree.getroot()
    pts = root.findall(".//g:trkpt", NS)
    if not pts:
        # Maybe namespaceless GPX
        pts = root.findall(".//trkpt")
        get = lambda p, tag: p.find(tag)
    else:
        get = lambda p, tag: p.find(f"g:{tag}", NS)

    times, lats, lons, eles = [], [], [], []
    for p in pts:
        lats.append(float(p.attrib["lat"]))
        lons.append(float(p.attrib["lon"]))
        t = get(p, "time")
        if t is None or t.text is None:
            sys.exit("GPX <trkpt> is missing <time>; cannot align without timestamps.")
        ts = t.text.replace("Z", "+00:00")
        times.append(datetime.fromisoformat(ts).astimezone(timezone.utc))
        e = get(p, "ele")
        eles.append(float(e.text) if (e is not None and e.text is not None) else float("nan"))

    name_el = root.find(".//g:trk/g:name", NS)
    if name_el is None:
        name_el = root.find(".//trk/name")
    name = name_el.text if (name_el is not None and name_el.text) else path.stem

    return GpxTrack(
        times=np.array(times),
        lats=np.array(lats),
        lons=np.array(lons),
        eles=np.array(eles),
        name=name,
    )


# ── Alignment ────────────────────────────────────────────────────────────

def choose_anchor(filename_start: datetime, gpx_start: datetime) -> datetime:
    """Use the GPX start as the wall-clock anchor if it lies within 5 min
    of the ART filename's minute-resolution start. Otherwise fall back
    to the filename and warn loudly."""
    delta = abs((filename_start.astimezone(timezone.utc) - gpx_start).total_seconds())
    if delta <= 300:
        print(f"Anchor: GPX start = {gpx_start.isoformat()} "
              f"(filename within {delta:.0f}s — using GPS as precise anchor)")
        return gpx_start
    print(f"WARNING: GPX start ({gpx_start.isoformat()}) is "
          f"{delta:.0f}s off the ART filename ({filename_start.isoformat()})")
    print("         Using ART filename as anchor — bad-spot locations may be approximate.")
    return filename_start.astimezone(timezone.utc)


def gps_at(times_utc, lats, lons, t_utc):
    """Linear interpolation of (lat, lon) at a given UTC datetime."""
    if t_utc <= times_utc[0]:
        return lats[0], lons[0]
    if t_utc >= times_utc[-1]:
        return lats[-1], lons[-1]
    ts_array  = np.array([(t - times_utc[0]).total_seconds() for t in times_utc])
    t_seconds = (t_utc - times_utc[0]).total_seconds()
    return (float(np.interp(t_seconds, ts_array, lats)),
            float(np.interp(t_seconds, ts_array, lons)))


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres."""
    R = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = np.radians(lat2 - lat1)
    dl = np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * R * np.arcsin(np.sqrt(a)))


def gps_speed_series(gpx):
    """Per-point speed (m/s) over a GpxTrack, smoothed with a 5-point boxcar.
    Speed at index i is distance to the next point / dt between them; the
    last point reuses the previous value."""
    n = len(gpx.lats)
    if n < 2:
        return np.zeros(n)
    sp = np.zeros(n)
    for i in range(n - 1):
        dt = (gpx.times[i + 1] - gpx.times[i]).total_seconds()
        if dt <= 0:
            continue
        d = haversine_m(gpx.lats[i], gpx.lons[i],
                        gpx.lats[i + 1], gpx.lons[i + 1])
        sp[i] = d / dt
    sp[-1] = sp[-2]
    # 5-point boxcar smoothing — pedestrian-grade GPS jitter shows up as
    # 1–2 m/s noise; smoothing pulls it back to the ride's actual cadence.
    k = 5
    if n >= k:
        kern = np.ones(k) / k
        sp = np.convolve(sp, kern, mode='same')
    return sp


def speed_at(gpx, sp, t_utc):
    """Speed (m/s) at a given UTC time, by linear interpolation."""
    if t_utc <= gpx.times[0]:
        return float(sp[0])
    if t_utc >= gpx.times[-1]:
        return float(sp[-1])
    ts = np.array([(t - gpx.times[0]).total_seconds() for t in gpx.times])
    s = (t_utc - gpx.times[0]).total_seconds()
    return float(np.interp(s, ts, sp))


def speed_in_range_duration(gpx, sp):
    """Return (in_range_seconds, total_seconds) — how long the ride
    spent in the Wolf-2005 speed window, ignoring pauses (< SPEED_PAUSE_MS)."""
    if len(gpx.times) < 2:
        return 0.0, 0.0
    total = 0.0
    in_range = 0.0
    for i in range(len(gpx.times) - 1):
        dt = (gpx.times[i + 1] - gpx.times[i]).total_seconds()
        if dt <= 0:
            continue
        v = sp[i]
        if v < SPEED_PAUSE_MS:
            continue  # pause: doesn't count toward either total
        total += dt
        if SPEED_MIN_MS <= v <= SPEED_MAX_MS:
            in_range += dt
    return in_range, total


def load_chair_profile(chair_name: str):
    """Load a per-chair calibration profile if it exists. Silent fallback."""
    if not chair_name:
        return None
    path = CHAIRS_DIR / f"{chair_name}.json"
    if not path.exists():
        print(f"   chair profile not found: {path} (continuing without)")
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        print(f"   chair profile {path} unreadable: {e} (continuing without)")
        return None


# ── Bad-spot detection ───────────────────────────────────────────────────

@dataclass
class BadSegment:
    rank: int
    severity: float
    t_start_s: float
    t_end_s: float
    rms_max: float
    jerk_max: float
    heavy_bump: bool
    bump: bool
    lat: float
    lon: float
    speed_ms: float = float('nan')   # filled after GPS alignment
    in_speed_range: bool = True      # set in main() after speed lookup

    @property
    def mid_s(self) -> float:
        return (self.t_start_s + self.t_end_s) / 2.0

    def time_label(self) -> str:
        def mmss(s):
            return f"{int(s)//60:02d}:{int(s)%60:02d}"
        if self.t_end_s - self.t_start_s < 3:
            return mmss(self.mid_s)
        return f"{mmss(self.t_start_s)}–{mmss(self.t_end_s)}"

    def issue(self) -> str:
        parts = []
        if self.heavy_bump:
            parts.append("HEAVY BUMP")
        elif self.bump:
            parts.append("bump")
        if self.rms_max > RMS_UNCOMFORTABLE:
            parts.append(f"RMS {self.rms_max:.2f} m/s² (uncomfortable)")
        elif self.rms_max > 0.5:
            parts.append(f"RMS {self.rms_max:.2f} m/s² (rough)")
        if self.jerk_max > JERK_NORMALIZER:
            parts.append(f"jerk {self.jerk_max:.0f} m/s³")
        return " + ".join(parts) if parts else "elevated vibration"


GAP_SECONDS = 5.0          # split contiguous chunks at gaps > this many seconds


def _contiguous_chunks(t_s: np.ndarray, fs: float):
    """Yield (start_idx, end_idx_exclusive) for runs where consecutive
    samples are within GAP_SECONDS of each other. This prevents bad-spot
    segments from chaining across paused/resumed recording gaps."""
    if len(t_s) == 0:
        return
    starts = [0]
    for i in range(1, len(t_s)):
        if t_s[i] - t_s[i-1] > GAP_SECONDS:
            starts.append(i)
    starts.append(len(t_s))
    for a, b in zip(starts[:-1], starts[1:]):
        if b - a >= int(WINDOW_SECONDS * fs):
            yield a, b


def find_bad_segments(accel: pd.DataFrame) -> tuple[list, list]:
    t_s    = accel['t_s'].values
    fs     = detect_sample_rate(accel)
    # Orientation-independent vibration intensity: deviation of the total
    # acceleration magnitude from the gravity baseline. Works regardless of
    # how the phone happens to be mounted (Y-up, Z-up, etc.).
    mag    = np.sqrt(accel['x'].values**2 + accel['y'].values**2 + accel['z'].values**2)
    y_cent = mag - GRAVITY                          # signed deviation
    dt     = 1.0 / fs
    jerk_y = np.concatenate(([0.0], np.abs(np.diff(mag)) / dt))

    win  = int(WINDOW_SECONDS * fs)
    step = win // 2

    rows = []
    all_segments = []

    for chunk_start, chunk_end in _contiguous_chunks(t_s, fs):
        chunk_rows = []
        for i in range(chunk_start, chunk_end - win, step):
            cy   = y_cent[i:i+win]
            cj   = jerk_y[i:i+win]
            cm   = mag[i:i+win]
            # Sanity: skip any window where samples span more than 4*WINDOW
            # (means the chunk-boundary detection missed something)
            if t_s[i+win-1] - t_s[i] > 4 * WINDOW_SECONDS:
                continue
            rms  = float(np.sqrt(np.mean(cy ** 2)))
            jmax = float(np.max(cj))
            heavy = bool(np.any(cm > HEAVY_BUMP_MAG))
            soft  = bool(np.any(cm > BUMP_MAG)) and not heavy
            severity = (
                0.6 * (rms / RMS_UNCOMFORTABLE) +
                0.4 * (jmax / JERK_NORMALIZER) +
                (1.0 if heavy else 0.0) +
                (0.3 if soft else 0.0)
            )
            rows.append({
                't_mid_s':   float(t_s[i + win // 2]),
                't_start_s': float(t_s[i]),
                't_end_s':   float(t_s[i + win - 1]),
                'rms': rms, 'jerk': jmax,
                'heavy': heavy, 'bump': soft,
                'severity': severity,
            })
            chunk_rows.append(rows[-1])

        # Group bad windows in this chunk only
        current = None
        for r in chunk_rows:
            if r['severity'] >= SEVERITY_CUTOFF:
                if current is None:
                    current = {**r}
                else:
                    current['t_end_s']  = r['t_end_s']
                    current['rms']      = max(current['rms'], r['rms'])
                    current['jerk']     = max(current['jerk'], r['jerk'])
                    current['heavy']    = current['heavy'] or r['heavy']
                    current['bump']     = current['bump']  or r['bump']
                    current['severity'] = max(current['severity'], r['severity'])
            else:
                if current is not None:
                    all_segments.append(current); current = None
        if current is not None:
            all_segments.append(current)

    all_segments.sort(key=lambda s: s['severity'], reverse=True)

    return [BadSegment(
        rank=0,                # filled in by caller
        severity=s['severity'],
        t_start_s=s['t_start_s'],
        t_end_s=s['t_end_s'],
        rms_max=s['rms'],
        jerk_max=s['jerk'],
        heavy_bump=s['heavy'],
        bump=s['bump'],
        lat=float('nan'), lon=float('nan'),
    ) for s in all_segments], rows


# ── Plotting ─────────────────────────────────────────────────────────────

def plot_map(gpx: GpxTrack, window_rows, segments: list[BadSegment],
             anchor_utc: datetime, accel_t0_s: float, out_png: Path,
             title: str,
             accel: pd.DataFrame | None = None,
             gyro: pd.DataFrame | None = None) -> None:
    # Per-window RMS interpolated onto the GPX timeline to color the route.
    # Points outside the CSV's time coverage stay NaN and are filtered out
    # before plotting, so the route only shows color where sensor data exists.
    if window_rows:
        win_t_utc = [anchor_utc + timedelta(seconds=r['t_mid_s'] - accel_t0_s)
                     for r in window_rows]
        win_rms   = np.array([r['rms'] for r in window_rows])
    else:
        win_t_utc, win_rms = [], np.array([])

    if len(win_t_utc):
        win_secs = np.array([(t - win_t_utc[0]).total_seconds() for t in win_t_utc])
        gpx_secs = np.array([(t - win_t_utc[0]).total_seconds() for t in gpx.times])
        # Nearest-neighbor lookup, NOT linear interpolation. Each GPS point
        # inherits the RMS of its closest 2-second window — no gradient
        # blending across windows. So consecutive GPS points inside the same
        # window share one colour, and a quiet stretch stays quiet right up
        # to the window that contains the actual bump.
        idx_right = np.searchsorted(win_secs, gpx_secs)
        idx_right = np.clip(idx_right, 1, len(win_secs) - 1)
        idx_left  = idx_right - 1
        use_right = (gpx_secs - win_secs[idx_left]) > (win_secs[idx_right] - gpx_secs)
        nearest_idx = np.where(use_right, idx_right, idx_left)
        gpx_rms = win_rms[nearest_idx].astype(float)
        # Points outside the CSV's time coverage stay uncoloured.
        outside = (gpx_secs < win_secs[0]) | (gpx_secs > win_secs[-1])
        gpx_rms[outside] = np.nan
    else:
        gpx_rms = np.full(len(gpx.times), np.nan)

    fig = plt.figure(figsize=(16, 9))
    gs  = fig.add_gridspec(1, 5, wspace=0.3)
    ax  = fig.add_subplot(gs[0, :3])
    tab = fig.add_subplot(gs[0, 3:]); tab.axis('off')

    # Faint base route (the full GPX path, including uncovered tail)
    ax.plot(gpx.lons, gpx.lats, color='#999', lw=1.2, alpha=0.4, zorder=1)

    # Discrete 3-band colour for the route, NOT a continuous gradient:
    #   < ISO comfortable (0.5 m/s²)  → green   (smooth)
    #   < ISO uncomfortable (1.15)    → yellow  (rough)
    #   ≥ ISO uncomfortable           → red     (uncomfortable / damaging)
    # Each GPS point falls into exactly one band — no time-interpolated fade.
    valid = ~np.isnan(gpx_rms)
    lons_arr = np.asarray(gpx.lons)
    lats_arr = np.asarray(gpx.lats)
    route_cmap = ListedColormap(['#2ca02c', '#ffcc00', '#d62728'])  # green / yellow / red
    route_norm = BoundaryNorm([0.0, 0.5, RMS_UNCOMFORTABLE, 1e6], route_cmap.N)
    sc = ax.scatter(lons_arr[valid], lats_arr[valid], c=gpx_rms[valid],
                    cmap=route_cmap, norm=route_norm, s=14, zorder=2)
    cb = plt.colorbar(sc, ax=ax, fraction=0.025, pad=0.01,
                      ticks=[0.25, (0.5+RMS_UNCOMFORTABLE)/2, RMS_UNCOMFORTABLE + 0.5])
    cb.ax.set_ylim(0.0, RMS_UNCOMFORTABLE + 1.0)
    cb.ax.set_yticklabels([
        f"smooth\n< 0.5",
        f"rough\n0.5–{RMS_UNCOMFORTABLE}",
        f"uncomfortable\n≥ {RMS_UNCOMFORTABLE}",
    ])
    cb.set_label("Vertical RMS band  (m/s²)", fontsize=9)

    # Individual bump pins (one dot per detected event), drawn between the
    # route and the numbered severe-segment markers.
    if accel is not None and len(accel) > 0:
        events = detect_events_offline(accel, gyro if gyro is not None else pd.DataFrame())
        if not events.empty:
            for kind, marker, fc, ec, sz in [
                ('bump',       'o', '#ff9900', 'black', 30),
                ('heavy_bump', 's', '#cc0000', 'black', 55),
            ]:
                ev = events[events['kind'] == kind]
                if ev.empty:
                    continue
                ev_utc = [anchor_utc + timedelta(seconds=float(t) - accel_t0_s)
                          for t in ev['t_s'].values]
                ev_lats, ev_lons = [], []
                for t_utc in ev_utc:
                    lat, lon = gps_at(gpx.times, gpx.lats, gpx.lons, t_utc)
                    if not np.isnan(lat):
                        ev_lats.append(lat); ev_lons.append(lon)
                if ev_lats:
                    ax.scatter(ev_lons, ev_lats, marker=marker, c=fc,
                               edgecolors=ec, linewidths=0.6, s=sz, zorder=2.5,
                               label=f"{kind} ({len(ev_lats)})")

    # Numbered pins for top-N severe segments (drawn last so they sit on top)
    for seg in segments[:TOP_N]:
        if not np.isnan(seg.lat):
            ax.plot(seg.lon, seg.lat, 'o', mfc='white', mec='black',
                    ms=20, mew=1.6, zorder=3)
            ax.text(seg.lon, seg.lat, str(seg.rank), color='black',
                    fontsize=10, fontweight='bold',
                    ha='center', va='center', zorder=4)

    # Start / end markers
    ax.plot(gpx.lons[0], gpx.lats[0], '^', color='#0a0', ms=12, mec='black', zorder=5)
    ax.plot(gpx.lons[-1], gpx.lats[-1], 's', color='#a00', ms=11, mec='black', zorder=5)
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_aspect('equal', adjustable='datalim')
    ax.set_title(title, fontsize=12)
    ax.grid(True, alpha=0.2)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(handles, labels, loc='upper left', fontsize=8, framealpha=0.9)

    # Right panel: top-N table
    tab.set_title(f"Top {min(TOP_N, len(segments))} bad spots",
                  fontsize=12, loc='left')
    header = f"{'#':<3}{'sev':>5}  {'time':<13}{'rms':>6}{'jerk':>7}  what"
    lines = [header, "-" * 60]
    for seg in segments[:TOP_N]:
        flag = "HV" if seg.heavy_bump else ("bp" if seg.bump else "  ")
        lines.append(
            f"{seg.rank:<3}{seg.severity:>5.2f}  {seg.time_label():<13}"
            f"{seg.rms_max:>5.2f}  {seg.jerk_max:>5.0f}  {flag}"
        )
    tab.text(0, 0.95, "\n".join(lines), family='monospace',
             fontsize=9, va='top', ha='left')

    plt.savefig(out_png, dpi=130, bbox_inches='tight')
    plt.close(fig)


# ── Main ─────────────────────────────────────────────────────────────────

def _format_hms(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def main():
    parser = argparse.ArgumentParser(
        description="WayTrace Locate — pin road problems to actual streets.",
        usage="waytrace_locate.py <ART-*.csv> <GPS-*.gpx> [--chair NAME]",
    )
    parser.add_argument("art_path", type=Path, help="ART-*.csv path")
    parser.add_argument("gpx_path", type=Path, help="GPS-*.gpx path")
    parser.add_argument("--chair", default=None,
                        help="per-chair calibration profile (file in ~/.config/waytrace/chairs/<NAME>.json)")
    args = parser.parse_args()

    art_path = args.art_path
    gpx_path = args.gpx_path
    for p in (art_path, gpx_path):
        if not p.exists():
            sys.exit(f"File not found: {p}")

    print(f"Loading {art_path.name} ...")
    df = load_csv(art_path)
    accel, gyro = split_sensors(df)
    generation, sensors_present = detect_generation(df, art_path)
    print(f"   file generation: {generation_banner(generation, sensors_present)}")
    print(f"   accel rows: {len(accel)}   duration: {accel['t_s'].iloc[-1]:.1f}s")

    print(f"Loading {gpx_path.name} ...")
    gpx = load_gpx(gpx_path)
    print(f"   GPX points: {len(gpx.lats)}   "
          f"span: {gpx.times[0].isoformat()} → {gpx.times[-1].isoformat()}")

    # Anchor: when did the ART file's t_s=0 happen in wall-clock UTC?
    art_start_local = parse_art_start_from_filename(art_path)
    anchor_utc      = choose_anchor(art_start_local, gpx.times[0])
    accel_t0        = float(accel['t_s'].iloc[0])  # essentially 0

    print(f"Detecting bad spots (window={WINDOW_SECONDS}s, cutoff={SEVERITY_CUTOFF}) ...")
    segments, window_rows = find_bad_segments(accel)
    print(f"   bad segments found: {len(segments)}")

    # Speed per GPS point + chair profile (both optional/forward-compat)
    speeds_per_point = gps_speed_series(gpx)
    chair_profile    = load_chair_profile(args.chair)
    if chair_profile:
        print(f"   chair profile loaded: {args.chair}")

    in_dur, total_dur = speed_in_range_duration(gpx, speeds_per_point)
    pct = (100.0 * in_dur / total_dur) if total_dur > 0 else 0.0

    # Tag each segment with GPS coords, speed, and in-range flag
    for i, seg in enumerate(segments, start=1):
        seg.rank = i
        mid_utc = anchor_utc + timedelta(seconds=seg.mid_s - accel_t0)
        seg.lat, seg.lon = gps_at(gpx.times, gpx.lats, gpx.lons, mid_utc)
        seg.speed_ms = speed_at(gpx, speeds_per_point, mid_utc)
        seg.in_speed_range = SPEED_MIN_MS <= seg.speed_ms <= SPEED_MAX_MS

    in_range_segs    = [s for s in segments if s.in_speed_range]
    out_of_range_segs = [s for s in segments if not s.in_speed_range]

    # Rerank the in-range segments by severity (already sorted, but reassign #)
    in_range_segs.sort(key=lambda s: s.severity, reverse=True)
    for new_rank, seg in enumerate(in_range_segs, start=1):
        seg.rank = new_rank

    # Out-of-range segments are listed separately; keep them severity-sorted too.
    out_of_range_segs.sort(key=lambda s: s.severity, reverse=True)

    # Outputs
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    out_dir = art_path.parent
    out_png = out_dir / f"LOC-{stamp}.png"
    out_txt = out_dir / f"LOC-{stamp}.txt"

    title = f"{art_path.name} + {gpx_path.name}  —  {gpx.name}"
    plot_map(gpx, window_rows, in_range_segs, anchor_utc, accel_t0, out_png, title,
             accel=accel, gyro=gyro)

    # Text report
    lines = []
    lines.append("WayTrace location-tagged road quality report")
    lines.append(f"  ART: {art_path.name}")
    lines.append(f"  GPX: {gpx_path.name}   ({gpx.name})")
    lines.append(f"  File generation: {generation_banner(generation, sensors_present)}")
    if args.chair:
        cp = "loaded" if chair_profile else "requested but not found — fallback to uncalibrated"
        lines.append(f"  Chair profile: {args.chair} ({cp})")
    lines.append(f"  Anchor (UTC): {anchor_utc.isoformat()}")
    lines.append(f"  Duration: {accel['t_s'].iloc[-1]:.1f}s    "
                 f"GPS points: {len(gpx.lats)}    "
                 f"Bad segments: {len(segments)}  "
                 f"(in-range: {len(in_range_segs)}, out-of-range: {len(out_of_range_segs)})")
    lines.append(f"  Speed-in-range window: {SPEED_MIN_MS}–{SPEED_MAX_MS} m/s "
                 f"(Wolf 2005). In-range duration: {_format_hms(in_dur)} "
                 f"of {_format_hms(total_dur)} "
                 f"({pct:.0f}%).")
    lines.append("")
    lines.append("In-range bad spots (ranked by severity):")
    lines.append(f"{'Rank':<5}{'Sev':>5}  {'Time':<14}{'Speed':>6}  "
                 f"{'Lat':>11}, {'Lon':>11}   Issue")
    lines.append("-" * 100)
    for seg in in_range_segs[:TOP_N]:
        lines.append(
            f"{seg.rank:<5}{seg.severity:>5.2f}  {seg.time_label():<14}"
            f"{seg.speed_ms:>5.2f}m/s  "
            f"{seg.lat:>11.6f}, {seg.lon:>11.6f}   {seg.issue()}"
        )
    if len(in_range_segs) > TOP_N:
        lines.append(f"... ({len(in_range_segs) - TOP_N} more not shown)")

    if out_of_range_segs:
        lines.append("")
        lines.append("Out-of-range hotspots "
                     f"(speed outside {SPEED_MIN_MS}–{SPEED_MAX_MS} m/s — "
                     "excluded from ranking, listed for sanity check):")
        lines.append(f"{'Sev':>5}  {'Time':<14}{'Speed':>6}  "
                     f"{'Lat':>11}, {'Lon':>11}   Issue")
        lines.append("-" * 100)
        for seg in out_of_range_segs[:TOP_N]:
            lines.append(
                f"{seg.severity:>5.2f}  {seg.time_label():<14}"
                f"{seg.speed_ms:>5.2f}m/s  "
                f"{seg.lat:>11.6f}, {seg.lon:>11.6f}   {seg.issue()}"
            )
        if len(out_of_range_segs) > TOP_N:
            lines.append(f"... ({len(out_of_range_segs) - TOP_N} more not shown)")

    out_txt.write_text("\n".join(lines) + "\n")

    print(f"\nMap:    {out_png}")
    print(f"Report: {out_txt}")
    print()
    # Show top of report (header + first segments)
    print("\n".join(lines[:8]))
    print("...")
    tail_lines = []
    if in_range_segs:
        tail_lines.append("")
        tail_lines.append("In-range bad spots:")
        for seg in in_range_segs[:5]:
            tail_lines.append(
                f"  {seg.rank} sev {seg.severity:.2f}  {seg.time_label()}  "
                f"{seg.speed_ms:.2f}m/s  "
                f"{seg.lat:.6f},{seg.lon:.6f}  {seg.issue()}"
            )
    print("\n".join(tail_lines))


if __name__ == "__main__":
    main()
