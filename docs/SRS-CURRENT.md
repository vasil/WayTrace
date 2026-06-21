# Open Streets Initiative — SRS-CURRENT
# Single permanent workflow file. Always updated in place. Never replaced.
# Last updated: 2026-06-19 UTC+2 (MERGED — pipeline order + dashboard/ISO 8608 unified)
#
# MERGE NOTE (2026-06-19): two SRS copies had forked on Drive — one with the
# locked per-push pipeline order, one with the OSI-017 footer-dashboard task.
# This file is the single reconciled truth: the pipeline-order version is the
# base (it was the more current), and the dashboard work is unified into the
# existing OSI-007-DASHBOARD-SPEC.md (now with an appended ISO 8608 method
# section). OSI-017 is retired as a duplicate — see note in TODO. Nothing was
# deleted; the dashboard detail lives in OSI-007-DASHBOARD-SPEC.md Appendix A.

---

## FOR CLAUDE CODE
Read THIS file. Execute IN PROGRESS task first. Update log when done.
Move completed task to DONE. Move next TODO to IN PROGRESS.
NEVER create a new SRS file — always update this one.

---

## *** PIPELINE CORRECTION — MERGE FIRST, SYNC ONCE (2026-06-21) ***
## This OVERRIDES the step order below where they conflict. Read this first.

PROBLEM observed (Vasil, 2026-06-21, reviewing yesterday's output):
the pipeline processed each camera-split MOV as if it were its own push.
The THIRD video started at the coffee break, the footer graph RESTARTED
from zero there, and the minimap dot jumped back to home (the push start)
instead of showing the true position. Each segment was synced and
positioned independently — WRONG. A push is ONE continuous thing.

CORRECTED ORDER — assemble the whole push BEFORE processing:

 A. MERGE VIDEO FIRST. Concatenate all camera-split MOVs of the push into
    ONE video, in recording order. Only the FIRST segment carries the
    OSI-016 "Push Off" clapper. The merged video is one continuous
    timeline.

 B. MERGE ART FIRST. From 2026-06-21 onward Vasil does NOT stop WayTrace
    mid-push — he uses PAUSE / RESUME. So there is normally ONE ART file
    per push already, containing a large GAP of rows in the middle where
    he paused for the break (he was not moving, so missing motion rows
    there are CORRECT and expected). If more than one ART file exists for
    a push, merge them into one continuous ART-MERGED-*.csv on the real
    timestamp timeline. Do NOT collapse or delete the pause gap — it is
    real (stationary) and must stay.

 C. ONE GPX FOR THE PUSH. The single Strava activity GPX spans the whole
    push. During the break the GPX sits in ONE place (he wasn't moving) —
    that is correct. The minimap dot starts at the TRUE GPS START of the
    Strava activity and moves continuously along the route for the whole
    push; it never resets per segment.

 D. FIND THE CLAPPER OFFSET ONCE, on the merged whole. The single
    chime near the start of the merged video anchors the entire push.
    video_to_art_offset is computed once and applies across the whole
    merged timeline (not per MOV).

 E. THEN PROCESS THE WHOLE PUSH as one continuous timeline: YOLO +
    GDPR blur + dashboard run over the merged video against the merged
    ART and the one GPX. Result: the footer VDV graph is ONE continuous
    line from push start to push end (flat/again-continuous across the
    pause gap, not restarted), and the dot is always at the correct
    real-world position.

NET RULE: one push = one merged video + one (merged) ART + one GPX,
synced ONCE on the clapper, processed as a single continuous timeline.
The old "CONCAT-PER-PUSH" at step 6 is therefore effectively done at
the START now (step A), not the end. Per-segment independent sync/
positioning is retired.

NEXT RUN PLAN (Vasil, 2026-06-21): when home, Vasil hands over a NEW data
set — new videos, ONE new ART file (pause/resume, with a mid gap), and the
ONE new Strava GPX. SYNC FIRST (steps A–D), confirm it, THEN start
processing (~22:00), expected done by ~11:00 next morning.

IMPLEMENTATION (2026-06-21, this session): see tools/merge_push_inputs.sh
(MOV concat + ART merge) and tools/vtx1_push_batch.sh (the push-level
batch script). The old tools/vtx1_phase2_batch.sh is retained for
reference but should not be used for new pushes.

---

## FILE NAMING CONVENTION
ART-YYYYMMDDHHMM.csv — sensor data
ART-MERGED-YYYYMMDDHHMM.csv — merged multi-session sensor data
WT-YYYYMMDDHHMM.apk — WayTrace app build
SRS-CURRENT.md — this file, permanent
RW-YYYYMMDDHHMM.mp4 — Rear Window video (Akaso V50 X, raw)
RW-YYYYMMDDHHMM-blurred.mp4 — GDPR processed (faces + plates blurred)
RW-YYYYMMDDHHMM-final.mp4 — single per-segment annotated output
RW-PUSH-YYYYMMDDHHMM-final.mp4 — one-file-per-push concat for YouTube upload
ANL-YYYYMMDDHHMM.txt — Analysis report
GPS-YYYYMMDDHHMM.gpx — GPS track from Strava
LOC-YYYYMMDDHHMM.png/.txt — Bad-spots map + ranked report
RQM-YYYYMMDDHHMM.png — Road-quality colored polyline + top-N hits
BSV-YYYYMMDDHHMM.mp4 — Beat-Synced Video output

---

## MACHINES
VT — development: Android builds, Claude Code, sensor analysis
VT-X1 (IP 10.0.0.110, DHCP — can move) — GPU: video processing, YOLO, YouTube upload

---

## PROJECT
App: WayTrace | Package: com.vasil.sensorlogger
Language: Kotlin | Min SDK 26 | Target SDK 34
GitHub: github.com/vasil/WayTrace
Phone: Xiaomi — 120–125 Hz sustained delivery (detected per-recording)

---

## PHYSICAL SETUP — CONFIRMED
Phone: RIGHT side of wheelchair, above front RIGHT caster wheel.
Portrait orientation, vertical. Screen faces rider. Camera end UP.
Mounting: fabric strap pocket — COMPLIANT mount (attenuates >50 Hz).
Y_accel = VERTICAL | X_accel = FORWARD/BACKWARD | Z_accel = LATERAL
Y_gyro = YAW | X_gyro = ROLL (danger) | Z_gyro = PITCH (wheelie)
NOTE: ISO 8608 vertical acceleration = Y_accel − g on this rig (NOT phone Z;
 Z is lateral here). Generic ISO "Z-axis" = world-vertical = Y_accel here.

---

## WHEELCHAIR GEOMETRY — PARTIALLY CONFIRMED
CASTER_DIAMETER_CM = 10.16 # 4 inches solid rubber — CONFIRMED
REAR_WHEEL_DIAMETER_CM = 60.0 # outer tyre — CONFIRMED
REAR_WHEEL_PRESSURE_BAR = 8 # pneumatic — CONFIRMED
WHEELBASE_LONGITUDINAL_CM = 32.0 # rear axle → caster axle — CONFIRMED
CASTER_TRAIL_CM = 5.0 # steering pin → contact — CONFIRMED
PHONE_HEIGHT_CM = 35.0 # floor to phone center — CONFIRMED
PHONE_FORWARD_OFFSET_CM = 15.0 # phone ahead of caster — CONFIRMED
PHONE_LATERAL_OFFSET_CM = 3.0 # phone outward from centerline — CONFIRMED
 PHONE_MOUNT_TYPE = "fabric_strap_pocket" # compliant
RIDER_WEIGHT_KG = 63.0 # CONFIRMED
CHAIR_WEIGHT_KG = 15.0 # old wheelchair — CONFIRMED
TOTAL_ROLLING_MASS_KG = 78.0 # rider + chair
PNEUMATIC_FACTOR = 1.47 # rear wheel absorption [REF-005]
GEOMETRY_FACTOR_CASTER_COMPLIANT = 2.0 # rider-exposure overstate factor for
 # fabric-strap pocket over caster
# Remaining: seat height, track widths, weight balance, wheelie ref — TO MEASURE
# NOTE: new Küschall with Spinergy wheels being assembled — lighter, update when ready.

---

## SCIENTIFIC REFERENCES
REF-001 to REF-010 — see previous SRS versions for full citations.
Key: ISO 2631-1 (REF-007), ASTM E3028-16 WPRI (REF-008),
Garcia-Mendez 2013 (REF-009), RFC OSI-ORIGINAL (REF-010).
REF-011: ISO 8608 road-roughness classes A–H, Gd(n₀) at n₀=0.1 cyc/m.

---

## CSV FORMAT — v3
Columns: timestamp_ms, sensor, x, y, z, rotvec_w
Rate: 120–125 Hz (per-device, detected at runtime) | All event detection offline in Python.
Marker rows (sensor=pinpoint or sensor=sync_pulse) carry the counter in col 3 and zeros in cols 4-5.

---

## UI COLOR LANGUAGE — LOCKED
START: GREY+GREEN | PAUSE: GREEN+ORANGE | RESUME: ORANGE+GREEN
STOP: RED+WHITE | PIN: BLUE+WHITE
SYNC: CYAN+BLACK (clapper button — see OSI-016)

---

## EXTERNAL OUTREACH — STRAVA FEATURE REQUEST
Date: 2026-06-08, refreshed 2026-06-17 after OSI-006b.
Status: DRAFT v2 at docs/strava-feature-request-2026-06-17.md (in OSI repo).
Proposed: Road Surface Quality map type colored by ISO 2631-1 Wk-weighted
vertical RMS in sliding windows.

---

## FUNDING — POTENTIAL TARGETS
Date added: 2026-06-08. Status: TO BE DRAFTED — one-page summary needed first.
Targets: EDF, Mozilla, Wellcome, OSM Foundation, Knight, NMK Ministry, RESNA.

---

## PER-PUSH PIPELINE — LOCKED ORDER (2026-06-19)

The canonical processing pipeline for a single Rear Window push, from
the moment Vasil walks in with the SD card to the moment a video is
ready to upload. Each stage feeds the next; nothing skips.

 1. AUTO-FETCH on SD insert
 Trigger: SD card inserted into VT-X1.
 - Mount, copy MOVs into ~/Videos/VIDEO/, byte-verify, wipe SD.
 - Auto-download the matching ART CSV(s) from gdrive2 (phone-side
 waytrace upload), and the GPX of the matching Strava activity
 via waytrace_strava.py.
 - Zero human steps after card insertion.

 2. SYNC ART ↔ VIDEO (chime-locked)
 - For each MOV, scan its audio track with tools/sync_chime_detect.py
 for the OSI-016 "Push Off" 5-note chime (C5-E5-G5-A5-C6 octave-up).
 - Match each detected chime to the corresponding sync_pulse row in
 the ART CSV by ordinal (first chime = first sync_pulse, etc.).
 - Compute video_to_art_offset_seconds per MOV; sanity-check that
 the start-of-MOV and end-of-MOV chime offsets agree.
 - Persist the offsets as a sidecar JSON next to each MOV.

 3. YOLO DETECTION PASS (identify before blurring)
 - Run YOLO on the (downscaled 1080p) video. Save bounding boxes
 for every car, truck, motorcycle, bus, person, bicycle,
 mobility-aid user, stroller, large/small obstacle.
 - These boxes are the substrate for both the GDPR blur (step 4)
 AND the color-coded annotation overlay carried into step 5.
 - Identification comes FIRST so step 4 can search inside each box
 with high prior, instead of full-frame.
 - TRACKING (required for step 4 temporal persistence): assign a
 STABLE TRACK ID to each vehicle and each person across frames
 (IoU/centroid tracker or built-in YOLO tracker). The plate/face
 blur in step 4 is applied per TRACK across its whole lifespan,
 not per isolated frame. See OSI-021 "temporal blur persistence".

 4. TARGETED GDPR BLUR ("look harder inside the box, blur when uncertain")
 - VEHICLE boxes (car/truck/motorcycle/bus/van): run a second,
 focused plate detector inside each box at a LOWER threshold than
 full-frame. Crop-priored detection catches plates the full-frame
 pass misses.
 - PERSON boxes: run a focused face detector inside each box at a
 lower threshold. Catches small/distant/edge faces.
 - SAFETY BACKSTOP (the inviolable rule, per Vasil's axiom "every
 car has a plate, every person has a face on the head"):
 • Vehicle box with NO plate found → blur the plate-likely
 zone (lower portion of the box) anyway.
 • Person box facing the camera with NO face found → blur the
 head region (upper portion of the box) anyway.
 • A person seen from behind has no face to blur — that is fine.
 • Over-blurring costs nothing; under-blurring is the violation.
 - TEMPORAL PERSISTENCE (NO BLINKING — see OSI-021, hard requirement):
 once a plate/face is blurred on a tracked object in ANY frame, it
 stays blurred for the ENTIRE track — through every frame the object
 is on screen, including frames where the detector missed it, and
 with padding before the first and after the last detection. The
 same car's plate is never readable for even one frame.
 - This stage is OSI-016 GDPR hardening, implementing what
 REAR-WINDOW-NEXT-TASKS.md priority 1 specifies. It REPLACES
 osi007_final.py's current single-pass blur-and-box.

 5. DASHBOARD HUD OVERLAY
 - Composit the OSI-007 Phase-2 dashboard per
 OSI-007-DASHBOARD-SPEC.md on top of the blurred+boxed frames.
 - HUD layers: push title + speed (upper-left, no bg), route map
 with current-position dot (upper-right, borderline only),
 30-min rolling Wk-weighted RMS trace with current-roughness
 coloured background + ISO 2631-1 threshold line (footer).
 - ISO 8608 class line / "X → Y" flash uses the rigorous method now
 in OSI-007-DASHBOARD-SPEC.md APPENDIX A (50 m windows, time→space,
 FFT PSD, S_z=C·(Ω/Ω₀)^-2 fit, class lookup). Computed offline in
 waytrace_analysis.py; the dashboard reads per-window output.
 - Inputs: dashboard takes the blurred-boxed MP4 from step 4, the
 ART CSV, the GPX, the Strava push title, and the offset from
 step 2.

 6. CONCAT-PER-PUSH
 - Lossless ffmpeg -f concat -c copy of all per-segment
 RW-*-final.mp4 of this push into ONE
 RW-PUSH-YYYYMMDDHHMM-final.mp4.
 - The Strava activity is the canonical session boundary; the
 multiple camera-split MOVs and the multiple ART files of a
 single push are stitched back into one upload artefact.

 7. (OPTIONAL) UPLOAD TO YOUTUBE (gated)
 - tools/youtube_upload.py uploads the concat from step 6 as
 privacy=unlisted, description auto-built from osi007 JSON
 counts.
 - GATED behind UPLOAD_TO_YOUTUBE=1 env var — stays OFF until
 step 4 (GDPR blur) is provably airtight, per the SRS rule
 "NO video goes to YouTube until ALL plates are confirmed
 blurred." Today's first run keeps the gate closed and the
 per-segment + concat outputs sit locally for manual review.

 PHILOSOPHY OF THE ORDER
- Identify (YOLO) BEFORE you redact (blur). Knowing what you're
 redacting makes the redaction better, and the YOLO boxes carry
 forward into the annotation overlay so the work isn't redundant.
- Lock the ART-video sync BEFORE the dashboard step. Anything the
 HUD reports has to be true for the exact pixel underneath it.
- The concat is the LAST step before upload — never re-encode after
 it, only ever -c copy.
- Upload is the FINAL gate and stays explicit; a flipped switch, not
 a default.

---

## CURRENT TASKS

### IN PROGRESS

**OSI-007 | Rear Window Video Pipeline — Phase-2 batch running**
 Priority: HIGH. Machine: VT-X1 (10.0.0.110). Updated: 2026-06-19.
 Re-run of the per-segment pipeline on the 8 MOVs of the 2026-06-18
 Rear Window Push, with the dashboard HUD step now active. First
 attempt 2026-06-19 ran 11 h then silently failed every dashboard
 step (pandas missing in the osi007 conda env — fixed by `pip install
 pandas` and a PRE-FLIGHT import check in the batch script). Second
 attempt launched 11:57 today, ETA finish ~23:00. The concat-per-push
 (step 6) and the optional gated YouTube upload (step 7) are wired
 into the batch but the YouTube gate is OFF this run — manual review
 on the concat first.
 Full dashboard visual spec: OSI-007-DASHBOARD-SPEC.md (4 layers) +
 APPENDIX A (ISO 8608 coefficient-C method behind the class line).
 KNOWN ISSUE (2026-06-19, from Vasil's review of an earlier output):
 license-plate blur BLINKS — a plate is blurred in some frames and
 readable in others on the SAME car. This is a GDPR leak and is fixed
 by the temporal-persistence requirement added to OSI-021 below. Do
 NOT consider OSI-007 publish-ready until plate/face blur is blink-free.

**OSI-016 | WayTrace SYNC clapper — first field run completed, working**
 Status: 2026-06-18 push used the OSI-016 SYNC clapper in the field
 for the first time. 4 chimes recorded across 8 camera-split MOVs,
 matching the 4 sync_pulse rows in the 2 ART CSVs (one clap right
 after START, one right before STOP, per the confirmed routine).
 tools/sync_chime_detect.py (template cross-correlation against the
 bundled chime WAV, NCC threshold 0.20) finds all 4 cleanly with no
 false positives. Per-MOV video↔ART offsets derived; phone-to-camera
 START delta is ~7-10 s per session (Vasil presses phone START, then
 camera). Field acceptance test "real-world audibility" PASSED.
 Cooldown + button-size bug-fix APK (WT-202606181442.apk) installed
 and behaving — no cooldown violations seen.
 (Earlier in-room retest 2026-06-18 15:11 also confirmed the fixes:
 ART-202606181509.csv had sync_pulse deltas 15908 ms and 16837 ms,
 both > 10 s cooldown; button equal-width and label readable.)

---

### TODO

**OSI-021 | Refactor osi007_final into the new per-push pipeline order**
 Source: the PER-PUSH PIPELINE section, locked 2026-06-19.
 Status (2026-06-20): **CODE COMPLETE, batch redeploying.** Vasil
 greenlit the refactor (out of order vs. the original "after batch
 review" plan) once he saw the blinking GDPR leak in the 2026-06-19
 osi007_final.py output. The 2026-06-19 push batch was stopped mid-
 first-MOV so the new pipeline could replace osi007_final.py before
 anything else gets blurred-with-blinks. Implementation summary:

   tools/osi007_detect.py (NEW, 169 lines):
     - Ultralytics YOLO obj.track() with the default BotSort tracker
       for stable per-object IDs across frames.
     - For each tracked vehicle (car/truck/bus/motorcycle): crops the
       bbox and runs the plate detector inside it at LOWER conf 0.10
       (vs. the old full-frame 0.15); skips the dashcam-timestamp
       upper-left zone (heuristic kept from osi007_final.py).
     - For each tracked person: crops and runs the face detector at
       conf 0.20 (vs. 0.30).
     - Emits a per-track JSON sidecar: track_id → cls, frames[],
       bboxes[], confs[], plates[], faces[] (parallel arrays).

   tools/osi007_blur.py (NEW, 280 lines):
     - Reads the detect JSON + the 1080p MP4 source.
     - For each track:
        * If any plate detection exists on the track: expresses each
          detection as a position RELATIVE to the vehicle bbox at
          detection time (rx1, ry1, rx2, ry2 in [0,1]), linearly
          interpolates the relative box between consecutive
          detections, projects onto the CURRENT vehicle bbox at each
          frame — so the blur scales with the car driving away and
          tracks it sideways. Pads ±round(0.5 × fps) frames around
          first/last detection.
        * If a vehicle is tracked but a plate is NEVER detected:
          SAFETY BACKSTOP — blurs the lower 60–100 % of the vehicle
          bbox, inset 10 % from each side, for the WHOLE track.
        * Same logic for faces on person tracks. Backstop = upper
          25 % of person bbox, inset 15 % each side (head region).
     - Then composites the SRS colored YOLO boxes on top (RED vehicles,
       GREEN cyclists/persons, BLUE small obstacles) — taking the
       per-frame bbox from the detect sidecar so the boxes don't
       duplicate detection work.
     - Mux audio from source. Write counts JSON sidecar with track-
       level breakdowns (plates_with_real_detection vs.
       plates_backstop_only, etc.) for review.

   tools/vtx1_phase2_batch.sh:
     - Step 2 split into [2/4] detect + [3/4] blur; step 4 is now [4/4]
       dashboard; gated YouTube upload renumbered [5/5].
     - PRE-FLIGHT check extended to include ultralytics.

 ACCEPTANCE (per Vasil's "removed, absolutely"): on the 2026-06-20 batch
 output, frame-by-frame review must show ZERO frames anywhere with a
 readable plate or identifiable forward-facing face on any tracked
 object — including during detector dropouts. Only after this passes
 may UPLOAD_TO_YOUTUBE=1 be flipped.

 Original problem statement, kept for context:
 Today's osi007_final.py does plate/face/object detection in one pass
 and writes the consolidated output directly. The new order separates
 these into independent stages so step 4's GDPR blur can be priored
 on step 3's YOLO boxes (cropped low-threshold detection inside each
 vehicle/person box), and so the boxes feed forward into the
 annotation overlay without redundant detection.

 *** HARD REQUIREMENT — TEMPORAL BLUR PERSISTENCE (NO BLINKING) ***
 (Added 2026-06-19 from Vasil's review. This is GDPR-critical and
 non-negotiable; the YouTube gate stays OFF until it passes.)
 PROBLEM observed in current output: the plate blur is computed
 PER FRAME independently. When the plate detector misses for a frame
 or two, the blur drops and the plate becomes readable, then blurs
 again — it BLINKS. Even a single readable frame is a privacy leak;
 the number got out.
 REQUIRED behaviour: blur is bound to the TRACKED OBJECT, not the
 frame. Concretely:
 - Use the stable per-object track IDs from step 3 (YOLO tracking).
 - For each tracked vehicle, maintain a plate-region estimate. Once a
 plate is detected anywhere on that track, BLUR THAT REGION IN EVERY
 FRAME of the track — including frames where detection failed, by
 carrying the last known plate box and/or interpolating between
 detections, and tracking the region with the car's motion.
 - PAD the blur in time: start blurring N frames BEFORE the first
 detection and continue N frames AFTER the last (e.g. N ≈ 0.5 s
 worth of frames), so onset/loss of detection is never visible.
 - If a vehicle is tracked but a plate is NEVER detected, fall back to
 the SAFETY BACKSTOP (blur the plate-likely lower zone of the box)
 for the whole track.
 - Same temporal persistence applies to FACES on person tracks: once
 blurred on a track, stay blurred across the track (a person turning
 away briefly must not flash their face); forward-facing-with-no-
 detection still gets the head-region backstop.
 - Net rule, in Vasil's words: the same car / same yellow mark keeps
 its plate blurred before, during, and after — the numbers are
 removed absolutely and never shown.
 ACCEPTANCE (temporal persistence): frame-by-frame review of the
 2026-06-18 push output shows ZERO frames anywhere with a readable
 plate or an identifiable forward-facing face on any tracked object —
 including during detector dropouts. No blinking. Only then may
 UPLOAD_TO_YOUTUBE be set.

 Touches: app/src/main/res/raw/* (no change), osi007_final.py (split
 into osi007_detect.py + osi007_blur.py, both producing sidecars the
 dashboard step consumes; osi007_detect.py now also emits per-object
 track IDs; osi007_blur.py consumes tracks for temporal persistence),
 tools/vtx1_phase2_batch.sh (re-wire to call the two new stages in
 order), tools/osi007_dashboard.py (no change — already takes the
 post-blur MP4).

 Acceptance: same visual quality of boxes and blurs as today, plus
 ZERO unblurred plates and ZERO unblurred forward-facing faces on
 spot-checks of the 2026-06-18 push output (per the temporal-
 persistence acceptance above). Then the YouTube upload gate
 (UPLOAD_TO_YOUTUBE=1) can be flipped.

**OSI-011 | Stationary suppression**
 Min 1 row/sec stationary. Full 120 Hz motion.

**OSI-012 | Beat-synced video — waytrace_beatsync.py (VT-X1)**
 librosa + MoviePy. BSV-YYYYMMDDHHMM.mp4.

**OSI-014 | Funding application — one-page project summary**
 Draft for international funding bodies; see FUNDING section.

**OSI-015 | Strava feature request email**
 Send to developers@strava.com and/or Strava Community Hub.

**REAR-WINDOW-NEXT-TASKS** (3 remaining, P1 absorbed into OSI-021 above):
 P2 Wider non-COCO detection model (bins, dumpsters, mobility aids, strollers)
 and drop "bench" [confirmed needed — trash cans box as "parking"]
 P3 Vibration border overlay — ABSORBED into the OSI-007 dashboard footer
 (LAYER 4 + Appendix A); kept here only as a pointer.
 P4 Road-vs-pavement segmentation + map-matching

# RETIRED: OSI-017 (footer dashboard) — was a duplicate created 2026-06-19
# of work already specified in OSI-007-DASHBOARD-SPEC.md. Its one unique
# addition (the rigorous ISO 8608 coefficient-C objective) is now folded
# into that spec as APPENDIX A. Do not re-create OSI-017; the dashboard is
# part of OSI-007 + its spec file.

---

### DONE
OSI-001 through OSI-006, OSI-006b, OSI-009, OSI-010, OSI-013 — see previous logs and UPDATE LOG.

(OSI-019 UDP sonification was confirmed BUILT per RECONCILIATION-LEDGER
C-011 — commit 7770e19, 2026-05-18 — but stays informal until/unless
Vasil folds it in formally.)

---

## UPDATE LOG
(Older entries elided for brevity; see prior SRS revisions for full history.
 Latest entries:)
2026-06-17 — OSI-006b DONE.
2026-06-17 — OSI-016 created from phone with full SYNC clapper spec.
2026-06-18 (01:15) — OSI-016 PARTIAL: first SYNC APK shipped (WT-202606180115.apk).
2026-06-18 (02:20) — In-room test found 2 UI bugs (clipped SYNC button + missing cooldown).
2026-06-18 (06:04) — Last week's OSI-007 batch FINISHED (10 h 46 min).
2026-06-18 (14:42) — OSI-016 bug fixes shipped (WT-202606181442.apk).
2026-06-18 (15:11) — OSI-016 in-room retest PASSED (cooldown deltas >10 s; button readable).
2026-06-18 (18:03) — OSI-007-DASHBOARD-SPEC.md written (4-layer HUD, from v6 sketch).
2026-06-18 push — first field run of OSI-016 SYNC clapper: 4 chimes
 captured across 8 MOVs + 2 ART files, all detectable post hoc; sync
 lock works.
2026-06-19 (11:57) — Phase-2 batch re-launched after the overnight
 pandas-missing wipeout. PRE-FLIGHT import check + fail-fast per step
 hardened into tools/vtx1_phase2_batch.sh.
2026-06-19 — Per-push pipeline order LOCKED (see PER-PUSH PIPELINE section):
 (1) auto-fetch on SD insert → (2) chime-locked ART↔video sync →
 (3) YOLO detection pass → (4) targeted GDPR blur with "blur-when-
 uncertain" backstop → (5) dashboard HUD → (6) concat per push →
 (7) gated YouTube upload. The refactor of osi007_final.py to match
 this order is OSI-021 in TODO.
2026-06-19 — OSI-007 spec note: once Vasil sets the Akaso V50 X clock to
 real wall-clock time, the camera burns an upper-left timestamp into the
 frame that will overlap the dashboard title + speed HUD. Future layout
 will shift the HUD text down ~80 px or crop a thin band off the top
 before running the dashboard.
2026-06-19 (20:30) — MERGE: two forked SRS copies on Drive reconciled into
 this single file (base = pipeline-order version; the OSI-017 footer-
 dashboard task folded into OSI-007-DASHBOARD-SPEC.md as APPENDIX A —
 the rigorous ISO 8608 coefficient-C method: 50 m windows, time→space,
 FFT PSD, S_z=C·(Ω/Ω₀)^-2 fit, A–F class lookup, with the Y_accel−g
 vertical-axis correction and honesty guards). OSI-017 retired as a
 duplicate. REAR-WINDOW P3 (vibration border) noted as absorbed into the
 dashboard footer. No content deleted anywhere.
2026-06-19 (later) — OSI-021 HARD REQUIREMENT added: temporal blur
 persistence (NO BLINKING). Vasil reviewed output and saw plate blur
 flickering on/off on the same car (readable in some frames). Fix:
 blur bound to YOLO track ID across the whole track — before, during,
 and after detections, with time padding and interpolation through
 detector dropouts; faces same way; safety backstop for never-detected
 plates/faces. Tracking added to pipeline step 3. ZERO readable-plate
 frames is the acceptance bar before UPLOAD_TO_YOUTUBE may be enabled.
 "Removed, absolutely" — Vasil.
2026-06-20 (morning) — 2026-06-19 push batch ("ISO 8608 Class E Push",
 6.20 km, 7 MOVs) launched at 09:17, stopped at ~10:30 mid-first-MOV
 after Vasil flagged the blinking GDPR leak in the existing osi007_final
 output. SD card mounted on VT-X1, 22 GB of new MOVs copied (yesterday's
 sources archived to ~/Videos/VIDEO_prev_20260620), per-MOV offsets
 derived from chime detection: session 1 bookend drift -0.62 s, session
 2 bookend drift -1.69 s (both healthy). New ART files + GPX uploaded
 to ~/waytrace-video/art/. Yesterday's 2026-06-18 dashboard outputs
 archived to ~/Videos/VIDEO_dashboard_20260618/ (concat
 RW-PUSH-202606181630-final.mp4, 9.8 GB, 99 m 28 s) for review while
 the new pipeline is built.
2026-06-20 (later) — OSI-021 IMPLEMENTED. osi007_detect.py +
 osi007_blur.py written. The detect script tracks objects with
 Ultralytics BotSort and runs focused plate/face detection inside each
 vehicle/person crop; the blur script computes per-track temporal-
 persistent blur regions via relative-bbox interpolation + ±0.5 s
 padding + safety backstop. Batch script re-wired to call the two new
 stages in order. Pipeline is now ready for the 2026-06-19 push re-run
 with no-blinking blur. Acceptance review (frame-by-frame, ZERO
 readable plates / forward-facing faces) is gated before
 UPLOAD_TO_YOUTUBE can be flipped.
