#!/usr/bin/env python3
"""
osi007_detect.py  IN.mp4  OUT.json

OSI-021 step 3 (new per-push pipeline): YOLO detection + STABLE TRACKING +
focused plate/face detection inside vehicle/person crops.

Outputs a JSON sidecar that osi007_blur.py consumes for temporal-persistent
blur (no blinking). The point of splitting from osi007_final.py is that the
blur decisions need the WHOLE-video track context — what frames each tracked
object exists in, where the plate was ever detected on it — before any
blur is committed.

Pipeline:
  • obj   = yolov8n.pt  → tracked with BotSort. Classes kept:
        vehicles  (car/truck/bus/motorcycle)  → also runs plate detector
        cyclists  (person/bicycle)            → person also runs face detector
        small obstacles (fire hydrant / bench / parking meter / stop sign)
  • plate = YOLOv11 fine-tune, applied INSIDE each vehicle box at
            conf=PLATE_CONF (lower than the previous full-frame pass).
  • face  = YOLOv8-face, applied INSIDE each person box at conf=FACE_CONF.

The detector results are emitted PER TRACK (not per frame) so the consumer
can reason about "this car's plate was seen at frames 142-198, blur the
whole lifespan 130-210 with interpolation".
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import cv2
from ultralytics import YOLO

# ── model paths
PLATE_MODEL = "/home/vasil/waytrace-video/models/plate_yolov11.pt"
FACE_MODEL  = "/home/vasil/waytrace-video/models/yolov8n-face.pt"
OBJ_MODEL   = "yolov8n.pt"

# ── confidence thresholds
PLATE_CONF  = 0.10   # lower than the full-frame 0.15 from osi007_final.py
FACE_CONF   = 0.20   # lower than the full-frame 0.30 from osi007_final.py
OBJ_CONF    = 0.35   # same as full-frame

# Akaso dashcam burns a date/time in the upper-left ~25% × 8% area; the
# plate detector loves that text. Skip plate detections whose CENTER lands
# in that zone (per the same heuristic used in osi007_final.py).
NO_BLUR_W = 0.25
NO_BLUR_H = 0.08

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle"}
CYCLIST_CLASSES = {"person", "bicycle"}
OBSTACLE_CLASSES = {"fire hydrant", "bench", "parking meter", "stop sign"}

KEEP_CLASSES = VEHICLE_CLASSES | CYCLIST_CLASSES | OBSTACLE_CLASSES


def run_in_crop(model, frame, x1, y1, x2, y2, conf, frame_w, frame_h,
                pad=20):
    """Run a detector inside a padded crop of `frame`. Return list of
    detected boxes in FULL-FRAME coords [(x1,y1,x2,y2,conf), ...].
    Returns [] if the crop is degenerate."""
    cx1 = max(0, x1 - pad); cy1 = max(0, y1 - pad)
    cx2 = min(frame_w, x2 + pad); cy2 = min(frame_h, y2 + pad)
    if cx2 - cx1 < 16 or cy2 - cy1 < 16:
        return []
    crop = frame[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return []
    r = model(crop, verbose=False, device=0, conf=conf)[0]
    if r.boxes is None or len(r.boxes) == 0:
        return []
    xy = r.boxes.xyxy.cpu().numpy()
    cf = r.boxes.conf.cpu().numpy()
    out = []
    for (bx1, by1, bx2, by2), p in zip(xy, cf):
        out.append((int(bx1 + cx1), int(by1 + cy1),
                    int(bx2 + cx1), int(by2 + cy1), float(p)))
    return out


def pick_best(boxes):
    """Pick the highest-conf detection from a list of (x1,y1,x2,y2,conf).
    Returns the bbox or None."""
    if not boxes:
        return None
    boxes.sort(key=lambda b: -b[4])
    x1, y1, x2, y2, _ = boxes[0]
    return [x1, y1, x2, y2]


def main(in_mp4, out_json):
    obj = YOLO(OBJ_MODEL)
    plate = YOLO(PLATE_MODEL)
    face = YOLO(FACE_MODEL)

    obj_names = obj.names
    obj_keep_ids = {i for i, n in obj_names.items() if n in KEEP_CLASSES}
    print(f"keep classes: {sorted(obj_names[i] for i in obj_keep_ids)}",
          flush=True)

    # peek video meta
    cap = cv2.VideoCapture(in_mp4)
    if not cap.isOpened():
        sys.exit(f"cannot open {in_mp4}")
    fps    = cap.get(cv2.CAP_PROP_FPS)
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    no_blur_x = int(width * NO_BLUR_W)
    no_blur_y = int(height * NO_BLUR_H)

    print(f"in : {in_mp4}  {width}x{height}  {fps:.2f} fps  {total} frames",
          flush=True)

    # per-track storage. track_id is unique across the WHOLE video.
    tracks = {}                # tid -> {cls, frames[], bboxes[], confs[],
                               #         plates[], faces[]}
    t0 = time.time()
    frame_idx = 0

    # Ultralytics .track() with stream=True yields one Result per frame, in
    # order, with persistent track IDs.
    for result in obj.track(source=in_mp4, stream=True, persist=True,
                            verbose=False, device=0, conf=OBJ_CONF):
        frame = result.orig_img
        boxes = result.boxes
        if boxes is None or boxes.id is None:
            frame_idx += 1
            continue

        xy  = boxes.xyxy.cpu().numpy().astype(int)
        cls = boxes.cls.cpu().numpy().astype(int)
        cf  = boxes.conf.cpu().numpy()
        ids = boxes.id.cpu().numpy().astype(int)

        for (x1, y1, x2, y2), c, p, tid in zip(xy, cls, cf, ids):
            if c not in obj_keep_ids:
                continue
            name = obj_names[c]

            # Run focused plate detector inside vehicle box
            plate_box = None
            if name in VEHICLE_CLASSES:
                hits = run_in_crop(plate, frame, x1, y1, x2, y2,
                                   PLATE_CONF, width, height)
                # filter out detections in the dashcam-timestamp zone
                hits = [h for h in hits
                        if not ((h[0] + h[2]) // 2 < no_blur_x and
                                (h[1] + h[3]) // 2 < no_blur_y)]
                plate_box = pick_best(hits)

            # Run focused face detector inside person box
            face_box = None
            if name == "person":
                hits = run_in_crop(face, frame, x1, y1, x2, y2,
                                   FACE_CONF, width, height)
                face_box = pick_best(hits)

            entry = tracks.setdefault(int(tid), {
                "cls":    name,
                "frames": [],
                "bboxes": [],
                "confs":  [],
                "plates": [],
                "faces":  [],
            })
            entry["frames"].append(frame_idx)
            entry["bboxes"].append([int(x1), int(y1), int(x2), int(y2)])
            entry["confs"].append(round(float(p), 3))
            entry["plates"].append(plate_box)
            entry["faces"].append(face_box)

        frame_idx += 1
        if frame_idx % 60 == 0:
            el = time.time() - t0
            rate = frame_idx / el if el > 0 else 0
            eta = (total - frame_idx) / rate if rate > 0 else 0
            plate_n = sum(1 for t in tracks.values()
                          for p in t["plates"] if p)
            face_n = sum(1 for t in tracks.values()
                         for f in t["faces"] if f)
            print(f"frame {frame_idx}/{total}  {rate:.1f} fps  "
                  f"ETA {eta:5.0f}s  tracks={len(tracks)}  "
                  f"plate_detections={plate_n}  face_detections={face_n}",
                  flush=True)

    sidecar = {
        "video":         os.path.basename(in_mp4),
        "fps":           fps,
        "width":         width,
        "height":        height,
        "total_frames":  total,
        "tracks":        {str(k): v for k, v in tracks.items()},
    }
    Path(out_json).write_text(json.dumps(sidecar))

    el = time.time() - t0
    plate_n = sum(1 for t in tracks.values() for p in t["plates"] if p)
    face_n  = sum(1 for t in tracks.values() for f in t["faces"]  if f)
    print(f"done: {frame_idx} frames in {el:.0f}s ({frame_idx/el:.1f} fps)",
          flush=True)
    print(f"out: {out_json}  ({os.path.getsize(out_json)//1024} KB)",
          flush=True)
    print(f"tracks: {len(tracks)}  "
          f"plate-detection-events: {plate_n}  face-detection-events: {face_n}",
          flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input",  help="input 1080p mp4")
    ap.add_argument("output", help="output _detect.json sidecar")
    args = ap.parse_args()
    main(args.input, args.output)
