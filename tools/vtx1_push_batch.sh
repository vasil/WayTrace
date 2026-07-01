#!/bin/bash
# vtx1_push_batch.sh — push-level Phase-2 batch implementing the SRS
#                       PIPELINE CORRECTION 2026-06-21 "MERGE FIRST,
#                       SYNC ONCE".
#
# Inputs (CONFIG below):
#   PUSH_TS         — e.g. 202606211835 (Strava activity start, local)
#   MERGED_MOV      — path to the one merged push video (produced by
#                     tools/merge_push_inputs.sh)
#   ART_CSV         — path to the one ART CSV (merged if multi-session)
#   GPX             — path to the one Strava GPX
#   TITLE           — Strava activity title shown in the dashboard
#
# Pipeline (push-level, not per-segment):
#   [1/4]  downscale merged 4K -> 1080p (NVENC, lossy 1080p once)
#   [2/4]  osi007_detect.py on the whole merged push  → tracks.json
#   [3/4]  osi007_blur.py on the whole merged push    → consolidated.mp4
#                                                       (no-blink + boxes)
#   [4/4]  osi007_dashboard.py on the whole merged push with the SINGLE
#          video_to_art_offset derived ONCE from the chime near the start
#          of the merged video.
#
# Output:
#   $OUT_DIR/RW-PUSH-<push-ts>-final.mp4    (single file, ready for review)
#
# Per the SRS rule, no per-MOV split anywhere. Concat step is gone — it
# is effectively done UP FRONT in merge_push_inputs.sh.
#
# Same hardening as the old phase2 batch:
#   - PRE-FLIGHT import check
#   - Fail-fast per step; intermediates kept on failure so we can resume
#   - Cleanup only on success + non-empty output

set -uo pipefail

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate osi007

if ! python -c "import cv2, numpy, pandas, scipy, ultralytics" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] PRE-FLIGHT FAILED: missing cv2/numpy/pandas/scipy/ultralytics"
    python -c "import cv2, numpy, pandas, scipy, ultralytics"
    exit 1
fi
echo "[$(date +%H:%M:%S)] pre-flight imports OK"

# ── CONFIG (edit per push) ───────────────────────────────────────────────
PUSH_TS="${PUSH_TS:-202606211835}"
MERGED_MOV="${MERGED_MOV:-$HOME/waytrace-video/push/RW-${PUSH_TS}.MOV}"
ART_CSV="${ART_CSV:-$HOME/waytrace-video/push/ART-MERGED-${PUSH_TS}.csv}"
GPX="${GPX:-$HOME/waytrace-video/art/GPS-${PUSH_TS}.gpx}"
TITLE="${TITLE:-Rear Window Push}"

# OSI-2026-06-30: optional Strava activity photo. When set + readable,
# the dashboard zooms the mini-map to full screen over this photo for
# the time the GPX runs after the video EOF (camera died early).
PHOTO="${PHOTO:-}"
# OSI-2026-06-30: pause cut. When 1, ART time gaps > 30 s are treated
# as WayTrace pauses (coffee stop), and the dashboard hard-cuts past
# them with a brief 'paused N min' caption.
PAUSE_CUT="${PAUSE_CUT:-1}"

ROOT="$HOME/waytrace-video"
OUT_DIR="$HOME/Videos/VIDEO_dashboard"
TMP_DIR="$ROOT/tmp"
LOG="$ROOT/push_batch.log"

# OSI-024: three-stage rollout per [[feature-rollout-stages]] memory.
#   test     — DB writes only, NO visible label change (default for first push)
#   staging  — DB writes + enhanced label "car · Nd Mw · NN%" when streak ≥ 7
#   final    — locked-in; same as staging
#   off      — skip OCR + DB entirely (legacy path)
OSI024_STAGE="${OSI024_STAGE:-test}"
export OSI024_STAGE
log_osi024_header() {
    log "OSI024_STAGE=$OSI024_STAGE  (test=DB only, staging/final=label on)"
}

mkdir -p "$OUT_DIR" "$TMP_DIR"

SP=$(python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
NVLIB=""
for d in "$SP"/nvidia/*/lib; do [ -d "$d" ] && NVLIB="$d:$NVLIB"; done
export LD_LIBRARY_PATH="$NVLIB:${LD_LIBRARY_PATH:-}"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

T0=$(date +%s)
log "=== PUSH BATCH START (MERGE-FIRST) ==="
log "merged MOV: $MERGED_MOV"
log "ART CSV   : $ART_CSV"
log "GPX       : $GPX"
log "title     : $TITLE"
log "out dir   : $OUT_DIR"
log_osi024_header

for required in "$MERGED_MOV" "$ART_CSV" "$GPX"; do
    if [ ! -f "$required" ]; then
        log "MISSING INPUT: $required"
        exit 1
    fi
done

DS="$TMP_DIR/${PUSH_TS}_1080p.mp4"
DETECT_JSON="$TMP_DIR/${PUSH_TS}_detect.json"
DETECT_ENRICHED="$TMP_DIR/${PUSH_TS}_detect_osi024.json"
BLURRED="$TMP_DIR/${PUSH_TS}_consolidated.mp4"
FINAL="$OUT_DIR/RW-PUSH-${PUSH_TS}-final.mp4"

if [ -f "$FINAL" ] && [ "$(stat -c%s "$FINAL")" -gt 1000000 ]; then
    log "skip — $FINAL already exists ($(du -h "$FINAL" | cut -f1))"
    exit 0
fi

t_a=$(date +%s)

# [1/4] downscale the WHOLE merged MOV
log "[1/4] downscale 4K -> 1080p (h264_nvenc, 8 Mbps)"
if ! ffmpeg -y -hide_banner -loglevel error \
    -hwaccel cuda -i "$MERGED_MOV" \
    -vf "scale=1920:1080:flags=lanczos" \
    -c:v h264_nvenc -preset p4 -b:v 8M -pix_fmt yuv420p \
    -c:a copy "$DS"; then
    log "    DOWNSCALE FAILED — exit"; exit 1
fi
t_b=$(date +%s)
log "    downscale: $((t_b - t_a))s, $(du -h "$DS" | cut -f1)"

# [2/4] osi007_detect.py — YOLO tracking + plate/face inside crops
log "[2/4] osi007_detect.py (YOLO + tracking + plate/face)"
if ! python -u "$ROOT/osi007_detect.py" "$DS" "$DETECT_JSON" \
        > "$TMP_DIR/${PUSH_TS}_detect.log" 2>&1; then
    log "    DETECT FAILED — see $TMP_DIR/${PUSH_TS}_detect.log"; exit 1
fi
t_c=$(date +%s)
log "    detect:    $((t_c - t_b))s, $(du -h "$DETECT_JSON" | cut -f1) JSON"

# Derive video↔ART offset ONCE from the chime in the downscaled MOV.
# Timing is identical pre- and post-blur, so we do this here (before blur)
# so OSI-024 can use it.
OFFSET="${OFFSET:-}"
if [ -z "$OFFSET" ]; then
    log "[offset] deriving video_to_art_offset from chime in downscaled MOV"
    python -u "$ROOT/sync_chime_detect.py" --save-json "$DS" \
        > "$TMP_DIR/${PUSH_TS}_chime.log" 2>&1 || true
    CHIME_T=$(python -c "
import json, sys
from pathlib import Path
p = Path('$DS').with_suffix('.mp4.sync_chimes.json')
if not p.exists(): p = Path('$DS').with_suffix('.MOV.sync_chimes.json')
if p.exists():
    j = json.loads(p.read_text())
    cs = j.get('chimes_s') or []
    if cs:
        print(cs[0]); sys.exit(0)
print('NONE')
" 2>/dev/null)
    if [ "$CHIME_T" = "NONE" ] || [ -z "$CHIME_T" ]; then
        log "    NO CHIME FOUND in downscaled MOV — using offset=0 (visual sanity only)"
        OFFSET="0"
    else
        ART_T=$(python -c "
import csv
t0 = None
with open('$ART_CSV') as f:
    r = csv.reader(f); next(r)
    for row in r:
        if t0 is None: t0 = int(row[0])
        if row[1] == 'sync_pulse':
            print((int(row[0]) - t0) / 1000.0); break
")
        OFFSET=$(python -c "print($ART_T - $CHIME_T)")
        log "    chime at video=${CHIME_T}s ; sync_pulse at ART=${ART_T}s ; offset=${OFFSET}s"
    fi
fi

# [3/5] osi024_ocr.py — OCR plates, write sightings to plates.db,
#                       emit enriched detect.json. Skipped when stage=off.
BLUR_DETECT="$DETECT_JSON"
if [ "$OSI024_STAGE" != "off" ]; then
    log "[3/5] osi024_ocr.py (EasyOCR + plates.db, stage=$OSI024_STAGE)"
    if ! python -u "$ROOT/osi024_ocr.py" \
            --video      "$DS" \
            --detect-in  "$DETECT_JSON" \
            --detect-out "$DETECT_ENRICHED" \
            --art        "$ART_CSV" \
            --gpx        "$GPX" \
            --offset     "$OFFSET" \
            --push-ts    "$PUSH_TS" \
            --stage      "$OSI024_STAGE" \
            > "$TMP_DIR/${PUSH_TS}_osi024.log" 2>&1; then
        log "    OSI024 FAILED — see $TMP_DIR/${PUSH_TS}_osi024.log"
        log "    proceeding with non-enriched detect.json (degraded)"
    else
        BLUR_DETECT="$DETECT_ENRICHED"
        t_x=$(date +%s)
        log "    osi024:    $((t_x - t_c))s -> $(du -h "$DETECT_ENRICHED" | cut -f1)"
    fi
else
    log "[3/5] osi024_ocr.py SKIPPED (OSI024_STAGE=off)"
fi

# [4/5] osi007_blur.py — temporal-persistent blur, NO BLINKING
log "[4/5] osi007_blur.py (temporal-persistent blur, NO BLINKING)"
if ! OSI024_STAGE="$OSI024_STAGE" python -u "$ROOT/osi007_blur.py" \
        "$DS" "$BLUR_DETECT" "$BLURRED" \
        > "$TMP_DIR/${PUSH_TS}_blur.log" 2>&1; then
    log "    BLUR FAILED — see $TMP_DIR/${PUSH_TS}_blur.log"; exit 1
fi
t_d=$(date +%s)
log "    blur:      $((t_d - t_c))s, $(du -h "$BLURRED" | cut -f1)"

# [5/5] osi007_dashboard.py — single offset for the whole push
log "[5/5] dashboard HUD overlay"
# osi007_dashboard.py takes piecewise spec via --offsets and single
# float via --video-art-offset. Pick the right flag based on OFFSET shape.
if [[ "$OFFSET" == *","* ]]; then
    OFFSET_ARG=( --offsets "$OFFSET" )
else
    OFFSET_ARG=( --video-art-offset "$OFFSET" )
fi
EXTRA_ARGS=()
if [ -n "$PHOTO" ] && [ -f "$PHOTO" ]; then
    EXTRA_ARGS+=( --photo "$PHOTO" --extend-to-gpx-end )
    log "    photo tail: $(basename "$PHOTO") (zoom-to-fullscreen after video EOF)"
fi
if [ "$PAUSE_CUT" = "1" ]; then
    EXTRA_ARGS+=( --pause-cut )
    log "    pause cut: ON (ART gaps > 30 s removed from output)"
fi
if ! python -u "$ROOT/osi007_dashboard.py" \
        --video "$BLURRED" \
        --art   "$ART_CSV" \
        --gpx   "$GPX" \
        --title "$TITLE" \
        "${OFFSET_ARG[@]}" \
        "${EXTRA_ARGS[@]}" \
        --out   "$FINAL" \
        > "$TMP_DIR/${PUSH_TS}_dash.log" 2>&1; then
    log "    DASHBOARD FAILED — see $TMP_DIR/${PUSH_TS}_dash.log"; exit 1
fi
t_e=$(date +%s)
if [ ! -f "$FINAL" ] || [ "$(stat -c%s "$FINAL")" -lt 1000000 ]; then
    log "    DASHBOARD produced no/empty output — intermediates kept"; exit 1
fi
log "    dashboard: $((t_e - t_d))s, $(du -h "$FINAL" | cut -f1)"

# Cleanup
rm -f "$DS" "$DETECT_JSON" "$DETECT_ENRICHED" "$BLURRED"
log "[DONE $PUSH_TS] total $((t_e - t_a))s -> $FINAL"

# Disk hygiene: gzip the ART CSV we just used. Plain CSV compresses ~10×.
# If ART_CSV is a symlink (e.g. ART-MERGED-* → ART-*), we gzip the real
# file and replace the symlink so it still points at something readable.
art_real=$(readlink -f "$ART_CSV" 2>/dev/null || echo "$ART_CSV")
if [ -f "$art_real" ] && [[ "$art_real" != *.gz ]]; then
    sz_before=$(stat -c%s "$art_real")
    gzip -9 "$art_real"
    if [ -L "$ART_CSV" ]; then
        ln -sf "$art_real.gz" "${ART_CSV}.gz"
        rm -f "$ART_CSV"
    fi
    sz_after=$(stat -c%s "$art_real.gz")
    log "[zip] ART CSV  $((sz_before / 1024 / 1024))MB -> $((sz_after / 1024 / 1024))MB"
fi

# Optional YouTube upload (gated)
if [ "${UPLOAD_TO_YOUTUBE:-0}" = "1" ]; then
    log "[upload] sending $FINAL to YouTube (privacy=unlisted)"
    json_counts="$(dirname "$BLURRED")/$(basename "$BLURRED" .mp4).json"
    if ! python -u "$ROOT/youtube_upload.py" \
            --video "$FINAL" \
            --title "$TITLE — $(date -d @"$t_e" +%Y-%m-%d)" \
            --privacy unlisted \
            --recorded "$(date -d @"$t_e" +%Y-%m-%d)" \
            ${json_counts:+--json-counts "$json_counts"} \
            > "$TMP_DIR/${PUSH_TS}_yt.log" 2>&1; then
        log "    UPLOAD FAILED — see $TMP_DIR/${PUSH_TS}_yt.log"
    else
        url=$(tail -1 "$TMP_DIR/${PUSH_TS}_yt.log")
        log "    uploaded -> $url"
    fi
fi

t_end=$(date +%s)
log "=== PUSH BATCH FINISHED — total $(printf '%dh%dm%ds' $((((t_end-T0)/3600))) $(((t_end-T0)/60%60)) $(((t_end-T0)%60))) ==="
