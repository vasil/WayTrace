# OSI-007 New Pipeline — Design Plan

Based on SRS-CURRENT.md (Drive id `12u95SaenpyoaoQq8cGp7GO6dFSotkFQr`,
updated 2026-06-08 17:55).

---

## Goal (from new SRS)

End-state workflow:
1. Insert Akaso SD card into VT-X1.
2. ART CSV uploads to Drive (already wired via `waytrace_strava.py`).
3. Run pipeline overnight on VT-X1.
4. Morning: `RW-YYYYMMDDHHMM-final.mp4` ready, with:
   - Face blur (GDPR)
   - **License plate blur — ALL plates, verified** (current critical gap)
   - Colored boxes per new 7-color scheme
   - Sensor overlay strip at bottom (RMS bands)
   - GPS speed + bump counter top-right
   - YELLOW boxes at GPS coords of ART `heavy_bump` events
   - Per-class counts in sidecar JSON for YouTube description

---

## The hard parts

The new SRS describes capabilities at three difficulty tiers:

| Tier | Capability | Why hard |
|---|---|---|
| **Easy** | Face blur, plate blur, vehicle/person/bicycle boxes | Existing YOLO models cover COCO classes; just wire up correctly |
| **Medium** | Pavement-vs-road classification (for RED rule), sensor overlay strip, ART-sync YELLOW | Pavement-vs-road needs semantic segmentation (SegFormer, ~heavy). ART sync needs GPS time-alignment between CSV and video |
| **Hard** | Stroller, mobility-aid (cane/walker), elderly detection (by gait) | Not in COCO. Need custom-trained models OR research-grade temporal models (ST-GCN for gait) |

Shipping it all at once would be a multi-week project. Proposing 3 phases.

---

## Phase 1 — MVP (this week)

**Goal:** ship a single-pass `osi007_final.py` that:

1. **Fixes the GDPR plate gap** (most urgent — blocks any YouTube publishing).
2. Replaces person-box face approximation with real face detection.
3. Maps COCO classes to the new colors, with the understanding that some
   classes (RED-on-pavement, stroller, mobility-aid, elderly) collapse
   into nearby COCO classes for v1.

### Class mapping for Phase 1

| New SRS color | COCO classes used | Notes |
|---|---|---|
| **RED** — vehicles on pavement | car, truck, bus, motorcycle | v1: ALL vehicles RED regardless of position. Pavement segmentation added in Phase 2 |
| **BLUE** — small obstacles | fire hydrant, bench, parking meter | These are the COCO classes closest to "bollard / street furniture" |
| **GREEN** — cyclists / strollers | bicycle, person + bicycle nearby | Stroller detection deferred to Phase 3 |
| **PURPLE** — mobility aid | — | Deferred to Phase 3 (no COCO class; needs custom model) |
| **WHITE** — elderly | — | Deferred to Phase 3 (gait analysis is a research project) |
| **ORANGE** — large fixed | — | Deferred to Phase 2 (no COCO class for dumpster/container) |
| **YELLOW** — road failure | — | Deferred to Phase 2 (needs ART sync) |

So Phase 1 ships **RED + BLUE + GREEN** boxes + face/plate blur. Three of
seven colors. The other four colors come in later phases.

### GDPR plate fix (critical)

Two changes vs current `combined.py`:
- **Lower confidence threshold** 0.25 → 0.15 (more recall, accepting more
  false positives — false positives just mean an extra blur, fine).
- **Tile-based pass**: run plate detector on 4 overlapping tiles (1080×720
  each) at full source resolution, not on downscaled frame. Catches small
  plates the single-frame pass misses.
- **Wider blur padding** 6 px → 12 px to catch text bleeding past detection.

### Face blur fix

- Download `yolov8n-face.pt` (akanametov/yolov8-face). ~6 MB. Already
  ultralytics-compatible.
- Replace top-25%-of-person-box heuristic with real face boxes.

### Resolution

Source MOVs are 4K. SRS says "Resolution: same as source (4K)". So **drop
the downscale to 1080p** that's in the current pipeline. Single pass at
3840×2160. Output `RW-YYYYMMDDHHMM-final.mp4` at 4K, h264+AAC, faststart.

This will be slower per frame (~3x pixel work) but no quality loss for the
YouTube upload.

### Files Phase 1 produces

- `~/waytrace-video/osi007_final.py` — new single-pass script
- `~/waytrace-video/models/yolov8n-face.pt` — downloaded once
- `~/Videos/RW-<base>-final.mp4` — per-MOV output
- `~/Videos/RW-<base>-final.json` — counts sidecar:
  ```json
  {"vehicles": 1234, "cyclists": 56, "persons": 789,
   "small_obstacles": 12, "plates_blurred": 4491,
   "faces_blurred": 1631}
  ```

### Phase 1 wall-time estimate

Per 4-GB 4K MOV: ~2-3 h on the GTX 1050 Ti Max-Q (3x pixel work vs current
1080p). 8 MOVs → batch runs overnight, ready by morning. Matches the SRS
target workflow.

---

## Phase 2 — Semantic segmentation + sensor sync (next)

- **Pavement segmentation**: SegFormer trained on Cityscapes. Adds RED rule
  ("vehicle on pavement") and the BLUE/ORANGE distinction.
- **ART sensor sync**: GPS-time-align CSV with video. Place YELLOW box at
  frame timestamps where `heavy_bump` events occurred.
- **Sensor overlay strip**: bottom 30 px, RMS-colored band. GPS speed +
  bump counter top-right corner. Reads from the matching ART CSV.

---

## Phase 3 — Custom detectors (later, optional)

- **Pothole detection**: dedicated YOLO model from Roboflow (well-trained
  ones exist).
- **Stroller / mobility-aid detection**: requires either a fine-tune (need
  labeled data) or finding a published model.
- **Elderly detection**: stretch goal. Could approximate via person tracking
  + velocity thresholding (slow walker on bad infra = WHITE candidate).
  Genuine gait classification is its own research project.

---

## Decisions to confirm before I start Phase 1

Defaults I'd pick if you don't push back:

| Decision | My default |
|---|---|
| Face model | Download `yolov8n-face.pt` (~6 MB) — much better than person-box top-25% |
| Plate fix | Lower threshold 0.15 + tile-based pass + 12 px padding |
| Resolution | 4K (no downscale) — matches new SRS spec |
| Naming | `RW-YYYYMMDDHHMM-final.mp4` per SRS |
| Counts sidecar | JSON next to mp4 — useful for both ANL report and YouTube description |
| Existing pipeline | Keep `combined.py` on disk as reference, mark as deprecated. Phase-1 lives in new `osi007_final.py` |

If those defaults all fit, I start writing `osi007_final.py` once the
current `combined.py` run finishes (so we can eyeball plate-blur quality
on the current consolidated.mp4 first as a baseline). Say the word and
I start.
