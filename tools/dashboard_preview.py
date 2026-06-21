#!/usr/bin/env python3
"""dashboard_preview.py — QA-0 static preview frames for the OSI-007 HUD.

Per SRS DASHBOARD QA BUNDLE (2026-06-21), no batch runs until preview
PNGs are reviewed. This script generates one PNG per visual state of the
HUD, composited on top of a real extracted video frame, so the full
dashboard can be reviewed without waiting on an hour-long batch.

States rendered (one PNG each, written to OUTDIR):
  footer_green   — VDV well below threshold
  footer_amber   — VDV near threshold
  footer_red     — VDV above threshold
  iso_class_A    — speed readout colour for ISO 8608 class A
  iso_class_B    — …class B
  iso_class_C    — …class C
  iso_class_D    — …class D
  iso_class_E    — …class E
  iso_class_F    — …class F
  multi_persons  — 3 GREEN person boxes with confidence
  one_vehicle    — 1 RED vehicle box with confidence
  no_detections  — clean footage, no overlays beyond HUD
  speed_zero     — paused/stopped (speed=0.0)
  speed_high     — fast push (~9 km/h)

QA fixes baked in (each preview PNG is the proposed final look):
  QA-1  footer trace = VDV (not RMS), label "VDV (ISO 2631-1, Wk)"
  QA-2  threshold line in DASHED MAGENTA, distinct from white trace
  QA-3  threshold value/units in VDV units: "8.5 m/s^1.75 (LOW-risk)"
  QA-4  no Unicode superscripts — ASCII "m/s^1.75" so any font renders
  QA-5  footer text scaled up ~2x from the prototype
  QA-6  trace fills full vertical band; metric label at right edge
  QA-7  speed readout colour cycles by ISO 8608 class (A=green … F=red)
  QA-8  title and speed share one left anchor (x=70)
  QA-9  minimap border DARK GRAY (was green); "ROUTE" label same gray

Usage:
    python tools/dashboard_preview.py \\
        --art   ART-202606181630.csv \\
        --gpx   GPS-202606181630.gpx \\
        --frame sample_frame.png \\
        --out   previews/

The same renderer is then ported into osi007_dashboard.py once Vasil
approves the previews.
"""
import argparse
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

# ── canvas + layout ─────────────────────────────────────────────────────
CANVAS_W, CANVAS_H = 1920, 1080

LEFT_ANCHOR = 70                # QA-8: title + speed share this x
TITLE_BASELINE = 60
SPEED_BASELINE = 145

MAP_X, MAP_Y, MAP_W, MAP_H = 1430, 30, 460, 300

# QA-6: footer band — full vertical use
FOOTER_Y, FOOTER_H = 970, 110   # was 980,100; gain a little height
FOOTER_PAD_TOP = 14
FOOTER_PAD_BOT = 24             # leave room for tick labels at the bottom

GRAVITY = 9.81

# ── colours (BGR — cv2 native) ──────────────────────────────────────────
YELLOW   = (51, 204, 255)
WHITE    = (255, 255, 255)
BLACK    = (0, 0, 0)
DARK_GRAY = (90, 90, 90)        # QA-9: minimap border + ROUTE label
MAGENTA  = (220, 0, 220)        # QA-2: threshold line, distinct from trace
TRACE_WHITE = (245, 245, 245)   # the live VDV trace
ROUTE_YELLOW = (51, 204, 255)
DOT_RED  = (51, 51, 255)

# Footer background tints by current VDV (semi-transparent)
GREEN_BG = (0, 60, 0)
AMBER_BG = (0, 95, 120)
RED_BG   = (0, 0, 95)
FOOTER_BORDER = (90, 0, 160)    # dark red-magenta hairline

# QA-7: speed colour cycles with ISO 8608 class A..F
ISO_CLASS_COLOR = {
    "A": (80, 220, 80),     # excellent — green
    "B": (140, 230, 80),    # good — yellow-green
    "C": (80, 230, 230),    # fair — yellow
    "D": (40, 165, 255),    # poor — orange
    "E": (40, 90, 240),     # very poor — red-orange
    "F": (40, 40, 220),     # hazardous — red
}

# YOLO box colours per the SRS locked language
BOX_RED   = (0,   0,   204)   # vehicles
BOX_GREEN = (0,   170, 0)     # person / bicycle
BOX_BLUE  = (204, 85,  0)     # small obstacles

# Bright VDV-bucket colours for the minimap polyline (different from
# the dark footer-background tints — these need to read against video).
MAP_BRIGHT_GREEN = (50, 220, 50)
MAP_BRIGHT_AMBER = (40, 175, 240)   # orange-yellow in BGR
MAP_BRIGHT_RED   = (60, 60, 235)
MAP_UNPASSED_GRAY = (130, 130, 130)


def vdv_to_map_color(v):
    if v < VDV_GREEN_LIMIT:   return MAP_BRIGHT_GREEN
    if v < VDV_AMBER_LIMIT:   return MAP_BRIGHT_AMBER
    return MAP_BRIGHT_RED

# ── VDV thresholds (rolling 10s window heuristic) ───────────────────────
# ISO 2631-1 VDV is defined cumulatively; here we plot a rolling 10-second
# window so the trace responds to road conditions. The threshold shown is
# the ISO 2631-1 LOW-risk boundary, expressed in VDV units. Tunable.
VDV_WINDOW_S = 10.0
VDV_THRESH = 8.5                # m/s^1.75 — LOW-risk boundary
VDV_PLOT_MAX = 25.0             # axis top
VDV_GREEN_LIMIT = 2.0           # background green below this
VDV_AMBER_LIMIT = 8.5           # background amber up to threshold


# ── ART / GPX loaders (shared with osi007_dashboard.py) ─────────────────
def parse_gpx(path: Path):
    ns = "http://www.topografix.com/GPX/1/1"
    pts = []
    for tp in ET.parse(path).getroot().iter(f"{{{ns}}}trkpt"):
        t = datetime.fromisoformat(
            tp.find(f"{{{ns}}}time").text.replace("Z", "+00:00"))
        pts.append((t.timestamp(),
                    float(tp.attrib["lat"]),
                    float(tp.attrib["lon"])))
    return np.array(pts, dtype=np.float64)


def gpx_speeds_kmh(gpx):
    R = 6_371_000.0
    lat = np.radians(gpx[:, 1]); lon = np.radians(gpx[:, 2])
    dlat = np.diff(lat); dlon = np.diff(lon)
    a = (np.sin(dlat/2)**2
         + np.cos(lat[:-1])*np.cos(lat[1:])*np.sin(dlon/2)**2)
    seg_m = 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    dt = np.diff(gpx[:, 0])
    v = np.divide(seg_m, np.maximum(dt, 0.1)) * 3.6
    return np.concatenate(([0.0], v))


def project_gpx_to_box(gpx, w, h, margin=22):
    lat = gpx[:, 1]; lon = gpx[:, 2]
    lat_c = 0.5 * (lat.min() + lat.max())
    lon_c = 0.5 * (lon.min() + lon.max())
    lat_range = max(lat.max() - lat.min(), 1e-9)
    lon_range = max(lon.max() - lon.min(), 1e-9)
    lon_scale = np.cos(np.radians(lat_c))
    sx = (w - 2*margin) / (lon_range * lon_scale)
    sy = (h - 2*margin) / lat_range
    s = min(sx, sy)

    def proj(la, lo):
        x = (lo - lon_c) * lon_scale * s + w / 2
        y = h / 2 - (la - lat_c) * s
        return int(x), int(y)
    return proj


def load_art(path: Path):
    df = pd.read_csv(path, low_memory=False)
    acc = df[df["sensor"] == "accel"].copy().reset_index(drop=True)
    if acc.empty:
        raise SystemExit(f"no accel rows in {path}")
    acc["t_s"] = (acc["timestamp_ms"]
                  - acc["timestamp_ms"].iloc[0]) / 1000.0
    return acc


def vdv_series(acc, fs, window_s=VDV_WINDOW_S, hop_s=1.0):
    """Rolling VDV. VDV = (∫ a^4 dt)^(1/4). Window is a sliding integration
    of a_w^4, then 4th-rooted. Wk weighting is approximated by removing g
    from |a|; the full ISO filter chain lives in waytrace_analysis.py."""
    a = (np.sqrt(acc["x"]**2 + acc["y"]**2 + acc["z"]**2)
         - GRAVITY).to_numpy()
    t = acc["t_s"].to_numpy()
    dt = 1.0 / fs
    win = int(window_s * fs); hop = int(hop_s * fs)
    if win >= len(a):
        v = float((np.sum(a**4) * dt) ** 0.25)
        return np.array([t[len(t)//2]]), np.array([v])
    ts, vdv = [], []
    a4 = a**4
    for i in range(0, len(a) - win, hop):
        ts.append(t[i + win // 2])
        vdv.append(float((np.sum(a4[i:i+win]) * dt) ** 0.25))
    return np.array(ts), np.array(vdv)


# ── draw helpers ─────────────────────────────────────────────────────────
def text_outlined(img, text, org, scale, color, thickness=2,
                  outline=4, font=cv2.FONT_HERSHEY_DUPLEX):
    cv2.putText(img, text, org, font, scale, BLACK,
                thickness + outline, cv2.LINE_AA)
    cv2.putText(img, text, org, font, scale, color,
                thickness, cv2.LINE_AA)


# ── LAYER 2: title + speed ──────────────────────────────────────────────
def draw_title_and_speed(frame, title, speed_kmh, iso_class):
    """QA-7: speed colour by ISO 8608 class.
    QA-8 (fixed): title + speed + class line all share LEFT_ANCHOR.
    Bug in v1: speed was f"{x:5.1f}" which left-padded with space —
    visually shifted right. Now no width spec, raw "5.6 km/h"."""
    text_outlined(frame, title, (LEFT_ANCHOR, TITLE_BASELINE),
                  1.1, YELLOW, thickness=2, outline=4)
    speed_color = ISO_CLASS_COLOR.get(iso_class, WHITE)
    text_outlined(frame, f"{speed_kmh:.1f} km/h",
                  (LEFT_ANCHOR, SPEED_BASELINE),
                  2.1, speed_color, thickness=4, outline=5)
    text_outlined(frame, f"ISO 8608 class {iso_class}",
                  (LEFT_ANCHOR, SPEED_BASELINE + 36),
                  0.65, speed_color, thickness=2, outline=3)


# ── LAYER 3: minimap ────────────────────────────────────────────────────
def draw_map(frame, proj, route_pts, cur_idx, gpx_vdv):
    """QA-9 + Vasil's clarification (2026-06-21 21:30):
       - dark gray border + label (QA-9)
       - UNPASSED road = gray (the future part)
       - PASSED road = coloured by VDV at that point
         (green smooth / amber moderate / red rough — same buckets
         as the footer background)
       - YOU dot rides the seam between passed and unpassed.
    cur_idx = GPX point index of current position (0..len-1).
    gpx_vdv = per-GPX-point VDV value (parallel to route_pts)."""
    cv2.rectangle(frame, (MAP_X, MAP_Y),
                  (MAP_X+MAP_W, MAP_Y+MAP_H), DARK_GRAY, 2)
    text_outlined(frame, "ROUTE",
                  (MAP_X + MAP_W//2 - 32, MAP_Y + 22),
                  0.65, DARK_GRAY, thickness=1, outline=3)
    if len(route_pts) > 1:
        pts_xy = [(p[0]+MAP_X, p[1]+MAP_Y) for p in route_pts]
        # ── 1) UNPASSED first (drawn underneath) — gray line, with halo
        future = pts_xy[cur_idx:]
        if len(future) > 1:
            fa = np.array(future, dtype=np.int32)
            cv2.polylines(frame, [fa], False, BLACK, 6, cv2.LINE_AA)
            cv2.polylines(frame, [fa], False, MAP_UNPASSED_GRAY,
                          3, cv2.LINE_AA)
        # ── 2) PASSED — per-segment colour by VDV at that point
        if cur_idx > 1:
            past = pts_xy[:cur_idx+1]
            pa = np.array(past, dtype=np.int32)
            cv2.polylines(frame, [pa], False, BLACK, 6, cv2.LINE_AA)
            # Colour each segment by VDV of its start point
            for i in range(len(past) - 1):
                c = vdv_to_map_color(gpx_vdv[i])
                cv2.line(frame, past[i], past[i+1], c, 3, cv2.LINE_AA)
    # YOU dot at cur_idx
    if 0 <= cur_idx < len(route_pts):
        dx, dy = route_pts[cur_idx]
        cv2.circle(frame, (dx+MAP_X, dy+MAP_Y), 9, WHITE, 2, cv2.LINE_AA)
        cv2.circle(frame, (dx+MAP_X, dy+MAP_Y), 7, DOT_RED, -1, cv2.LINE_AA)
        text_outlined(frame, "YOU",
                      (dx+MAP_X+14, dy+MAP_Y+6),
                      0.55, WHITE, thickness=1, outline=3)


# ── LAYER 4: footer VDV trace ───────────────────────────────────────────
def vdv_to_y(v):
    """Map VDV value → y inside the footer band (top fills with FOOTER_PAD_TOP)."""
    v_clip = max(0.0, min(VDV_PLOT_MAX, v))
    avail = FOOTER_H - FOOTER_PAD_TOP - FOOTER_PAD_BOT
    return int(FOOTER_Y + FOOTER_H - FOOTER_PAD_BOT
               - (v_clip / VDV_PLOT_MAX) * avail)


def draw_dashed_hline(frame, x0, x1, y, color, dash=18, gap=10, thickness=2):
    """Custom dashed line because cv2.line doesn't support dashes."""
    x = x0
    while x < x1:
        x2 = min(x + dash, x1)
        cv2.line(frame, (x, y), (x2, y), color, thickness, cv2.LINE_AA)
        x = x2 + gap


def draw_footer(frame, t_now, t_vdv, vdv, cur_vdv):
    """QA-1,2,3,4,5,6: VDV trace, magenta-dashed threshold, ASCII units,
    bigger fonts, full-height trace."""
    # Background tint by current VDV
    if cur_vdv < VDV_GREEN_LIMIT:
        bg = GREEN_BG
    elif cur_vdv < VDV_AMBER_LIMIT:
        bg = AMBER_BG
    else:
        bg = RED_BG
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, FOOTER_Y),
                  (CANVAS_W, FOOTER_Y+FOOTER_H), bg, -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)
    cv2.rectangle(frame, (0, FOOTER_Y),
                  (CANVAS_W, FOOTER_Y+FOOTER_H), FOOTER_BORDER, 1)

    # QA-2: threshold line in DASHED MAGENTA
    thresh_y = vdv_to_y(VDV_THRESH)
    draw_dashed_hline(frame, 0, CANVAS_W, thresh_y, MAGENTA,
                      dash=22, gap=14, thickness=2)
    # QA-3, QA-4: label in VDV units, ASCII exponent
    text_outlined(frame,
                  f"VDV LOW-risk = {VDV_THRESH:.1f} m/s^1.75",
                  (18, thresh_y - 9), 0.62, MAGENTA,
                  thickness=2, outline=3)

    # 30-min rolling window: -30m … now
    window_start = t_now - 30 * 60
    mask = (t_vdv >= window_start) & (t_vdv <= t_now)
    if mask.sum() > 1:
        ts_w = t_vdv[mask]; vdv_w = vdv[mask]
        xs = ((ts_w - window_start) / (30 * 60) * CANVAS_W).astype(np.int32)
        ys = np.array([vdv_to_y(v) for v in vdv_w], dtype=np.int32)
        pts = np.stack([xs, ys], axis=1)
        if len(pts) > 1:
            cv2.polylines(frame, [pts], False, TRACE_WHITE, 2, cv2.LINE_AA)

    # Tick labels — QA-5 bigger fonts
    for i, m in enumerate([30, 25, 20, 15, 10, 5, 0]):
        x = int(30 + (CANVAS_W - 60) * i / 6)
        label = "now" if m == 0 else f"-{m}m"
        text_outlined(frame, label,
                      (x - 22, FOOTER_Y + FOOTER_H - 5),
                      0.55, (230, 230, 230), thickness=1, outline=2)

    # QA-1,4 metric label, ASCII units, bigger
    text_outlined(frame, "VDV (ISO 2631-1, Wk)",
                  (CANVAS_W - 440, FOOTER_Y + 28),
                  0.72, (250, 250, 250), thickness=2, outline=3)

    # Current value readout — top-right of footer (extra context for QA-6)
    text_outlined(frame,
                  f"now: {cur_vdv:4.1f} m/s^1.75",
                  (CANVAS_W - 440, FOOTER_Y + 60),
                  0.62, (250, 250, 250), thickness=1, outline=3)


# ── YOLO box overlay (for the detection-state previews) ─────────────────
def draw_box(frame, x1, y1, x2, y2, color, label, conf):
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    txt = f"{label} {conf:.2f}"
    (tw, th_), _ = cv2.getTextSize(
        txt, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
    cv2.rectangle(frame, (x1, y1 - th_ - 6),
                  (x1 + tw + 4, y1), color, -1)
    cv2.putText(frame, txt, (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, WHITE, 2)


# ── state composer ─────────────────────────────────────────────────────
def compose(frame_template, title, speed, iso_class,
            t_now, t_vdv, vdv, cur_vdv,
            proj, route_pts, cur_idx, gpx_vdv,
            extra_boxes=None):
    f = frame_template.copy()
    if extra_boxes:
        for b in extra_boxes:
            draw_box(f, *b)
    draw_title_and_speed(f, title, speed, iso_class)
    draw_map(f, proj, route_pts, cur_idx, gpx_vdv)
    draw_footer(f, t_now, t_vdv, vdv, cur_vdv)
    return f


def load_frame(path: Path):
    bg = cv2.imread(str(path))
    if bg is None:
        raise SystemExit(f"cannot read frame: {path}")
    if bg.shape[:2] != (CANVAS_H, CANVAS_W):
        bg = cv2.resize(bg, (CANVAS_W, CANVAS_H),
                        interpolation=cv2.INTER_LANCZOS4)
    return bg


def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--art",   required=True, type=Path)
    ap.add_argument("--gpx",   required=True, type=Path)
    ap.add_argument("--frames-dir", required=True, type=Path,
        help="Directory of frame PNG/JPGs extracted from real MOVs at "
             "different timestamps. Rotated across the preview states.")
    ap.add_argument("--out",   required=True, type=Path,
        help="output directory; one PNG per state will be written")
    ap.add_argument("--title", default="Rear Window Push",
        help="Strava activity name shown upper-left")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    # ── frames
    frame_paths = sorted(list(args.frames_dir.glob("*.png"))
                         + list(args.frames_dir.glob("*.jpg")))
    if not frame_paths:
        raise SystemExit(f"no frames in {args.frames_dir}")
    print(f"loaded {len(frame_paths)} frames from {args.frames_dir}",
          flush=True)
    frames = {p.stem: load_frame(p) for p in frame_paths}
    frame_keys = list(frames.keys())

    # ── ART → VDV series
    print(f"loading ART:   {args.art}", flush=True)
    acc = load_art(args.art)
    fs = 1.0 / np.median(np.diff(acc["t_s"].to_numpy()[:1000]))
    print(f"  {len(acc):,} accel rows, fs ≈ {fs:.1f} Hz", flush=True)
    t_vdv, vdv = vdv_series(acc, fs)
    print(f"  VDV series: {len(t_vdv):,} samples, "
          f"min={vdv.min():.2f} max={vdv.max():.2f} "
          f"median={np.median(vdv):.2f}", flush=True)
    t_vdv_max = float(t_vdv.max())

    # ── GPX
    print(f"loading GPX:   {args.gpx}", flush=True)
    gpx = parse_gpx(args.gpx)
    proj = project_gpx_to_box(gpx, MAP_W, MAP_H)
    route_pts = [proj(p[1], p[2]) for p in gpx]

    # Per-GPX-point VDV — interpolated from the ART VDV series by mapping
    # each GPX point's fractional position in the push to the matching
    # fractional position in the VDV series. ART and GPX have independent
    # clocks (different start times) but cover the same physical push, so
    # fractional progress is the cleanest map without requiring a sync.
    gpx_n = len(gpx)
    gpx_vdv = np.array([
        float(vdv[min(len(vdv)-1, int(i / max(1, gpx_n-1) * (len(vdv)-1)))])
        for i in range(gpx_n)
    ])

    # ── helpers per-state
    def state_setup(progress):
        """progress ∈ [0,1] → (cur_idx, t_now)."""
        cur_idx = max(0, min(gpx_n-1, int(progress * (gpx_n-1))))
        t_now = progress * t_vdv_max
        return cur_idx, t_now

    # state defs: (name, progress, frame_key, speed, iso_class, cur_vdv, extra_boxes)
    speed_demo = 5.6
    n_frames = len(frame_keys)
    def fkey(i): return frame_keys[i % n_frames]

    states = [
        ("footer_green",  0.30, fkey(0), speed_demo, "A",  1.2,  None),
        ("footer_amber",  0.50, fkey(1), speed_demo, "C",  5.8,  None),
        ("footer_red",    0.70, fkey(2), speed_demo, "E", 14.5,  None),

        ("iso_class_A",   0.10, fkey(3), speed_demo, "A",  0.9,  None),
        ("iso_class_B",   0.22, fkey(4), speed_demo, "B",  1.6,  None),
        ("iso_class_C",   0.34, fkey(5), speed_demo, "C",  3.4,  None),
        ("iso_class_D",   0.46, fkey(6), speed_demo, "D",  6.0,  None),
        ("iso_class_E",   0.58, fkey(0), speed_demo, "E", 11.5,  None),
        ("iso_class_F",   0.75, fkey(1), speed_demo, "F", 18.2,  None),

        ("speed_zero",    0.05, fkey(2), 0.0,        "A",  0.8,  None),
        ("speed_high",    0.40, fkey(3), 9.1,        "B",  2.2,  None),

        ("multi_persons", 0.45, fkey(4), speed_demo, "C",  4.4, [
            (520, 360, 720, 880, BOX_GREEN, "person", 0.91),
            (820, 400, 980, 870, BOX_GREEN, "person", 0.84),
            (1140, 380, 1320, 880, BOX_GREEN, "person", 0.78),
        ]),
        ("one_vehicle",   0.60, fkey(5), speed_demo, "D",  7.2, [
            (480, 420, 1180, 820, BOX_RED, "car", 0.93),
        ]),
        ("no_detections", 0.25, fkey(6), speed_demo, "B",  1.4, []),
    ]

    for name, progress, fk, speed, iso_class, cur_vdv, extra_boxes in states:
        cur_idx, t_now = state_setup(progress)
        img = compose(frames[fk], args.title,
                      speed=speed, iso_class=iso_class,
                      t_now=t_now, t_vdv=t_vdv, vdv=vdv,
                      cur_vdv=cur_vdv,
                      proj=proj, route_pts=route_pts,
                      cur_idx=cur_idx, gpx_vdv=gpx_vdv,
                      extra_boxes=extra_boxes)
        out_path = args.out / f"preview-{name}.png"
        cv2.imwrite(str(out_path), img,
                    [cv2.IMWRITE_PNG_COMPRESSION, 4])
        print(f"  wrote {out_path.name}  "
              f"frame={fk}  prog={progress:.2f}  "
              f"speed={speed:.1f}km/h  iso={iso_class}  VDV={cur_vdv:.1f}",
              flush=True)

    print(f"\nDone — {len(states)} preview PNGs in {args.out}/", flush=True)


if __name__ == "__main__":
    main()
