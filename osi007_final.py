#!/usr/bin/env python3
"""
osi007_final.py  IN.mov  OUT.mp4

OSI-007 final-pipeline single-pass:
  • plate detection (YOLOv11 fine-tune)  → Gaussian blur, conf 0.15, pad 12 px
  • face  detection (YOLOv8-face)        → Gaussian blur
  • object detection (YOLOv8n COCO)      → colored boxes per new SRS scheme:
        RED   #CC0000   any vehicle  (car, truck, bus, motorcycle)
        GREEN #00AA00   person, bicycle  (vulnerable user, cyclist)
        BLUE  #0055CC   small fixed obstacle  (fire hydrant, bench,
                                              parking meter, stop sign)

Source resolution preserved.  Audio re-muxed from input.  Counts written
to a JSON sidecar next to OUT.mp4.

Phase 1 of the OSI-007 rewrite (SRS 2026-06-08).  Deferred to later phases:
ORANGE / YELLOW / PURPLE / WHITE classes, pavement-vs-road segmentation,
ART sensor overlay strip, ART-synced YELLOW boxes.
"""
import json, os, subprocess, sys, time
import cv2
from ultralytics import YOLO

PLATE_MODEL = "/home/vasil/waytrace-video/models/plate_yolov11.pt"
FACE_MODEL  = "/home/vasil/waytrace-video/models/yolov8n-face.pt"
OBJ_MODEL   = "yolov8n.pt"   # ultralytics auto-downloads on first run

PLATE_CONF  = 0.15           # was 0.25 — lower → catch more plates
PLATE_PAD   = 12             # was 6  — wider → catch text bleeding past detection
PLATE_KERNEL = (61, 61); PLATE_SIGMA = 30

FACE_CONF   = 0.30
FACE_PAD    = 6
FACE_KERNEL = (51, 51); FACE_SIGMA = 25

OBJ_CONF    = 0.35

# top-left dashcam timestamp exclusion (fractions of frame size)
NO_BLUR_W = 0.25
NO_BLUR_H = 0.08

# BGR (cv2 is BGR-native)
RED   = (0,   0,   204)   # #CC0000
GREEN = (0,   170, 0)     # #00AA00
BLUE  = (204, 85,  0)     # #0055CC

VEHICLE_CLASSES = {"car", "truck", "bus", "motorcycle"}
CYCLIST_CLASSES = {"person", "bicycle"}
OBSTACLE_CLASSES = {"fire hydrant", "bench", "parking meter", "stop sign"}

CLASS_COLOR = {
    **{c: RED   for c in VEHICLE_CLASSES},
    **{c: GREEN for c in CYCLIST_CLASSES},
    **{c: BLUE  for c in OBSTACLE_CLASSES},
}


def main(inp, out):
    plate = YOLO(PLATE_MODEL)
    face  = YOLO(FACE_MODEL)
    obj   = YOLO(OBJ_MODEL)
    obj_names = obj.names
    obj_keep  = {i for i, n in obj_names.items() if n in CLASS_COLOR}
    print(f"obj classes kept: {sorted(obj_names[i] for i in obj_keep)}", flush=True)

    cap = cv2.VideoCapture(inp)
    if not cap.isOpened():
        sys.exit(f"cannot open {inp}")
    fps   = cap.get(cv2.CAP_PROP_FPS)
    w     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    no_blur_x = int(w * NO_BLUR_W)
    no_blur_y = int(h * NO_BLUR_H)
    print(f"in : {inp}  {w}x{h}  {fps:.2f} fps  {total} frames", flush=True)

    tmp = f"{out}.video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"avc1")
    wri = cv2.VideoWriter(tmp, fourcc, fps, (w, h))
    if not wri.isOpened():
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        wri = cv2.VideoWriter(tmp, fourcc, fps, (w, h))
    if not wri.isOpened():
        sys.exit("VideoWriter failed to open")

    counts = {
        "frames":            0,
        "plates_blurred":    0,
        "plates_skipped_in_timestamp_zone": 0,
        "faces_blurred":     0,
        "vehicles":          0,   # RED total
        "cyclists":          0,   # bicycle only (not person)
        "persons":           0,   # GREEN person
        "small_obstacles":   0,   # BLUE total
    }
    t0 = time.time()
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        # --- plate pass: blur first (so any later box draw stays clean) ---
        r_pl = plate(frame, verbose=False, device=0, conf=PLATE_CONF)[0]
        if r_pl.boxes is not None:
            xy = r_pl.boxes.xyxy.cpu().numpy().astype(int)
            for x1, y1, x2, y2 in xy:
                cx, cy = (x1+x2)//2, (y1+y2)//2
                if cx < no_blur_x and cy < no_blur_y:
                    counts["plates_skipped_in_timestamp_zone"] += 1
                    continue
                x1 = max(0, x1 - PLATE_PAD); y1 = max(0, y1 - PLATE_PAD)
                x2 = min(w, x2 + PLATE_PAD); y2 = min(h, y2 + PLATE_PAD)
                roi = frame[y1:y2, x1:x2]
                if roi.size:
                    frame[y1:y2, x1:x2] = cv2.GaussianBlur(
                        roi, PLATE_KERNEL, PLATE_SIGMA)
                    counts["plates_blurred"] += 1

        # --- face pass: blur ---
        r_fc = face(frame, verbose=False, device=0, conf=FACE_CONF)[0]
        if r_fc.boxes is not None:
            xy = r_fc.boxes.xyxy.cpu().numpy().astype(int)
            for x1, y1, x2, y2 in xy:
                x1 = max(0, x1 - FACE_PAD); y1 = max(0, y1 - FACE_PAD)
                x2 = min(w, x2 + FACE_PAD); y2 = min(h, y2 + FACE_PAD)
                roi = frame[y1:y2, x1:x2]
                if roi.size:
                    frame[y1:y2, x1:x2] = cv2.GaussianBlur(
                        roi, FACE_KERNEL, FACE_SIGMA)
                    counts["faces_blurred"] += 1

        # --- object pass: colored boxes + labels ---
        r_ob = obj(frame, verbose=False, device=0, conf=OBJ_CONF)[0]
        if r_ob.boxes is not None:
            xy  = r_ob.boxes.xyxy.cpu().numpy().astype(int)
            cls = r_ob.boxes.cls.cpu().numpy().astype(int)
            cf  = r_ob.boxes.conf.cpu().numpy()
            for (x1,y1,x2,y2), c, p in zip(xy, cls, cf):
                if c not in obj_keep:
                    continue
                name  = obj_names[c]
                color = CLASS_COLOR[name]
                cv2.rectangle(frame, (x1,y1), (x2,y2), color, 2)
                label = f"{name} {p:.2f}"
                (tw, th_), _ = cv2.getTextSize(
                    label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(frame, (x1, y1-th_-6), (x1+tw+4, y1), color, -1)
                cv2.putText(frame, label, (x1+2, y1-4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
                if name == "person":
                    counts["persons"] += 1
                elif name == "bicycle":
                    counts["cyclists"] += 1
                elif name in VEHICLE_CLASSES:
                    counts["vehicles"] += 1
                elif name in OBSTACLE_CLASSES:
                    counts["small_obstacles"] += 1

        wri.write(frame)
        i += 1; counts["frames"] = i
        if i % 60 == 0:
            el = time.time() - t0
            rate = i/el if el>0 else 0
            eta = (total-i)/rate if rate>0 else 0
            print(f"frame {i}/{total}  {rate:.1f} fps  ETA {eta:5.0f}s  "
                  f"plates={counts['plates_blurred']} faces={counts['faces_blurred']} "
                  f"vehicles={counts['vehicles']} persons={counts['persons']} "
                  f"cyclists={counts['cyclists']} obstacles={counts['small_obstacles']}",
                  flush=True)

    cap.release(); wri.release()

    # remux temp video with input's audio + faststart for streaming
    print("remuxing audio + faststart...", flush=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", tmp, "-i", inp,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a?",
        "-movflags", "+faststart",
        out,
    ], check=True)
    os.remove(tmp)

    sidecar = os.path.splitext(out)[0] + ".json"
    with open(sidecar, "w") as f:
        json.dump(counts, f, indent=2)

    el = time.time() - t0
    print(f"done: {i} frames in {el:.0f}s ({i/el:.1f} fps)", flush=True)
    print(f"out: {out}  ({os.path.getsize(out)//1024//1024} MB)", flush=True)
    print(f"counts: {sidecar}\n  {json.dumps(counts, indent=2)}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: osi007_final.py IN OUT")
    main(sys.argv[1], sys.argv[2])
