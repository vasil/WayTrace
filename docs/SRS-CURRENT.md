# Open Streets Initiative — SRS-CURRENT
# Single permanent workflow file. Always updated in place. Never replaced.
# Last updated: 2026-06-27 UTC+2 (license-plate streak tracker OSI-024 +
#                                  dashboard speed-HUD 1 Hz latch + GPS-dropout blank)
#
# MERGE NOTE (2026-06-19): two SRS copies had forked on Drive — reconciled
# into one. Dashboard detail lives in OSI-007-DASHBOARD-SPEC.md (Appendix A
# ISO 8608 method, Appendix B clarifications). OSI-017 retired as duplicate.

---

## FOR CLAUDE CODE
Read THIS file top to bottom. Execute IN PROGRESS task first. Update log
when done. Move completed task to DONE. Move next TODO to IN PROGRESS.
NEVER create a new SRS file — always update THIS one in place.

---

## *** PIPELINE CORRECTION — MERGE FIRST, SYNC ONCE (2026-06-21) ***
## This OVERRIDES the step order below where they conflict. Read first.

PROBLEM (Vasil, 2026-06-21): the pipeline processed each camera-split MOV
as its own push. The 3rd video started at the coffee break, the footer
graph RESTARTED from zero, and the minimap dot jumped back to home. Each
segment was synced/positioned independently — WRONG. A push is ONE thing.

CORRECTED ORDER — assemble the whole push BEFORE processing:
 A. MERGE VIDEO FIRST. Concatenate all MOVs of the push into ONE video,
 in recording order. Only the FIRST segment carries the OSI-016 "Push
 Off" clapper. The merged video is one continuous timeline.
 B. MERGE ART FIRST. From 2026-06-21 Vasil does NOT stop WayTrace mid-push
 — he PAUSES/RESUMES, so normally ONE ART file per push with a large
 stationary GAP in the middle (the break — he wasn't moving, missing
 motion rows there are CORRECT). If >1 ART file exists, merge into one
 ART-MERGED-*.csv on the real timestamp timeline. Do NOT collapse or
 delete the pause gap.
 C. ONE GPX FOR THE PUSH. The single Strava GPX spans the whole push;
 during the break it sits in one place (correct). The minimap dot
 starts at the TRUE GPS START and moves continuously; never resets.
 D. FIND THE CLAPPER OFFSET ONCE on the merged whole; one
 video_to_art_offset for the entire push.
 E. THEN PROCESS the whole push as one continuous timeline (YOLO + GDPR
 blur + dashboard). Footer VDV graph is ONE continuous line; the dot is
 always at the correct real-world position.

NET RULE: one push = one merged video + one (merged) ART + one GPX, synced
ONCE on the clapper, processed as a single continuous timeline. Old
"concat-per-push" at the end is now effectively done at the START. Per-
segment independent sync/positioning is retired.

---

## *** PHYSICAL SETUP — NEW CHAIR, AXIS MAPPING VERIFIED 2026-06-24 ***
## Confirmed from test file ART-202606240047.csv (249 s: still, fwd, back,
## left turn, right turn, wheelie, second hard wheelie). SUPERSEDES the old
## "PHYSICAL SETUP — CONFIRMED" block (kept below as HISTORICAL).

NEW CHAIR: Küschall K-Series rigid wheelchair, Spinergy wheels, rear
anti-tip wheels fitted. Lighter than the old 15 kg chair — reweigh.
 - Caster diameter: Vasil said "24 inch" — UNUSUAL for front casters;
 MEASURE and confirm before trusting. CASTER_DIAMETER (new) = TO MEASURE.

NEW PHONE POSITION: in a NON-RIGID, HANGING POCKET on the wheelchair
BACKREST, at SEAT / body sitting height (NOT caster height). LANDSCAPE,
TOP EDGE of the phone to the RIGHT. Camera faces BACKWARD (good for Rear
Window). Screen faces direction of travel (forward). Sampling vibration at
the BODY's height — arguably MORE representative for ISO 2631-1 whole-body
exposure (note for the pitch), BUT the pocket HANGS and can swing — see
MOUNTING DYNAMICS below and OSI-023.

*** MOUNTING DYNAMICS — HANGING POCKET (not a rigid frame mount) ***
 Unlike the old caster strap (fairly rigidly coupled to the frame), the
 backrest pocket is non-rigid and HANGS. It has its own pendulum sway and
 resonance. So the sensor sees TWO things superimposed:
 (a) the wanted signal — terrain + chair vibration, and
 (b) PARASITIC pocket motion — the pocket swinging/oscillating on its own,
 which can ring on after a bump and inflate or smear the reading.
 PHYSICS: pocket sway is LOW frequency (pendulum, typically < ~2-3 Hz);
 the ISO 2631-1 health-relevant terrain band is higher (a few Hz up to
 tens of Hz). So they are largely SEPARABLE by frequency.
 HONESTY CONSEQUENCE (state it in the pitch): a hanging-pocket reading is
 a slightly noisier, less direct measure of frame vibration than the old
 caster mount. It still captures the road and is closer to body-height
 exposure, but the parasitic pocket motion MUST be filtered and that
 filtering DISCLOSED — otherwise a critic could say the numbers are
 inflated by the pocket swinging. Filter it AND say you filtered it.
 (Note: the existing OSI-006b Wk band-pass 0.4–100 Hz already removes
 some very-low-frequency sway, but the pocket pendulum frequency must be
 measured from real rolling data and filtered explicitly — see OSI-023.)

AXIS MAPPING — *** VERIFIED FROM DATA (2026-06-24) ***:
 At rest, the gravity vector sits at: X=0.56, Y=8.75, Z=4.32 (|g|=9.80).
 => VERTICAL IS STILL Y. (Y carries 89% of gravity at rest.)
 The phone rides TILTED BACK ~26° (that is the Z=4.32 component — the
 backrest recline angle), but Y remains the dominant up/down axis.

 CONFIRMED MAPPING (new chair, backrest pocket):
 - Y_accel = VERTICAL (− g). SAME AXIS AS THE OLD RIG.
 ==> waytrace_analysis.py needs NO vertical-axis change. ISO 8608 / VDV
 / RMS keep using Y_accel − g. (Earlier hypothesis that vertical would
 move to X was WRONG — the test disproved it. This is why we tested.)
 - X_accel = the WHEELIE / tip-back axis. PROOF: during both wheelies the
 gravity Y collapsed 8.75 → ~0.0 and shifted onto X (→9.17, then 9.65),
 i.e. the chair rotating backward rotates the phone about X. So pitch
 (wheelie) shows on the X channel.
 - Z_accel = the remaining horizontal axis (forward/back vs lateral are
 partly mixed across Z and the horizontal part of Y due to the ~26°
 tilt). For wheelie/roughness this does not matter (vertical=Y is clean).
 If forward-speed-from-IMU or lateral metrics are ever needed, fit the
 tilt from the resting gravity vector and rotate to world frame first.

 SAFETY EVENT IN THE TEST (2026-06-24): the SECOND wheelie was a real
 BACKWARD TIP-OVER — the anti-tip wheels had been loosened earlier that
 day by friends adjusting Vasil's centre of gravity and were not
 re-hardened, so they rotated through instead of catching. Vasil fell
 backward; the phone ended display-up. Signature: Y→0, big Z collapse,
 22.8 m/s² spike at t≈237.7 s. Vasil is OK; anti-tips since lowered
 (smaller tip angle) and to be re-hardened by Kosta. (This signature is
 a good labelled example for a future "backward tip / fall" detector.)

TILT CORRECTION (recommended, not blocking): de-rotate the ~26° using the
 resting gravity unit vector per recording for clean forward/lateral.
 Current Y−g is already valid for vertical roughness.

OPEN ITEMS (new chair):
 [ ] Real caster diameter (24" claimed — verify).
 [ ] New chair mass → new TOTAL_ROLLING_MASS_KG (was 78 w/ old 15 kg chair).
 [x] Vertical axis CONFIRMED = Y (2026-06-24).
 [x] Wheelie/pitch axis CONFIRMED = X.
 [x] OSI-023 v1: gravity-projected vertical landed in waytrace_analysis.py
     (commit pending) — mount-agnostic; degenerates to a_y−g on old data.
 [x] OSI-023 v2: pocket pendulum HP at 2.0 Hz for new-chair files
     (measured peak 1.07 Hz from K Push steady windows, 5.8× over terrain).
 [ ] OSI-023 v3: per-50m ISO 8608 (Stage-2 #53) so the class reflects
     the road, not the HP filter — needed to quote class on new-chair pushes.
 [ ] Re-tune GEOMETRY_FACTOR for the backrest hanging-pocket mount.
 [ ] Phone height from floor in the new pocket (for geometry).
 [ ] Re-harden anti-tip wheels (Kosta) before outdoor pushing.

## PHYSICAL SETUP — HISTORICAL (OLD CHAIR, pre-2026-06-22)
## Use THIS mapping to analyse OLD ART files (all before 2026-06-22).
Phone: RIGHT side, above front RIGHT caster. Portrait, vertical. Screen
faces rider. Camera end UP. Fabric strap pocket (compliant, >50 Hz atten).
Y_accel=VERTICAL, X_accel=FORWARD/BACK, Z_accel=LATERAL. Y_gyro=YAW,
X_gyro=ROLL, Z_gyro=PITCH. ISO 8608 vertical = Y_accel − g.
(NOTE: vertical axis Y is the SAME on the new chair — old and new files
both analyse with Y as vertical. Differences: forward/lateral split, the
~26° tilt, and the hanging-pocket parasitic motion (OSI-023).)

---

## FILE NAMING CONVENTION
ART-YYYYMMDDHHMM.csv — sensor data | ART-MERGED-YYYYMMDDHHMM.csv — merged
WT-YYYYMMDDHHMM.apk — app build | SRS-CURRENT.md — this file, permanent
RW-YYYYMMDDHHMM.mp4 — raw Rear Window video (Akaso V50 X)
RW-YYYYMMDDHHMM-blurred.mp4 — GDPR processed | -final.mp4 — annotated
RW-PUSH-YYYYMMDDHHMM-final.mp4 — one-file-per-push concat for upload
ANL-YYYYMMDDHHMM.txt — Analysis report | GPS-YYYYMMDDHHMM.gpx — Strava track
LOC-*.png/.txt — bad-spots map | RQM-*.png — road-quality polyline
BSV-*.mp4 — Beat-Synced Video

---

## MACHINES
VT — development: Android builds, Claude Code, sensor analysis.
VT-X1 (IP 10.0.0.110, DHCP) — GPU: video processing, YOLO, YouTube upload.

## PROJECT
App: WayTrace | Package: com.vasil.sensorlogger | Kotlin | Min SDK 26 |
Target SDK 34 | GitHub: github.com/vasil/WayTrace | Phone: Xiaomi —
120–125 Hz sustained (detected per-recording).

---

## WHEELCHAIR GEOMETRY — PARTIALLY CONFIRMED (OLD CHAIR — new chair TBD)
CASTER_DIAMETER_CM = 10.16 # old chair, 4" solid — CONFIRMED (new = TO MEASURE)
REAR_WHEEL_DIAMETER_CM = 60.0 # CONFIRMED (old)
REAR_WHEEL_PRESSURE_BAR = 8 # CONFIRMED (old)
WHEELBASE_LONGITUDINAL_CM = 32.0 ; CASTER_TRAIL_CM = 5.0 # old, CONFIRMED
PHONE_HEIGHT_CM = 35.0 # OLD caster mount — NEW pocket height TO MEASURE
PHONE_FORWARD_OFFSET_CM = 15.0 ; PHONE_LATERAL_OFFSET_CM = 3.0 # OLD
RIDER_WEIGHT_KG = 63.0 # CONFIRMED
CHAIR_WEIGHT_KG = 15.0 # OLD chair — new Küschall lighter, TO REWEIGH
TOTAL_ROLLING_MASS_KG = 78.0 # old (rider+old chair); update for new chair
PNEUMATIC_FACTOR = 1.47 # rear wheel absorption [REF-005]
GEOMETRY_FACTOR_CASTER_COMPLIANT = 2.0 # OLD caster-fork mount; new hanging
 # backrest pocket needs its own factor (TO RE-TUNE, see OSI-023)
# Remaining: seat height, track widths, weight balance, wheelie ref — TO MEASURE

---

## SCIENTIFIC REFERENCES
REF-001..010 — see prior SRS. Key: ISO 2631-1 (REF-007), ASTM E3028-16
WPRI (REF-008), Garcia-Mendez 2013 (REF-009), RFC OSI-ORIGINAL (REF-010).
REF-011: ISO 8608 road-roughness classes A–H, Gd(n₀) at n₀=0.1 cyc/m.

## CSV FORMAT — v3
Columns: timestamp_ms, sensor, x, y, z, rotvec_w. Rate 120–125 Hz
(per-device, runtime-detected). All event detection offline in Python.
Marker rows (pinpoint / sync_pulse) carry counter in col 3, zeros in 4-5.
Sensors present: accel, gravity, gyro, mag, rotvec.

## UI COLOR LANGUAGE — LOCKED
START GREY+GREEN | PAUSE GREEN+ORANGE | RESUME ORANGE+GREEN | STOP RED+WHITE
PIN BLUE+WHITE | SYNC CYAN+BLACK (clapper — OSI-016).

## EXTERNAL OUTREACH — STRAVA FEATURE REQUEST
DRAFT v2 at docs/strava-feature-request-2026-06-17.md. Proposed: Road
Surface Quality map type coloured by ISO 2631-1 Wk-weighted vertical RMS.

## FUNDING — POTENTIAL TARGETS
TO BE DRAFTED (one-page summary first). Targets: EDF, Mozilla, Wellcome,
OSM Foundation, Knight, NMK Ministry, RESNA.

---

## PER-PUSH PIPELINE — LOCKED ORDER (2026-06-19)
## Front-superseded by MERGE-FIRST (above): assemble one merged video +
## one ART + one GPX and sync ONCE before these stages.

 1. AUTO-FETCH on SD insert: mount, copy MOVs, verify, wipe SD; auto-fetch
 ART(s) from gdrive2 + Strava GPX via waytrace_strava.py. Then MERGE
 MOVs→one video and (if needed) ART→one ART-MERGED before sync.
 2. SYNC ART↔VIDEO (chime-locked): scan MERGED audio with
 tools/sync_chime_detect.py for the Push Off chime; match to first
 sync_pulse; compute ONE offset for the push.
 3. YOLO DETECTION PASS: boxes for car/truck/motorcycle/bus/person/bicycle/
 mobility-aid/stroller/obstacles. Identify BEFORE blurring. Assign STABLE
 TRACK IDs (for temporal blur persistence AND OSI-022 de-dup).
 4. TARGETED GDPR BLUR ("look harder inside the box, blur when uncertain"):
 focused low-threshold plate detect inside each vehicle box; focused face
 detect inside each person box. SAFETY BACKSTOP: no plate found → blur
 plate-likely lower zone anyway; forward-facing no face → blur head region
 anyway; person from behind = fine. Over-blur costs nothing; under-blur is
 the violation. TEMPORAL PERSISTENCE (OSI-021, hard req): blur bound to the
 TRACK across its whole life, through detector misses, padded before/after
 — no blinking. CONFIRMED WORKING 2026-06-21 (RW-200028-final.mp4: no
 flicker, 176 backstop cases hold, faces blurred).
 5. DASHBOARD HUD OVERLAY per OSI-007-DASHBOARD-SPEC.md: title+speed (upper
 left), route map+dot (upper right, borderline only), footer VDV trace +
 coloured background + VDV threshold line. MERGE-FIRST → footer is ONE
 continuous line; dot rides one GPX from true start. ISO 8608 class/"X→Y"
 flash uses DASHBOARD-SPEC Appendix A (50 m windows, time→space, FFT PSD,
 S_z=C·(Ω/Ω₀)^-2 fit). OSI-022 counter renders here too.
 6. CONCAT (now effectively done at START as MERGE): output one
 RW-PUSH-YYYYMMDDHHMM-final.mp4 per push.
 7. UPLOAD TO YOUTUBE (gated): youtube_upload.py, privacy=unlisted, behind
 UPLOAD_TO_YOUTUBE=1 — stays OFF until GDPR blur is provably airtight.

PHILOSOPHY: merge into one timeline first, sync once, then process.
Identify before redact. Lock sync before dashboard. Never re-encode after
concat (-c copy only). Upload is the final explicit gate.

---

## DASHBOARD QA BUNDLE (2026-06-21) — fix before next full batch
(from Vasil's VLC review of RW-200028-final.mp4)

QA-0. PREVIEW FRAMES FIRST — no full batch until previews approved.
 Render static 1920×1080 PNGs of the dashboard in EVERY visual state from
 REAL data at different timestamps, all four HUD layers composited as the
 pipeline produces them. Cover ALL visuals/texts/colours — do NOT cap the
 PNG count: footer GREEN/AMBER/RED; ISO 8608 class A,B,C,D… in the speed
 colour; multi-person frame with confidences; vehicle frame with
 confidence; a NO-detections frame; (when OSI-022 lands) ROAD vs PAVEMENT.
 Save to previews/. — Vasil: "do not count how many PNGs, let it be all
 the different visuals with all the different texts and colours."
QA-1. FOOTER METRIC = VDV (decided). Trace, label AND threshold all VDV
 (ISO 2631-1, Wk-weighted) — NOT plain RMS. RMS may stay as background
 tint only. Update DASHBOARD-SPEC LAYER 4.
QA-2. THRESHOLD LINE COLOUR distinct from the VDV trace (e.g. dashed bright
 cyan/magenta), labelled with the VDV high-risk value.
QA-3. THRESHOLD VALUE in VDV units (m/s^1.75), not the old RMS ~1.15 m/s².
QA-4. ENCODING BUG: "m/s??" → fix UTF-8/font so ² and ^1.75 render (or
 render exponent as plain text "m/s^1.75").
QA-5. FOOTER FONT too small — increase significantly.
QA-6. FOOTER LAYOUT: VDV trace should fill the band's vertical space.
QA-7. SPEED READOUT colour changes on ISO 8608 class change (title never
 changes colour — correct).
QA-8. TITLE/SPEED share one LEFT anchor.
QA-9. MINIMAP BORDER → DARK GRAY, borderline-only, no fill.

---

## CURRENT TASKS

### IN PROGRESS

**OSI-007 | Rear Window Video Pipeline — Phase-2 dashboard QA + MERGE-FIRST**
 HIGH. VT-X1. The 2026-06-18 push processed end-to-end; GDPR blur CONFIRMED
 working (no flicker). Remaining: (1) MERGE-FIRST correction; (2) DASHBOARD
 QA BUNDLE via PREVIEW FRAMES FIRST then a batch. YouTube gate OFF until
 OSI-021 temporal persistence formally accepted. Spec: OSI-007-DASHBOARD-
 SPEC.md (4 layers + Appendix A ISO 8608 + Appendix B clarifications).

**OSI-016 | WayTrace SYNC clapper — field-proven, working**
 2026-06-18 first field run: 4 chimes across 8 MOVs matched 4 sync_pulse
 rows. Audibility PASSED. Bug-fix APK WT-202606181442.apk behaving.
 FIELD METHOD: PAUSE/RESUME at breaks (one ART with stationary gap); one
 SYNC after START, one before STOP. NEW CHAIR: re-verify chime capture from
 the backrest hanging-pocket position on the first real new-chair push.

### TODO

**OSI-023 | Hanging-pocket parasitic-motion filtering (new-chair mount)**
 Added 2026-06-24. HIGH for new-chair data validity (BLOCKS trusting
 new-chair roughness numbers until done).
 PROBLEM: the backrest pocket is non-rigid and HANGS; it adds its own
 pendulum sway + resonance on top of the real terrain/chair vibration.
 Without removing it, VDV/roughness on the new chair can be inflated or
 smeared (the pocket rings on after a bump).
 METHOD:
 - From a real rolling segment (not the bench test), estimate the pocket
 PENDULUM frequency/band via FFT/PSD of the vertical (Y−g) signal during
 steady pushing on a KNOWN-smooth surface — the residual low-frequency
 peak that is NOT terrain is the pocket sway.
 - Add an explicit HIGH-PASS (or notch at the pendulum band) BEFORE the
 Wk weighting / VDV / ISO 8608 stages, tuned to remove that band while
 preserving the ISO 2631-1 health band (a few Hz to tens of Hz).
 - Cross-check against the gyro/gravity: pure pocket sway shows as slow
 orientation wobble with little true translational road content; use it
 to validate the cutoff.
 - Re-tune GEOMETRY_FACTOR for the hanging-pocket mount (the old caster
 2.0 does not apply); document the new factor with its justification.
 - DISCLOSE the filtering in outputs/pitch ("parasitic pocket motion
 removed by high-pass at X Hz") — honesty guard.
 ACCEPTANCE: on a known-smooth surface the post-filter VDV/roughness reads
 LOW (class A/B) as it should; a known-rough surface still reads high; the
 pocket-sway peak is gone from the post-filter PSD; same input → same
 output.

**OSI-024 | License-Plate Streak Tracker — GDPR-safe repeat-offender detector**
 Added 2026-06-27. MEDIUM — advocacy-grade evidence for cars that habitually
 occupy curb cuts / sidewalks. Builds on OSI-021 (the plate is already
 detected and tracked for blurring) and OSI-022 (per-vehicle accounting).

 GDPR HARD CONSTRAINT: the plate text NEVER appears on the rendered video.
 OCR result lives only in a local SQLite DB on VT-X1. Video output and
 anything uploaded to YouTube continue to show the plate fully blurred.
 The on-screen label changes only the *text*, not the blur.

 STORAGE:
 - SQLite at ~/waytrace-video/plates.db (gitignored; never synced).
 - Table `plates(plate_text PK, first_seen_date, last_seen_date,
     chair_lat_bin, chair_lon_bin, chair_heading_bin, ocr_conf_max,
     total_pushes)`.
 - Table `sightings(plate_text, push_ts, date, chair_lat, chair_lon,
     chair_heading_deg, ocr_conf, yolo_conf, bbox_xyxy)` — append-only;
     one row per detected track.
 - PLATE-LOCATION IDENTITY is derived from the CHAIR's pose, NOT a GPS
   on the car (we don't have one). The car's position is inferred from:
     (a) the chair's lat/lon at detection time (from GPX, via the
         video↔ART offset),
     (b) the chair's heading from `rotvec` (phone orientation), and
     (c) the camera's view direction (rear-facing on this rig).
   The "cluster" is therefore (chair_lat_bin ≈ 10 m, chair_lon_bin ≈ 10 m,
   chair_heading_bin = 45° octant) keyed with the plate text. Same
   plate parked in the same direction on the same stretch of street =
   same streak; same plate on a different street, or facing differently,
   is a separate streak. v2 may project a ray from the camera along the
   chair-heading vector and bin actual car positions; v1 keeps the
   cluster on chair pose to avoid distance-estimation error.

 OCR:
 - EasyOCR (GPU) on each plate crop AFTER YOLO confirms the box, BEFORE
   blur is applied. CPU fallback if no CUDA.
 - Lowest-cost normalisation: uppercase, strip whitespace and dashes,
   drop reads with confidence < 0.55 or fewer than 5 characters.
 - One OCR pass per TRACK (not per frame); aggregate by majority vote
   across the track's frames; final confidence = max single-frame conf.
 - Failed OCR → track still gets blurred, but contributes no DB row.

 STREAK DEFINITIONS:
 - daily_streak = max run of CONSECUTIVE calendar days the (plate,cluster)
   was sighted at least once. A missed day resets the run.
 - weekly_streak = number of CONSECUTIVE ISO weeks the (plate,cluster)
   was sighted at least once. A missed week resets.
 - Both computed at the end of each push from the sightings table.
 - Idempotency: a push reprocessed on the same calendar day does not
   increment the streak (DB enforces `UNIQUE(plate, date, lat_cluster,
   lon_cluster)` on sightings).

 ON-SCREEN LABEL (replaces YOLO text label only; box + blur unchanged):
 - daily_streak < 7  → standard YOLO label, e.g. `car 92%`. Streak hidden.
 - daily_streak ≥ 7  → enhanced label, e.g. `car · 12d 2w · 92%`
     ("12 days, 2 weeks", followed by YOLO confidence). Visible from week
     two onward and forever after — once a car earns a streak it keeps it.
 - PLATE TEXT IS NEVER IN THE LABEL. No exceptions.

 ACCEPTANCE:
 - First time a plate is seen → standard `car NN%` label; DB row created.
 - Reprocessing the same push → no double-count; streak unchanged.
 - Same plate, same curb, on day 1..7 → label stays plain car. On day 8 it
   flips to `car · 7d 1w · NN%`.
 - Spot-check 20 frames after a full push: ZERO plate characters visible
   anywhere in the rendered video.
 - DB survives a push and can be queried offline:
     `sqlite3 plates.db "SELECT plate_text, daily_streak FROM v_streaks
        ORDER BY daily_streak DESC LIMIT 20;"`

 OUT OF SCOPE for v1: cross-cluster reasoning (same plate roaming),
 per-week trend graphs, automated LTR generation from top streaks.

**OSI-022 | Forced-Road Counter — HUD live count + post-run advocacy report**
 HIGH (advocacy core). Vasil is legally a pedestrian; pushing on the
 carriageway is never by choice — either no pavement, or pavement above the
 ISO 2631-1 uncomfortable threshold. Count every vehicle and person on that
 carriageway as evidence.
 TIER 1 (YOLO COCO now): car/truck/motorcycle/bus/van = VEHICLES; person =
 PEDESTRIAN ON ROAD. TIER 2 (needs REAR-WINDOW P2): cane/crutch/walker,
 stroller/pram, other wheelchair/scooter = VULNERABLE ROAD USERS.
 COUNT ONLY on carriageway segments (P4 later; proxy until then: RMS above
 ISO threshold + GPS on a known road). DE-DUP by YOLO track ID.
 HUD (upper-left, below speed): "ROAD CAR 12 PERSON 4" (+ "MOBILITY 2
 STROLLER 1" at Tier 2). On pavement (future P4) counter PAUSES → PAVEMENT.
 POST-RUN FORCED ROAD EXPOSURE SUMMARY appended to ANL-*.txt — copy-paste
 ready for an OSI-LTR letter. ACCEPTANCE: counter updates; counts match
 spot-check; report in every ANL; no double-counting within a segment.

**OSI-021 | Refactor osi007_final into the new per-push pipeline order**
 (+ MERGE-FIRST). TODO → IN PROGRESS once current batch reviewed & Vasil
 greenlights. Split osi007_final.py into osi007_detect.py (emits track IDs)
 + osi007_blur.py (temporal persistence); add a merge stage at the front;
 re-wire tools/vtx1_phase2_batch.sh; osi007_dashboard.py takes the post-blur
 MERGED MP4.
 *** HARD REQUIREMENT — TEMPORAL BLUR PERSISTENCE (NO BLINKING) ***
 GDPR-critical; YouTube gate OFF until it passes. Blur bound to the TRACK
 not the frame: once detected on a track, blur EVERY frame of the track
 (carry/interpolate through misses, move with the car), pad ~0.5 s before
 first and after last; never-detected vehicle → backstop lower-zone blur;
 faces same on person tracks. ACCEPTANCE: frame-by-frame review shows ZERO
 readable plates / identifiable forward-facing faces on any track, incl.
 dropouts. Only then may UPLOAD_TO_YOUTUBE be set. (2026-06-21 VLC review
 shows this working; keep formal frame-by-frame acceptance as the gate.)

**OSI-011 | Stationary suppression** — min 1 row/sec stationary; full 120 Hz motion.
**OSI-012 | Beat-synced video** — waytrace_beatsync.py (librosa+MoviePy) → BSV-*.mp4.
**OSI-014 | Funding one-page summary** — see FUNDING.
**OSI-015 | Strava feature request email** — to developers@strava.com / Community Hub.

**REAR-WINDOW-NEXT-TASKS** (P1 absorbed into OSI-021):
 P2 Wider non-COCO model (bins, dumpsters, mobility aids, strollers), drop
 "bench". Enables OSI-022 Tier 2.
 P3 Vibration border overlay — ABSORBED into the OSI-007 dashboard footer.
 P4 Road-vs-pavement segmentation + map-matching — proper basis for OSI-022.

# RETIRED: OSI-017 (footer dashboard) — duplicate; its ISO 8608 objective is
# now DASHBOARD-SPEC Appendix A. Do not re-create; dashboard is part of OSI-007.

### DONE
OSI-001..006, 006b, 009, 010, 013 — see prior logs.
(OSI-019 UDP sonification confirmed BUILT per RECONCILIATION-LEDGER C-011,
commit 7770e19, 2026-05-18 — stays informal unless Vasil folds it in.)

---

## UPDATE LOG (latest first; older in prior revisions)
2026-06-27 — OSI-024 (license-plate streak tracker) added as TODO with
 GDPR-strict spec: plate text in local SQLite only, never on rendered
 video. Streak unlocks at ≥7 days; before that the YOLO label stays
 plain `car NN%`. Also: dashboard speed HUD switched from per-frame
 interpolation to a 1 Hz latch with GPS-dropout blank — Vasil reported
 the upper-left speed flickering 2+ times per second on the Soundless
 final; the old code recomputed `np.interp(t_art, gpx_t, speeds)` every
 frame against ~1 Hz GPX, so micro-jitter showed live. New code refreshes
 once per second of video time and shows "— km/h" when the nearest GPX
 sample is more than 3 s away. Code in tools/osi007_dashboard.py;
 mirrored to vt-x1:~/waytrace-video/. Does NOT apply to the
 mid-flight Three Summits Push batch (Python loaded old code at start);
 effective next push. Also: PHYSICAL field observation logged —
 pocket mount attenuates real signal (Three Summits = ISO class A on
 a three-summit climb), reinforces OSI-023 v3 priority and the push to
 frame-mount the phone. See memory: phone-mount-attenuation.

2026-06-24 (evening) — OSI-023 v1 LANDED in waytrace_analysis.py and
 real-push data REFUTES the bench-test "Y is vertical" generalisation:
 the new "K Push" (ART-202606240822.csv, 5.36 km Küschall shakedown)
 shows the resting gravity vector at X=-0.49, Y=+1.13, Z=+9.86
 (vertical=Z, screen-up flat in the pocket), then under push load
 (30–60 s) shifts to X=+9.46, Y=+0.13, Z=+2.89 (vertical=X — pocket
 swung 90° forward), then returns to vertical=Z when stopped. The
 bench test was held by hand; under real push load the pocket settles
 into a different orientation. CONCLUSION: no single fixed axis can
 be assumed vertical for the hanging-pocket mount — use the LIVE
 gravity vector (low-pass < 0.25 Hz) and project per-sample.
 IMPLEMENTED: compute_gravity_vertical(accel, fs) — projection onto
 unit gravity, |g| subtracted. wk_weighted_vertical() now consumes
 this; for old-chair (gravity steady on Y) the projection degenerates
 to a_y − g, so historical analyses are byte-equivalent.
 OSI-023 v2 (pendulum HP) ALSO LANDED: pocket pendulum peak measured
 at 1.07 Hz (5.8× above 3–8 Hz terrain reference) on today's steady
 windows. Wk band-pass low edge bumped 0.4 Hz → 2.0 Hz for new-chair
 files (filename ≥ 202606220000). Disclosed in ANL output. Effect on
 K Push: VDV_Wk 28.6 → 27.9 (≈unchanged, expected — VDV energy is
 mostly above 5 Hz where Wk peaks); ED 4.9 → 1.5 J/m (the pendulum
 was contributing ~70% of the "energy" reading); session ISO 8608
 E → A (the 2.0 Hz HP also strips real low-frequency road content
 from the whole-session fit — Stage-2 per-50m method [TODO #53] will
 fix this without losing terrain content). VDV remains the honest
 health-exposure metric on the new chair.
 NEXT: Stage-2 per-50m ISO 8608 (the SRS Appendix A method) so the
 class reflects the road, not the pendulum filter. Until then quote
 VDV (and ED post-filter) on new-chair pushes.
2026-06-24 — OSI-023 created: hanging-pocket parasitic-motion filtering.
 The new backrest mount is a NON-RIGID HANGING pocket (top edge to the
 right, screen forward, camera back). It adds pendulum sway/resonance
 (low frequency, < ~2-3 Hz) on top of the real terrain vibration. Must be
 measured from real rolling data and filtered (high-pass/notch before Wk/
 VDV/ISO 8608), GEOMETRY_FACTOR re-tuned, and the filtering DISCLOSED in
 outputs. Blocks trusting new-chair roughness numbers until done. Vertical
 channel itself is unaffected (Y still vertical, verified 2026-06-24).
2026-06-24 — NEW-CHAIR AXIS MAPPING VERIFIED from ART-202606240047.csv:
 VERTICAL IS STILL Y (resting gravity X=0.56 Y=8.75 Z=4.32; Y=89% of g;
 ~26° backrest tilt on Z). No vertical-axis code change needed. X_accel =
 wheelie/pitch axis. The earlier "vertical moves to X" hypothesis was
 DISPROVEN. Second wheelie was a real BACKWARD TIP-OVER (loosened anti-tips
 not re-hardened) — 22.8 m/s² spike at t≈237.7 s; Vasil OK; anti-tips
 lowered + to be re-hardened by Kosta.
2026-06-22 — PHYSICAL SETUP revised for the new Küschall chair + backrest
 pocket position (then hypothesised; verified 2026-06-24).
2026-06-21 — MERGE-FIRST pipeline correction. FIELD METHOD: pause/resume at
 breaks. DASHBOARD QA BUNDLE; footer = VDV; PREVIEW FRAMES FIRST. OSI-022
 created. GDPR blur CONFIRMED working.
2026-06-19 — Pipeline order LOCKED; SRS forks reconciled; ISO 8608 →
 DASHBOARD-SPEC Appendix A; OSI-017 retired; OSI-021 temporal persistence;
 DASHBOARD-SPEC Appendix B ("heart-rate monitor" = metaphor; footer plots
 VIBRATION, not heart rate).
2026-06-18 — OSI-016 SYNC clapper field-proven; DASHBOARD-SPEC written.
2026-06-17 — OSI-006b DONE.
