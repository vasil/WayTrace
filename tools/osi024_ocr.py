#!/usr/bin/env python3
"""
osi024_ocr.py — OCR plate crops per track, write sightings to plates.db,
emit an enriched detect.json with plate_text + daily/weekly streak per track.

Runs AFTER osi007_detect.py and BEFORE osi007_blur.py in the push batch.

Stage flag (env OSI024_STAGE, default "test"):
  test    — DB writes ON, enriched JSON written, but blur uses STANDARD label
            (handled by osi007_blur.py — this script just produces the data).
  staging — same OCR/DB; blur uses ENHANCED label for streak ≥ 7 days.
  final   — locked-in behaviour, same as staging.
  off     — skip OCR entirely; pass detect.json through unchanged.

GDPR HARD CONSTRAINT: plate text never leaves this machine. It is written
only to ~/waytrace-video/plates.db and to the local enriched JSON. It is
NOT in any rendered video — osi007_blur.py only reads streak counts.

Cluster identity (per OSI-024 SRS): chair pose, not car GPS.
  chair_lat / chair_lon = GPX nearest sample at ART_t = video_t + offset
  chair_heading_deg     = bearing(GPX[i-1] → GPX[i+1]) at that sample
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import osi024_plates_db as pdb  # noqa: E402

OCR_CONF_MIN = 0.55
PLATE_MIN_CHARS = 5
# Plate alphabet — keep alnum + a small set of separators; drop unicode junk.
ALLOWED = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")


def parse_gpx(path):
    """Return ndarray [(t_unix, lat, lon)] sorted by time."""
    import xml.etree.ElementTree as ET
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = ET.parse(path).getroot()
    out = []
    for trkpt in root.iter("{http://www.topografix.com/GPX/1/1}trkpt"):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        time_el = trkpt.find("g:time", ns)
        if time_el is None or time_el.text is None:
            continue
        t = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
        out.append((t.timestamp(), lat, lon))
    out.sort()
    return np.array(out, dtype=float)


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial compass bearing from point 1 to point 2 (degrees 0..360)."""
    phi1 = math.radians(lat1); phi2 = math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2) -
         math.sin(phi1) * math.cos(phi2) * math.cos(dlam))
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def chair_pose_at(gpx, t_unix):
    """Return (lat, lon, heading_deg) at t_unix via nearest GPX sample, with
    heading from neighbouring samples. gpx is the ndarray from parse_gpx."""
    if len(gpx) < 2:
        return None
    times = gpx[:, 0]
    i = int(np.searchsorted(times, t_unix))
    i = max(1, min(len(gpx) - 2, i))
    lat = float(gpx[i, 1]); lon = float(gpx[i, 2])
    head = bearing_deg(float(gpx[i - 1, 1]), float(gpx[i - 1, 2]),
                       float(gpx[i + 1, 1]), float(gpx[i + 1, 2]))
    return lat, lon, head


def normalize_plate(text):
    s = "".join(c for c in text.upper() if c in ALLOWED)
    return s


def parse_offset_spec(s):
    """Parse '--offset' arg into a piecewise list [(t_start, offset), ...]
    sorted by t_start. A single float yields [(0.0, float(s))]."""
    s = s.strip()
    if "," not in s and ":" not in s:
        return [(0.0, float(s))]
    out = []
    for tok in s.split(","):
        t_str, o_str = tok.split(":")
        out.append((float(t_str), float(o_str)))
    out.sort()
    if out[0][0] != 0.0:
        # extend the earliest segment back to 0
        out = [(0.0, out[0][1])] + out
    return out


def offset_at(pieces, v_t):
    """Pick the offset (float) effective at video time v_t."""
    chosen = pieces[0][1]
    for t_start, o in pieces:
        if v_t >= t_start:
            chosen = o
        else:
            break
    return chosen


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video",      required=True)
    ap.add_argument("--detect-in",  required=True)
    ap.add_argument("--detect-out", required=True)
    ap.add_argument("--art",        required=True,
                    help="ART CSV (used only for ART start time anchor)")
    ap.add_argument("--gpx",        required=True)
    ap.add_argument("--offset",     required=True, type=str,
                    help="ART_t = video_t + offset (seconds). "
                         "Either a single float (e.g. -59.69) or a piecewise "
                         "spec 't0:o0,t1:o1,...' where ti is the video time "
                         "in seconds at which offset oi takes effect.")
    ap.add_argument("--push-ts",    required=True,
                    help="e.g. 202606280835 (filename timestamp)")
    ap.add_argument("--stage",      default=os.environ.get("OSI024_STAGE", "test"),
                    choices=["test", "staging", "final", "off"])
    ap.add_argument("--db",         default=pdb.DEFAULT_DB)
    ap.add_argument("--max-ocr-per-track", type=int, default=12,
                    help="cap OCR calls per track for speed")
    args = ap.parse_args()

    detect_path = Path(args.detect_in)
    detect = json.loads(detect_path.read_text())

    # OSI024_STAGE=off — bypass: copy through unchanged
    if args.stage == "off":
        Path(args.detect_out).write_text(json.dumps(detect))
        print("OSI024_STAGE=off — passed through unchanged", flush=True)
        return

    # ── parse push date from push_ts (YYYYMMDDHHMM)
    pt = args.push_ts
    if len(pt) < 8 or not pt.isdigit():
        sys.exit(f"bad --push-ts: {pt}")
    push_date = date(int(pt[:4]), int(pt[4:6]), int(pt[6:8])).isoformat()

    # ── parse offset (single float or piecewise spec)
    offset_pieces = parse_offset_spec(args.offset)
    print(f"offset: {offset_pieces}", flush=True)

    # ── load GPX
    gpx = parse_gpx(args.gpx)
    print(f"gpx: {len(gpx)} samples", flush=True)
    if len(gpx) < 2:
        sys.exit("GPX has < 2 points — cannot derive chair pose")
    art_t0_unix = float(gpx[0, 0])  # anchor: ART seconds 0 ≈ first GPX time

    # ── open video
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"cannot open {args.video}")
    fps = float(detect["fps"])
    width = int(detect["width"]); height = int(detect["height"])

    # ── load EasyOCR (lazy)
    print("loading EasyOCR (GPU if available)...", flush=True)
    import easyocr
    reader = easyocr.Reader(["en"], gpu=True, verbose=False)
    print("EasyOCR ready.", flush=True)

    # ── connect DB
    con = pdb.init_db(args.db)
    print(f"db: {args.db}  stage={args.stage}", flush=True)

    tracks = detect["tracks"]
    n_tracks = 0
    n_ocr_calls = 0
    n_sightings_new = 0
    n_with_plate = 0
    t0 = time.time()

    for tid, track in tracks.items():
        cls = track["cls"]
        if cls not in {"car", "truck", "bus", "motorcycle"}:
            continue
        # frames with a real plate detection
        plate_frames = [(track["frames"][i], track["plates"][i],
                         track["bboxes"][i], track["confs"][i])
                        for i in range(len(track["frames"]))
                        if track["plates"][i] is not None]
        if not plate_frames:
            continue
        n_tracks += 1

        # subsample to at most max_ocr_per_track frames spread across the
        # detection window
        if len(plate_frames) > args.max_ocr_per_track:
            idxs = np.linspace(0, len(plate_frames) - 1,
                               args.max_ocr_per_track).astype(int)
            sample = [plate_frames[i] for i in idxs]
        else:
            sample = plate_frames

        votes = Counter()
        best_conf = 0.0
        best_text = None
        best_bbox = None
        best_yolo_conf = 0.0
        best_frame_idx = None

        for (f_idx, plate_bbox, veh_bbox, yolo_conf) in sample:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ok, frame = cap.read()
            if not ok:
                continue
            x1, y1, x2, y2 = plate_bbox
            x1 = max(0, x1); y1 = max(0, y1)
            x2 = min(width, x2); y2 = min(height, y2)
            if x2 - x1 < 8 or y2 - y1 < 4:
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            try:
                results = reader.readtext(crop, detail=1, paragraph=False)
            except Exception as e:
                print(f"  ocr error tid={tid} f={f_idx}: {e}", flush=True)
                continue
            n_ocr_calls += 1
            for _, txt, conf in results:
                norm = normalize_plate(txt)
                if len(norm) < PLATE_MIN_CHARS or conf < OCR_CONF_MIN:
                    continue
                votes[norm] += 1
                if conf > best_conf:
                    best_conf = float(conf)
                    best_text = norm
                    best_bbox = [x1, y1, x2, y2]
                    best_yolo_conf = float(yolo_conf)
                    best_frame_idx = int(f_idx)

        if not votes or best_text is None:
            continue

        # majority vote: pick the most common normalized plate text;
        # tie-breaker = best_text by conf.
        winner = votes.most_common(1)[0][0]
        if votes[winner] < 2 and len(sample) >= 3:
            # require ≥ 2 agreeing samples when we have ≥ 3 chances
            continue

        # ── chair pose at the representative frame
        # video time of best frame
        v_t = best_frame_idx / fps
        art_t = v_t + offset_at(offset_pieces, v_t)
        unix_t = art_t0_unix + art_t
        pose = chair_pose_at(gpx, unix_t)
        if pose is None:
            continue
        lat, lon, heading = pose

        # ── hash plate text BEFORE anything touches disk (GDPR)
        winner_hash = pdb.plate_hash(winner)
        del winner   # don't accidentally serialize the readable text

        # ── write sighting (idempotent on plate_hash)
        new = pdb.upsert_sighting(con, winner_hash, args.push_ts, push_date,
                                  lat, lon, heading,
                                  best_conf, best_yolo_conf, best_bbox,
                                  prehashed=True)
        if new:
            n_sightings_new += 1
        n_with_plate += 1

        cluster = pdb.cluster_bins(lat, lon, heading)
        daily = pdb.get_daily_streak(con, winner_hash, cluster, push_date,
                                     prehashed=True)
        weekly = pdb.get_weekly_streak(con, winner_hash, cluster, push_date,
                                       prehashed=True)

        # ── attach to track in enriched JSON. We store the HASH, never
        # the readable text. Downstream (osi007_blur.py) reads streak
        # counts; it never needs to know the plate.
        track["plate_hash"] = winner_hash
        track["ocr_conf"] = best_conf
        track["daily_streak"] = daily
        track["weekly_streak"] = weekly
        track["chair_lat_bin"] = cluster[0]
        track["chair_lon_bin"] = cluster[1]
        track["chair_heading_bin"] = cluster[2]

    cap.release()

    # ── write enriched detect.json
    # Per GDPR convention: the on-disk JSON CAN contain plate_text (it
    # stays on VT-X1). osi007_blur.py is required to NEVER render it.
    Path(args.detect_out).write_text(json.dumps(detect))

    el = time.time() - t0
    print(f"osi024: stage={args.stage}  tracks_with_plates={n_tracks}  "
          f"ocr_calls={n_ocr_calls}  matched_plates={n_with_plate}  "
          f"new_sightings={n_sightings_new}  elapsed={el:.0f}s",
          flush=True)
    print(f"out: {args.detect_out}", flush=True)


if __name__ == "__main__":
    main()
