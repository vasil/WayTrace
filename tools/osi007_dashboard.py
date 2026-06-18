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
TITLE_POS    = (70, 60)        # baseline of title
SPEED_POS    = (70, 145)       # baseline of speed
MAP_X, MAP_Y, MAP_W, MAP_H = 1430, 30, 460, 300
FOOTER_Y, FOOTER_H = 980, 100
GRAVITY = 9.81

# ── BGR colours (cv2 native) ────────────────────────────────────────────
YELLOW   = (51, 204, 255)
CYAN     = (221, 204, 0)
WHITE    = (255, 255, 255)
GREEN    = (51, 170, 51)
RED      = (51, 51, 255)
BLACK    = (0, 0, 0)
AMBER_BG = (0, 85, 107)
GREEN_BG = (0, 51, 0)
RED_BG   = (0, 0, 90)
FOOTER_BORDER = (51, 0, 204)


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


# ── Drawing helpers ------------------------------------------------------
def text_outlined(img, text, org, scale, color, thickness=2,
                  outline=4, font=cv2.FONT_HERSHEY_DUPLEX):
    cv2.putText(img, text, org, font, scale, BLACK,
                thickness + outline, cv2.LINE_AA)
    cv2.putText(img, text, org, font, scale, color,
                thickness, cv2.LINE_AA)


def draw_title_and_speed(frame, title, speed_kmh):
    text_outlined(frame, title, TITLE_POS, 1.1, YELLOW, thickness=2, outline=4)
    text_outlined(frame, f"{speed_kmh:5.1f} km/h",
                  SPEED_POS, 2.1, WHITE, thickness=4, outline=5)


def draw_map(frame, proj, route_pts, cur_lat, cur_lon):
    # frame
    cv2.rectangle(frame, (MAP_X, MAP_Y),
                  (MAP_X+MAP_W, MAP_Y+MAP_H), GREEN, 2)
    text_outlined(frame, "ROUTE",
                  (MAP_X + MAP_W//2 - 38, MAP_Y + 22),
                  0.7, GREEN, thickness=1, outline=3)
    # polyline (offset into map block)
    if len(route_pts) > 1:
        pts = np.array([[p[0]+MAP_X, p[1]+MAP_Y] for p in route_pts],
                       dtype=np.int32)
        cv2.polylines(frame, [pts], False, BLACK,  7, cv2.LINE_AA)  # halo
        cv2.polylines(frame, [pts], False, YELLOW, 4, cv2.LINE_AA)  # route
    # dot
    dx, dy = proj(cur_lat, cur_lon)
    cv2.circle(frame, (dx+MAP_X, dy+MAP_Y), 9, WHITE, 2, cv2.LINE_AA)
    cv2.circle(frame, (dx+MAP_X, dy+MAP_Y), 7, RED, -1, cv2.LINE_AA)
    text_outlined(frame, "YOU",
                  (dx+MAP_X+14, dy+MAP_Y+6),
                  0.55, WHITE, thickness=1, outline=3)


def rms_to_y(v):
    # axis: 0 at bottom of footer, 5 m/s² at the top — clip above
    v_clip = max(0.0, min(5.0, v))
    return int(FOOTER_Y + FOOTER_H - (v_clip / 5.0) * (FOOTER_H - 30))


def draw_footer(frame, t_now, t_rms, rms, cur_rms):
    # Background by current roughness — semi-transparent fill
    if cur_rms < 1.0:
        bg = GREEN_BG
    elif cur_rms < 5.0:
        bg = AMBER_BG
    else:
        bg = RED_BG
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, FOOTER_Y),
                  (CANVAS_W, FOOTER_Y+FOOTER_H), bg, -1)
    cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)
    cv2.rectangle(frame, (0, FOOTER_Y),
                  (CANVAS_W, FOOTER_Y+FOOTER_H), FOOTER_BORDER, 1)

    # Threshold line — ISO 2631-1 RMS_uncomfortable = 1.15 m/s²
    thresh_y = rms_to_y(1.15)
    cv2.line(frame, (0, thresh_y), (CANVAS_W, thresh_y), WHITE, 1, cv2.LINE_AA)
    text_outlined(frame,
                  "ISO 2631-1 uncomfortable threshold (1.15 m/s²)",
                  (15, thresh_y - 8), 0.42, WHITE, thickness=1, outline=2)

    # 30-min rolling window: -30m to now
    window_start = t_now - 30 * 60
    mask = (t_rms >= window_start) & (t_rms <= t_now)
    if mask.sum() > 1:
        ts_w = t_rms[mask]
        rms_w = rms[mask]
        xs = ((ts_w - window_start) / (30 * 60) * CANVAS_W).astype(np.int32)
        ys = np.array([rms_to_y(v) for v in rms_w], dtype=np.int32)
        pts = np.stack([xs, ys], axis=1)
        if len(pts) > 1:
            cv2.polylines(frame, [pts], False, WHITE, 2, cv2.LINE_AA)

    # Tick labels
    for i, m in enumerate([30, 25, 20, 15, 10, 5, 0]):
        x = int(30 + (CANVAS_W - 60) * i / 6)
        label = "now" if m == 0 else f"-{m}m"
        text_outlined(frame, label,
                      (x - 18, FOOTER_Y + FOOTER_H - 8),
                      0.40, (220, 220, 220), thickness=1, outline=2)

    # Metric label
    text_outlined(frame, "metric: windowed RMS (default)",
                  (CANVAS_W - 360, FOOTER_Y + 22),
                  0.45, (220, 220, 220), thickness=1, outline=2)


# ── Main ----------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--art",   required=True, type=Path)
    ap.add_argument("--gpx",   required=True, type=Path)
    ap.add_argument("--title", default="Push")
    ap.add_argument("--video-art-offset", type=float, default=0.0,
                    help="art_time = video_time + offset (seconds).")
    ap.add_argument("--out",   required=True, type=Path)
    args = ap.parse_args()

    # Load ART & RMS series
    acc = load_art(args.art)
    fs = 1.0 / float(np.median(np.diff(acc["timestamp_ms"].to_numpy()))) * 1000.0
    print(f"ART: {len(acc):,} accel rows, fs ≈ {fs:.1f} Hz")
    t_rms, rms = windowed_rms_series(acc, fs)
    print(f"RMS series: {len(t_rms):,} samples")

    # GPX, speeds, projection
    gpx = parse_gpx(args.gpx)
    if len(gpx) < 2:
        raise SystemExit("not enough GPX points")
    speeds = gpx_speeds_kmh(gpx)
    proj = project_gpx_to_box(gpx, MAP_W, MAP_H)
    route_pts = [proj(la, lo) for _, la, lo in gpx]
    # GPX time relative to its first point (matches the ART t=0 frame).
    gpx_t = gpx[:, 0] - gpx[0, 0]

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
    tmp_out = Path(tempfile.mkstemp(suffix=".mp4")[1])
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp_out), fourcc, fps, (CANVAS_W, CANVAS_H))

    frame_i = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if scale:
            frame = cv2.resize(frame, (CANVAS_W, CANVAS_H),
                               interpolation=cv2.INTER_AREA)
        t_video = frame_i / fps
        t_art   = t_video + args.video_art_offset

        # Speed via interpolation on GPX
        if 0 <= t_art <= gpx_t[-1]:
            speed = float(np.interp(t_art, gpx_t, speeds))
            cur_lat = float(np.interp(t_art, gpx_t, gpx[:, 1]))
            cur_lon = float(np.interp(t_art, gpx_t, gpx[:, 2]))
        else:
            speed = 0.0
            cur_lat = gpx[0, 1]; cur_lon = gpx[0, 2]

        # Current windowed RMS
        cur_rms = float(np.interp(t_art, t_rms, rms))

        # ── Draw layers ─────────────────────────────────────────────────
        draw_title_and_speed(frame, args.title, speed)
        draw_map(frame, proj, route_pts, cur_lat, cur_lon)
        draw_footer(frame, t_art, t_rms, rms, cur_rms)

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
