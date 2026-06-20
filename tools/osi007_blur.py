#!/usr/bin/env python3
"""
osi007_blur.py  IN.mp4  IN_DETECT.json  OUT.mp4

OSI-021 step 4: temporal-persistent GDPR blur driven by the per-track
detection sidecar from osi007_detect.py.

The point of this script is to GUARANTEE no-blinking blur. The previous
single-pass pipeline (osi007_final.py) ran plate/face detection per frame
and blurred whatever it found that frame. If the detector missed a frame,
the plate became readable for that frame — a GDPR leak.

The new rule (per SRS OSI-021 HARD REQUIREMENT, 2026-06-19):
  • blur is bound to the TRACKED OBJECT, not the frame
  • once a plate is detected anywhere on a vehicle track, the plate region
    is blurred in EVERY frame of that track — including detection misses,
    by interpolating from neighbouring detections; relative to the moving
    vehicle bbox so the blur tracks the car
  • PAD ±0.5 s of frames around the detection range (onset/loss invisible)
  • if a vehicle is tracked but a plate is NEVER detected, fall back to the
    SAFETY BACKSTOP — blur the lower 40 % of the vehicle bbox for the
    whole track
  • same logic for faces on person tracks; person tracks with no detected
    face get the upper-head-region backstop (over-blur is harmless, the
    SRS axiom is "every person has a face on the head")

Then on top of the blurred frames it draws the OSI-007 colored YOLO boxes
(SRS color language, locked):
   RED   any vehicle
   GREEN person, bicycle
   BLUE  small fixed obstacle (fire hydrant, bench, parking meter, stop sign)
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

# ── blur params (same as osi007_final.py) ─────────────────────────────────
PLATE_KERNEL = (61, 61); PLATE_SIGMA = 30
FACE_KERNEL  = (51, 51); FACE_SIGMA  = 25

# Temporal padding: blur N frames BEFORE first detection and AFTER last
# detection on the same track. SRS says ~0.5 s; we round on fps.
PAD_SECONDS = 0.5

# Safety backstop fractions of the vehicle / person bbox.
PLATE_BACKSTOP_FRAC_TOP    = 0.60   # blur from 60% down to bottom of box
PLATE_BACKSTOP_FRAC_LEFT   = 0.10   # 10% inside left edge
PLATE_BACKSTOP_FRAC_RIGHT  = 0.10   # 10% inside right edge

FACE_BACKSTOP_FRAC_BOTTOM  = 0.25   # blur top 25% of person box
FACE_BACKSTOP_FRAC_INSET   = 0.15   # inset left/right by 15%

# Locked SRS color language (BGR — cv2 native)
RED   = (0,   0,   204)   # #CC0000  any vehicle
GREEN = (0,   170, 0)     # #00AA00  person, bicycle
BLUE  = (204, 85,  0)     # #0055CC  small fixed obstacle

VEHICLE_CLASSES  = {"car", "truck", "bus", "motorcycle"}
CYCLIST_CLASSES  = {"person", "bicycle"}
OBSTACLE_CLASSES = {"fire hydrant", "bench", "parking meter", "stop sign"}
CLASS_COLOR = {
    **{c: RED   for c in VEHICLE_CLASSES},
    **{c: GREEN for c in CYCLIST_CLASSES},
    **{c: BLUE  for c in OBSTACLE_CLASSES},
}


# ── geometry helpers ──────────────────────────────────────────────────────

def relative(plate_box, veh_box):
    """Express plate_box as (rx1, ry1, rx2, ry2) in [0,1] relative to
    veh_box. Used so blur regions can be projected onto a different vehicle
    bbox at another frame."""
    vx1, vy1, vx2, vy2 = veh_box
    vw = max(1, vx2 - vx1); vh = max(1, vy2 - vy1)
    px1, py1, px2, py2 = plate_box
    return ((px1 - vx1) / vw, (py1 - vy1) / vh,
            (px2 - vx1) / vw, (py2 - vy1) / vh)


def project_relative(rel, veh_box, width, height):
    """Inverse of relative(): plate_box at the current vehicle bbox."""
    vx1, vy1, vx2, vy2 = veh_box
    vw = vx2 - vx1; vh = vy2 - vy1
    rx1, ry1, rx2, ry2 = rel
    return (max(0, int(vx1 + rx1 * vw)),
            max(0, int(vy1 + ry1 * vh)),
            min(width, int(vx1 + rx2 * vw)),
            min(height, int(vy1 + ry2 * vh)))


def lerp_rel(rel_a, rel_b, t):
    """Linear interpolation between two relative boxes, t in [0,1]."""
    return tuple(a + (b - a) * t for a, b in zip(rel_a, rel_b))


def plate_backstop_box(veh_box, width, height):
    """Lower portion of the vehicle box, inset from left/right edges."""
    vx1, vy1, vx2, vy2 = veh_box
    vw = vx2 - vx1; vh = vy2 - vy1
    return (max(0, int(vx1 + vw * PLATE_BACKSTOP_FRAC_LEFT)),
            max(0, int(vy1 + vh * PLATE_BACKSTOP_FRAC_TOP)),
            min(width, int(vx2 - vw * PLATE_BACKSTOP_FRAC_RIGHT)),
            min(height, vy2))


def face_backstop_box(person_box, width, height):
    """Upper portion of the person box (head region)."""
    px1, py1, px2, py2 = person_box
    pw = px2 - px1; ph = py2 - py1
    return (max(0, int(px1 + pw * FACE_BACKSTOP_FRAC_INSET)),
            max(0, py1),
            min(width, int(px2 - pw * FACE_BACKSTOP_FRAC_INSET)),
            min(height, int(py1 + ph * FACE_BACKSTOP_FRAC_BOTTOM)))


# ── per-track blur plan ───────────────────────────────────────────────────

def plan_track_blurs(track, target_kind, kernel_name, pad_frames,
                     width, height):
    """For a single track, compute the per-frame blur regions of one kind
    (plates or faces). Returns dict {frame_idx: (x1,y1,x2,y2)}.

    Args:
      track: the per-track dict from _detect.json — has frames[], bboxes[],
             plates[] or faces[] (parallel arrays).
      target_kind: "plates" or "faces" (key into track).
      kernel_name: only used for diagnostic stats.
      pad_frames: int — extra frames of blur before first and after last
                  detection.

    Behaviour:
      • Build anchors = list of (idx, frame, veh_box, rel_target)
        where idx is the position in the track's parallel arrays.
      • If anchors is non-empty: for each frame in
          [first_anchor.frame - pad, last_anchor.frame + pad]
        that the track is actually on screen, compute a target box by
        interpolating relative position between surrounding anchors
        (with extrapolation at the ends), then project onto the
        vehicle/person bbox at that frame.
      • If anchors is empty AND class is a vehicle (for plates) or a person
        (for faces): safety backstop — blur for every frame the track is
        on screen. Return that.
      • Otherwise (bicycle/obstacle without anchor, etc.): no blur.
    """
    frames = track["frames"]
    bboxes = track["bboxes"]
    targets = track[target_kind]
    cls = track["cls"]

    anchors = []
    for i, t in enumerate(targets):
        if t is not None:
            rel = relative(t, bboxes[i])
            anchors.append((i, frames[i], bboxes[i], rel))

    plan = {}

    if anchors:
        # blur for the padded range, but clamp to the track lifespan
        first_anchor_frame = anchors[0][1]
        last_anchor_frame  = anchors[-1][1]
        track_first = frames[0]
        track_last  = frames[-1]
        blur_start = max(track_first, first_anchor_frame - pad_frames)
        blur_end   = min(track_last,  last_anchor_frame  + pad_frames)

        # build frame_idx → array_idx
        frame_to_idx = {f: i for i, f in enumerate(frames)}

        # anchor frame → (rel, anchor_idx_into_anchors)
        anchor_frames = [a[1] for a in anchors]

        for f in range(blur_start, blur_end + 1):
            if f not in frame_to_idx:
                continue   # the track is not on screen this frame
            idx = frame_to_idx[f]
            veh = bboxes[idx]

            # find surrounding anchors
            # left_anchor = greatest anchor with frame ≤ f
            # right_anchor = smallest anchor with frame ≥ f
            left_a = None
            right_a = None
            for af in anchor_frames:
                if af <= f:
                    left_a = af
                if af >= f and right_a is None:
                    right_a = af
            if left_a is not None and right_a is not None and left_a != right_a:
                la = next(a for a in anchors if a[1] == left_a)
                ra = next(a for a in anchors if a[1] == right_a)
                t = (f - left_a) / (right_a - left_a)
                rel = lerp_rel(la[3], ra[3], t)
            elif left_a is not None:
                la = next(a for a in anchors if a[1] == left_a)
                rel = la[3]
            elif right_a is not None:
                ra = next(a for a in anchors if a[1] == right_a)
                rel = ra[3]
            else:
                continue
            plan[f] = project_relative(rel, veh, width, height)
        return plan, "detected", len(anchors)

    # ── no anchors: safety backstop for vehicles / persons
    if target_kind == "plates" and cls in VEHICLE_CLASSES:
        for f, veh in zip(frames, bboxes):
            plan[f] = plate_backstop_box(veh, width, height)
        return plan, "backstop", 0
    if target_kind == "faces" and cls == "person":
        for f, veh in zip(frames, bboxes):
            plan[f] = face_backstop_box(veh, width, height)
        return plan, "backstop", 0

    return plan, "none", 0


# ── main ──────────────────────────────────────────────────────────────────

def main(in_mp4, in_json, out_mp4):
    detect = json.loads(Path(in_json).read_text())
    fps    = float(detect["fps"])
    width  = int(detect["width"])
    height = int(detect["height"])
    total  = int(detect["total_frames"])
    tracks = detect["tracks"]
    pad_frames = max(1, round(PAD_SECONDS * fps))

    print(f"in : {in_mp4}  {width}x{height}  {fps:.2f} fps  {total} frames",
          flush=True)
    print(f"detect: {len(tracks)} tracks  PAD={pad_frames} frames", flush=True)

    # frame_idx → list of (x1,y1,x2,y2, kind)  where kind in
    # {"plate", "plate_backstop", "face", "face_backstop"}
    plate_plan = defaultdict(list)
    face_plan  = defaultdict(list)

    # per-frame YOLO boxes to draw (RED / GREEN / BLUE color from class)
    box_plan = defaultdict(list)    # frame -> [(bbox, color, class_label, conf)]

    track_summary = {
        "vehicles": 0, "persons": 0, "cyclists": 0, "small_obstacles": 0,
        "plates_with_real_detection": 0, "plates_backstop_only": 0,
        "faces_with_real_detection":  0, "faces_backstop_only":  0,
    }

    for tid, track in tracks.items():
        cls = track["cls"]
        if cls in VEHICLE_CLASSES:
            track_summary["vehicles"] += 1
        if cls == "person":
            track_summary["persons"] += 1
        if cls == "bicycle":
            track_summary["cyclists"] += 1
        if cls in OBSTACLE_CLASSES:
            track_summary["small_obstacles"] += 1

        # plate plan
        plan, mode, n_anchors = plan_track_blurs(
            track, "plates", "plate", pad_frames, width, height)
        for f, box in plan.items():
            plate_plan[f].append(box)
        if cls in VEHICLE_CLASSES:
            if mode == "detected":
                track_summary["plates_with_real_detection"] += 1
            elif mode == "backstop":
                track_summary["plates_backstop_only"] += 1

        # face plan
        plan, mode, n_anchors = plan_track_blurs(
            track, "faces", "face", pad_frames, width, height)
        for f, box in plan.items():
            face_plan[f].append(box)
        if cls == "person":
            if mode == "detected":
                track_summary["faces_with_real_detection"] += 1
            elif mode == "backstop":
                track_summary["faces_backstop_only"] += 1

        # box overlay plan
        color = CLASS_COLOR.get(cls)
        if color is not None:
            label = cls
            for f, bb, conf in zip(track["frames"], track["bboxes"],
                                   track["confs"]):
                box_plan[f].append((bb, color, label, conf))

    print(f"plan built: plates={sum(len(v) for v in plate_plan.values())} "
          f"regions  faces={sum(len(v) for v in face_plan.values())} regions  "
          f"boxes={sum(len(v) for v in box_plan.values())}",
          flush=True)

    # ── pass 2: read input video frame-by-frame, apply blurs + boxes
    cap = cv2.VideoCapture(in_mp4)
    if not cap.isOpened():
        sys.exit(f"cannot open {in_mp4}")
    tmp_video = f"{out_mp4}.video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"avc1")
    wri = cv2.VideoWriter(tmp_video, fourcc, fps, (width, height))
    if not wri.isOpened():
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        wri = cv2.VideoWriter(tmp_video, fourcc, fps, (width, height))
    if not wri.isOpened():
        sys.exit("VideoWriter failed to open")

    counts = {
        "frames":            0,
        "plate_blurs":       0,
        "face_blurs":        0,
        "boxes_drawn":       0,
    }
    counts.update(track_summary)

    t0 = time.time()
    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        # plate blurs
        for (x1, y1, x2, y2) in plate_plan.get(frame_idx, []):
            roi = frame[y1:y2, x1:x2]
            if roi.size:
                frame[y1:y2, x1:x2] = cv2.GaussianBlur(
                    roi, PLATE_KERNEL, PLATE_SIGMA)
                counts["plate_blurs"] += 1

        # face blurs
        for (x1, y1, x2, y2) in face_plan.get(frame_idx, []):
            roi = frame[y1:y2, x1:x2]
            if roi.size:
                frame[y1:y2, x1:x2] = cv2.GaussianBlur(
                    roi, FACE_KERNEL, FACE_SIGMA)
                counts["face_blurs"] += 1

        # colored boxes on top
        for (bb, color, label, conf) in box_plan.get(frame_idx, []):
            x1, y1, x2, y2 = bb
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            txt = f"{label} {conf:.2f}"
            (tw, th_), _ = cv2.getTextSize(
                txt, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(frame, (x1, y1 - th_ - 6),
                          (x1 + tw + 4, y1), color, -1)
            cv2.putText(frame, txt, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (255, 255, 255), 2)
            counts["boxes_drawn"] += 1

        wri.write(frame)
        frame_idx += 1; counts["frames"] = frame_idx

        if frame_idx % 60 == 0:
            el = time.time() - t0
            rate = frame_idx / el if el > 0 else 0
            eta  = (total - frame_idx) / rate if rate > 0 else 0
            print(f"frame {frame_idx}/{total}  {rate:.1f} fps  "
                  f"ETA {eta:5.0f}s  plate_blurs={counts['plate_blurs']} "
                  f"face_blurs={counts['face_blurs']}",
                  flush=True)

    cap.release(); wri.release()

    # mux audio back in from the source
    print("remuxing audio + faststart…", flush=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", tmp_video, "-i", in_mp4,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a?",
        "-movflags", "+faststart",
        out_mp4,
    ], check=True)
    os.remove(tmp_video)

    sidecar = os.path.splitext(out_mp4)[0] + ".json"
    with open(sidecar, "w") as f:
        json.dump(counts, f, indent=2)

    el = time.time() - t0
    print(f"done: {frame_idx} frames in {el:.0f}s ({frame_idx/el:.1f} fps)",
          flush=True)
    print(f"out: {out_mp4}  ({os.path.getsize(out_mp4)//1024//1024} MB)",
          flush=True)
    print(f"counts: {sidecar}\n  {json.dumps(counts, indent=2)}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input",  help="input 1080p mp4")
    ap.add_argument("detect", help="input _detect.json from osi007_detect.py")
    ap.add_argument("output", help="output consolidated mp4 (blurred + boxed)")
    args = ap.parse_args()
    main(args.input, args.detect, args.output)
