#!/usr/bin/env python3
"""OSI-007 Phase-2 video dashboard compositor (PROTOTYPE).

Draws the HUD overlays defined in OSI-007-DASHBOARD-SPEC.md on top of a
source MOV: push title + speed (upper-left), route map with current-
position dot (upper-right), and a 30-min scrolling roughness trace with
ISO threshold line (footer).

This is the v1 / prototype implementation. Event flashes (heavy bumps,
ISO 8608 class change, vehicles passed) and the auto-switching footer
metric are stubbed; the default speed and the windowed-RMS trace are
fully wired.

Usage:
    osi007_dashboard.py
        --video INPUT.mov
        --art   ART-YYYYMMDDHHMM.csv
        --gpx   GPS-YYYYMMDDHHMM.gpx
        --title "Rear Window Push"
        --video-art-offset -6.9
        --out   OUT.mp4
"""
import argparse
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

CANVAS_W, CANVAS_H = 1920, 1080

# ── QA-8 (2026-06-21): title + speed + ISO class all share LEFT_ANCHOR
LEFT_ANCHOR    = 70
TITLE_BASELINE = 60
SPEED_BASELINE = 145

MAP_X, MAP_Y, MAP_W, MAP_H = 1430, 30, 460, 300

# QA-6: footer band stretched slightly
FOOTER_Y, FOOTER_H = 970, 110
FOOTER_PAD_TOP = 14
FOOTER_PAD_BOT = 24

GRAVITY = 9.81

# ── BGR colours (cv2 native) ────────────────────────────────────────────
YELLOW   = (51, 204, 255)
WHITE    = (255, 255, 255)
BLACK    = (0, 0, 0)
DARK_GRAY     = (90, 90, 90)        # QA-9: minimap border + ROUTE label
MAGENTA       = (220, 0, 220)       # QA-2: threshold line
TRACE_WHITE   = (245, 245, 245)
ROUTE_YELLOW  = (51, 204, 255)
DOT_RED       = (51, 51, 255)

# Footer background tints by current VDV (dark, semi-transparent)
GREEN_BG = (0, 60, 0)
AMBER_BG = (0, 95, 120)
RED_BG   = (0, 0, 95)
FOOTER_BORDER = (90, 0, 160)

# QA-7: speed colour cycles with ISO 8608 class A..F
ISO_CLASS_COLOR = {
    "A": (80, 220, 80),
    "B": (140, 230, 80),
    "C": (80, 230, 230),
    "D": (40, 165, 255),
    "E": (40, 90, 240),
    "F": (40, 40, 220),
}

# Bright VDV-bucket colours for the minimap polyline (passed road)
MAP_BRIGHT_GREEN  = (50, 220, 50)
MAP_BRIGHT_AMBER  = (40, 175, 240)
MAP_BRIGHT_RED    = (60, 60, 235)
MAP_UNPASSED_GRAY = (130, 130, 130)

# ── VDV (QA-1) thresholds ───────────────────────────────────────────────
VDV_WINDOW_S    = 10.0
VDV_THRESH      = 8.5      # m/s^1.75 — LOW-risk boundary
VDV_PLOT_MAX    = 25.0
VDV_GREEN_LIMIT = 2.0
VDV_AMBER_LIMIT = 8.5

# Interim VDV→ISO 8608 class buckets (Appendix A coefficient-C method
# is in waytrace_analysis.py and will replace this when wired).
def vdv_to_iso_class(v):
    if v < 1.5:  return "A"
    if v < 3.0:  return "B"
    if v < 6.0:  return "C"
    if v < 10.0: return "D"
    if v < 17.0: return "E"
    return "F"


def vdv_to_map_color(v):
    if v < VDV_GREEN_LIMIT: return MAP_BRIGHT_GREEN
    if v < VDV_AMBER_LIMIT: return MAP_BRIGHT_AMBER
    return MAP_BRIGHT_RED


# ── GPX -------------------------------------------------------------------
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
    """Per-point speed (km/h). Index 0 = 0; index i = speed from i-1 to i."""
    R = 6_371_000.0
    lat = np.radians(gpx[:, 1]); lon = np.radians(gpx[:, 2])
    dlat = np.diff(lat); dlon = np.diff(lon)
    a = np.sin(dlat/2)**2 + np.cos(lat[:-1])*np.cos(lat[1:])*np.sin(dlon/2)**2
    seg_m = 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    dt = np.diff(gpx[:, 0])
    v = np.divide(seg_m, np.maximum(dt, 0.1)) * 3.6
    return np.concatenate(([0.0], v))


def project_gpx_to_box(gpx, w, h, margin=22):
    """Return projection function (lat, lon) → (x, y) inside (0,0)-(w,h)."""
    lat = gpx[:, 1]; lon = gpx[:, 2]
    lat_c = 0.5 * (lat.min() + lat.max())
    lon_c = 0.5 * (lon.min() + lon.max())
    lat_range = max(lat.max() - lat.min(), 1e-9)
    lon_range = max(lon.max() - lon.min(), 1e-9)
    # equal-aspect using cos(lat) so the map keeps its proportions
    lon_scale = np.cos(np.radians(lat_c))
    sx = (w - 2*margin) / (lon_range * lon_scale)
    sy = (h - 2*margin) / lat_range
    s = min(sx, sy)

    def proj(la, lo):
        x = (lo - lon_c) * lon_scale * s + w / 2
        y = h / 2 - (la - lat_c) * s
        return int(x), int(y)
    return proj


# ── ART -------------------------------------------------------------------
def load_art(path: Path):
    df = pd.read_csv(path, low_memory=False)
    acc = df[df["sensor"] == "accel"].copy().reset_index(drop=True)
    if acc.empty:
        raise SystemExit(f"no accel rows in {path}")
    acc["t_s"] = (acc["timestamp_ms"] - acc["timestamp_ms"].iloc[0]) / 1000.0
    return acc


def windowed_rms_series(acc, fs, window_s=10.0, hop_s=1.0):
    vib = (np.sqrt(acc["x"]**2 + acc["y"]**2 + acc["z"]**2)
           - GRAVITY).to_numpy()
    t = acc["t_s"].to_numpy()
    win = int(window_s * fs)
    hop = int(hop_s * fs)
    if win >= len(vib):
        return np.array([t[len(t)//2]]), np.array([float(np.sqrt(np.mean(vib**2)))])
    ts, rms = [], []
    for i in range(0, len(vib) - win, hop):
        ts.append(t[i + win // 2])
        rms.append(float(np.sqrt(np.mean(vib[i:i+win]**2))))
    return np.array(ts), np.array(rms)


def vdv_series(acc, fs, window_s=VDV_WINDOW_S, hop_s=1.0):
    """Rolling VDV. VDV = (∫ a^4 dt)^(1/4). Approximate Wk by removing
    gravity from |a|; the rigorous Wk filter lives in waytrace_analysis."""
    a = (np.sqrt(acc["x"]**2 + acc["y"]**2 + acc["z"]**2)
         - GRAVITY).to_numpy()
    t = acc["t_s"].to_numpy()
    dt = 1.0 / fs
    win = int(window_s * fs); hop = int(hop_s * fs)
    if win >= len(a):
        v = float((np.sum(a**4) * dt) ** 0.25)
        return np.array([t[len(t)//2]]), np.array([v])
    ts, out = [], []
    a4 = a**4
    for i in range(0, len(a) - win, hop):
        ts.append(t[i + win // 2])
        out.append(float((np.sum(a4[i:i+win]) * dt) ** 0.25))
    return np.array(ts), np.array(out)


# ── Drawing helpers ------------------------------------------------------
def text_outlined(img, text, org, scale, color, thickness=2,
                  outline=4, font=cv2.FONT_HERSHEY_DUPLEX):
    cv2.putText(img, text, org, font, scale, BLACK,
                thickness + outline, cv2.LINE_AA)
    cv2.putText(img, text, org, font, scale, color,
                thickness, cv2.LINE_AA)


def draw_title_and_speed(frame, title, speed_kmh, iso_class):
    """QA-7 speed colour by ISO class + QA-8 shared left anchor.
    speed_kmh=None → blank (e.g. GPS dropout)."""
    text_outlined(frame, title, (LEFT_ANCHOR, TITLE_BASELINE),
                  1.1, YELLOW, thickness=2, outline=4)
    speed_color = ISO_CLASS_COLOR.get(iso_class, WHITE)
    speed_text = "— km/h" if speed_kmh is None else f"{speed_kmh:.1f} km/h"
    text_outlined(frame, speed_text,
                  (LEFT_ANCHOR, SPEED_BASELINE),
                  2.1, speed_color, thickness=4, outline=5)
    text_outlined(frame, f"ISO 8608 class {iso_class}",
                  (LEFT_ANCHOR, SPEED_BASELINE + 36),
                  0.65, speed_color, thickness=2, outline=3)


def draw_map(frame, proj, route_pts, cur_idx, gpx_vdv):
    """QA-9 + Vasil's clarification 2026-06-21:
       - dark gray border + label
       - UNPASSED road = gray
       - PASSED road = coloured by VDV at that point
       - YOU dot rides the seam."""
    cv2.rectangle(frame, (MAP_X, MAP_Y),
                  (MAP_X+MAP_W, MAP_Y+MAP_H), DARK_GRAY, 2)
    text_outlined(frame, "ROUTE",
                  (MAP_X + MAP_W//2 - 32, MAP_Y + 22),
                  0.65, DARK_GRAY, thickness=1, outline=3)
    if len(route_pts) > 1:
        pts_xy = [(p[0]+MAP_X, p[1]+MAP_Y) for p in route_pts]
        future = pts_xy[cur_idx:]
        if len(future) > 1:
            fa = np.array(future, dtype=np.int32)
            cv2.polylines(frame, [fa], False, BLACK, 6, cv2.LINE_AA)
            cv2.polylines(frame, [fa], False, MAP_UNPASSED_GRAY,
                          3, cv2.LINE_AA)
        if cur_idx > 1:
            past = pts_xy[:cur_idx+1]
            pa = np.array(past, dtype=np.int32)
            cv2.polylines(frame, [pa], False, BLACK, 6, cv2.LINE_AA)
            for i in range(len(past) - 1):
                c = vdv_to_map_color(gpx_vdv[i])
                cv2.line(frame, past[i], past[i+1], c, 3, cv2.LINE_AA)
    if 0 <= cur_idx < len(route_pts):
        dx, dy = route_pts[cur_idx]
        cv2.circle(frame, (dx+MAP_X, dy+MAP_Y), 9, WHITE, 2, cv2.LINE_AA)
        cv2.circle(frame, (dx+MAP_X, dy+MAP_Y), 7, DOT_RED, -1, cv2.LINE_AA)
        text_outlined(frame, "YOU",
                      (dx+MAP_X+14, dy+MAP_Y+6),
                      0.55, WHITE, thickness=1, outline=3)


def vdv_to_y(v):
    v_clip = max(0.0, min(VDV_PLOT_MAX, v))
    avail = FOOTER_H - FOOTER_PAD_TOP - FOOTER_PAD_BOT
    return int(FOOTER_Y + FOOTER_H - FOOTER_PAD_BOT
               - (v_clip / VDV_PLOT_MAX) * avail)


def draw_dashed_hline(frame, x0, x1, y, color, dash=22, gap=14, thickness=2):
    x = x0
    while x < x1:
        x2 = min(x + dash, x1)
        cv2.line(frame, (x, y), (x2, y), color, thickness, cv2.LINE_AA)
        x = x2 + gap


def draw_footer(frame, t_now, t_vdv, vdv, cur_vdv):
    """QA-1..6: VDV trace (not RMS), dashed magenta threshold, ASCII
    units, larger fonts, full-band layout."""
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

    # QA-2: dashed magenta threshold line
    thresh_y = vdv_to_y(VDV_THRESH)
    draw_dashed_hline(frame, 0, CANVAS_W, thresh_y, MAGENTA,
                      dash=22, gap=14, thickness=2)
    # QA-3,4: ASCII VDV threshold label
    text_outlined(frame,
                  f"VDV LOW-risk = {VDV_THRESH:.1f} m/s^1.75",
                  (18, thresh_y - 9), 0.62, MAGENTA,
                  thickness=2, outline=3)

    # 30-min rolling window — VDV trace
    window_start = t_now - 30 * 60
    mask = (t_vdv >= window_start) & (t_vdv <= t_now)
    if mask.sum() > 1:
        ts_w = t_vdv[mask]; vdv_w = vdv[mask]
        xs = ((ts_w - window_start) / (30 * 60) * CANVAS_W).astype(np.int32)
        ys = np.array([vdv_to_y(v) for v in vdv_w], dtype=np.int32)
        pts = np.stack([xs, ys], axis=1)
        if len(pts) > 1:
            cv2.polylines(frame, [pts], False, TRACE_WHITE, 2, cv2.LINE_AA)

    # QA-5: larger tick labels
    for i, m in enumerate([30, 25, 20, 15, 10, 5, 0]):
        x = int(30 + (CANVAS_W - 60) * i / 6)
        label = "now" if m == 0 else f"-{m}m"
        text_outlined(frame, label,
                      (x - 22, FOOTER_Y + FOOTER_H - 5),
                      0.55, (230, 230, 230), thickness=1, outline=2)

    # Metric label + current readout
    text_outlined(frame, "VDV (ISO 2631-1, Wk)",
                  (CANVAS_W - 440, FOOTER_Y + 28),
                  0.72, (250, 250, 250), thickness=2, outline=3)
    text_outlined(frame,
                  f"now: {cur_vdv:4.1f} m/s^1.75",
                  (CANVAS_W - 440, FOOTER_Y + 60),
                  0.62, (250, 250, 250), thickness=1, outline=3)


# ── Main ----------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--art",   required=True, type=Path)
    ap.add_argument("--gpx",   required=True, type=Path)
    ap.add_argument("--title", default="Push")
    ap.add_argument("--video-art-offset", type=float, default=0.0,
                    help="art_time = video_time + offset (seconds). "
                         "Used when --offsets is NOT given (single offset).")
    ap.add_argument("--offsets", type=str, default=None,
                    help="Piecewise offsets for camera-stop/restart pushes. "
                         "Format: 'video_t1:offset1,video_t2:offset2,...' "
                         "where each pair applies starting at video_t. "
                         "Example: '0:37.93,2453:2527.23' = 2 segments split "
                         "at video t=2453s.")
    ap.add_argument("--out",   required=True, type=Path)
    args = ap.parse_args()

    # ── Build the offset lookup. If --offsets given, use piecewise;
    # otherwise the single --video-art-offset for the whole video.
    if args.offsets:
        segments = []
        for chunk in args.offsets.split(","):
            t_str, off_str = chunk.strip().split(":")
            segments.append((float(t_str), float(off_str)))
        segments.sort()
        print(f"piecewise offsets ({len(segments)} segments):")
        for t, o in segments:
            print(f"  starting video_t={t:8.2f}s : offset={o:+8.2f}s")
    else:
        segments = [(0.0, args.video_art_offset)]
        print(f"single offset: {args.video_art_offset:+.2f}s")

    def video_t_to_offset(t):
        """Pick the segment offset for video time t."""
        off = segments[0][1]
        for seg_t, seg_off in segments:
            if t >= seg_t:
                off = seg_off
            else:
                break
        return off

    def seam_distance(t):
        """Seconds to/from the nearest segment boundary (used for the
        'BREAK' overlay on the seam frames)."""
        if len(segments) < 2:
            return float("inf")
        boundaries = [s[0] for s in segments[1:]]
        return min(abs(t - b) for b in boundaries)

    # Load ART & VDV series (QA-1: VDV, not RMS)
    acc = load_art(args.art)
    fs = 1.0 / float(np.median(np.diff(acc["timestamp_ms"].to_numpy()))) * 1000.0
    print(f"ART: {len(acc):,} accel rows, fs ≈ {fs:.1f} Hz")
    t_vdv, vdv = vdv_series(acc, fs)
    print(f"VDV series: {len(t_vdv):,} samples  "
          f"min={vdv.min():.2f} max={vdv.max():.2f} "
          f"median={np.median(vdv):.2f}")

    # GPX, speeds, projection
    gpx = parse_gpx(args.gpx)
    if len(gpx) < 2:
        raise SystemExit("not enough GPX points")
    speeds = gpx_speeds_kmh(gpx)
    proj = project_gpx_to_box(gpx, MAP_W, MAP_H)
    route_pts = [proj(la, lo) for _, la, lo in gpx]
    # GPX time relative to its first point.
    gpx_t = gpx[:, 0] - gpx[0, 0]

    # Per-GPX-point VDV by fractional progress through the push (so the
    # minimap can colour each passed segment by the VDV at that point).
    # ART and GPX may have different start times; fractional progress is
    # the cleanest mapping without depending on an absolute sync.
    gpx_n = len(gpx)
    if len(vdv) > 0:
        gpx_vdv = np.array([
            float(vdv[min(len(vdv)-1,
                          int(i / max(1, gpx_n-1) * (len(vdv)-1)))])
            for i in range(gpx_n)
        ])
    else:
        gpx_vdv = np.zeros(gpx_n)

    # Open video
    cap = cv2.VideoCapture(str(args.video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    w0  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h0  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    nf  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"video: {w0}x{h0} {fps:.2f} fps, {nf} frames ({nf/max(fps,1):.1f}s)")

    # Pre-scale: if source is not 1920x1080, scale during the loop.
    scale = (w0, h0) != (CANVAS_W, CANVAS_H)

    # Writer — mp4v fallback (avc1 often fails on this box); we'll re-encode
    # the temp file with libx264 in the audio-mux step.
    #
    # IMPORTANT (2026-06-22 fix): write the temp into the OUTPUT dir, not
    # /tmp. /tmp is a small tmpfs on this machine and a 70-min 1080p mp4v
    # temp file (~14 GB) overflows it, corrupts the moov atom, and the
    # remux step fails with "moov atom not found".
    tmp_dir_for_writer = args.out.resolve().parent
    tmp_dir_for_writer.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".mp4", dir=str(tmp_dir_for_writer))
    import os as _os
    _os.close(fd)
    tmp_out = Path(tmp_path)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp_out), fourcc, fps, (CANVAS_W, CANVAS_H))

    # Speed HUD latch: refresh at 1 Hz (video time); blank on GPS dropout
    # so the number doesn't flicker between adjacent GPX samples or lie
    # when the GPS hasn't reported in seconds.
    SPEED_HUD_INTERVAL_S = 1.0
    GPS_STALE_GAP_S = 3.0
    last_speed_hud_t = -1e9
    speed_hud = None

    frame_i = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if scale:
            frame = cv2.resize(frame, (CANVAS_W, CANVAS_H),
                               interpolation=cv2.INTER_AREA)
        t_video = frame_i / fps
        # Piecewise (or single) offset lookup — handles
        # camera-stop/restart pushes per Vasil's "break" sync.
        t_art   = t_video + video_t_to_offset(t_video)

        # cur_idx (for minimap dot/passed-segment colouring) updates every
        # frame — smooth motion is desirable there.
        if 0 <= t_art <= gpx_t[-1]:
            cur_idx = int(np.searchsorted(gpx_t, t_art))
            cur_idx = max(0, min(gpx_n - 1, cur_idx))
        else:
            cur_idx = 0 if t_art < 0 else gpx_n - 1

        # Speed HUD: latched at 1 Hz of video time. Blank when the
        # nearest GPX sample is > GPS_STALE_GAP_S away (dropout).
        if t_video - last_speed_hud_t >= SPEED_HUD_INTERVAL_S:
            if 0 <= t_art <= gpx_t[-1]:
                gpx_gap = abs(gpx_t[cur_idx] - t_art)
                speed_hud = (None if gpx_gap > GPS_STALE_GAP_S
                             else float(np.interp(t_art, gpx_t, speeds)))
            else:
                speed_hud = None
            last_speed_hud_t = t_video

        # Current windowed VDV (QA-1) + derived ISO class (QA-7)
        cur_vdv = float(np.interp(t_art, t_vdv, vdv)) if len(t_vdv) else 0.0
        iso_class = vdv_to_iso_class(cur_vdv)

        # ── Draw layers ─────────────────────────────────────────────────
        draw_title_and_speed(frame, args.title, speed_hud, iso_class)
        draw_map(frame, proj, route_pts, cur_idx, gpx_vdv)
        draw_footer(frame, t_art, t_vdv, vdv, cur_vdv)

        # BREAK badge on the seam between piecewise segments
        # (camera-stop/restart) — within ±2 s of a boundary.
        if seam_distance(t_video) < 2.0:
            text_outlined(frame, "BREAK",
                          (CANVAS_W // 2 - 70, CANVAS_H // 2 - 100),
                          2.2, MAGENTA, thickness=5, outline=6)

        writer.write(frame)
        frame_i += 1
        if frame_i % 60 == 0:
            print(f"  frame {frame_i}/{nf}", flush=True)

    cap.release()
    writer.release()
    print(f"wrote temp video, re-encoding + muxing audio …")

    # Re-encode with x264 + mux original audio
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-i", str(tmp_out), "-i", str(args.video),
           "-map", "0:v", "-map", "1:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-c:a", "aac", "-shortest", str(args.out)]
    subprocess.run(cmd, check=True)
    tmp_out.unlink(missing_ok=True)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
