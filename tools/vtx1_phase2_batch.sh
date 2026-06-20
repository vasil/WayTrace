#!/bin/bash
# OSI-007 Phase-2 batch: 4K -> 1080p downscale + osi007_detect.py
# (YOLO tracking + plate/face detection inside boxes) + osi007_blur.py
# (TEMPORAL-PERSISTENT blur + colored YOLO boxes) +
# osi007_dashboard.py (HUD overlays).
#
# OSI-021 revision (2026-06-20): step 2 is split into detect + blur per
# the SRS HARD REQUIREMENT "temporal blur persistence (no blinking)".
# Plates and faces are now blurred for the ENTIRE lifespan of each
# tracked object, interpolating through detector dropouts and padding
# before/after the detection window — the per-frame independent blur
# of osi007_final.py is what caused the GDPR-leaking blink.
#
# Hardened revision (after the 2026-06-18 pandas-missing wipeout):
#   - PRE-FLIGHT import check up front: fail loud if any dependency
#     (cv2/numpy/pandas/scipy/ultralytics) is missing — no point burning
#     11 h on a pipeline that crashes silently late.
#   - Each step checks exit status; if any fails the intermediates are
#     KEPT (no cleanup) and we abort the loop for that MOV.
#   - Final cleanup only happens if the final file is present + non-empty.

set -uo pipefail

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate osi007

# ── PRE-FLIGHT ───────────────────────────────────────────────────────────
# Crash early if dependencies are missing, not 11 h in.
if ! python -c "import cv2, numpy, pandas, scipy, ultralytics" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] PRE-FLIGHT FAILED: missing cv2/numpy/pandas/scipy/ultralytics"
    python -c "import cv2, numpy, pandas, scipy, ultralytics"   # print the actual error
    exit 1
fi
echo "[$(date +%H:%M:%S)] pre-flight imports OK"

ROOT="$HOME/waytrace-video"
IN_DIR="$HOME/Videos/VIDEO"
OUT_DIR="$HOME/Videos/VIDEO_dashboard"
TMP_DIR="$ROOT/tmp"
LOG="$ROOT/phase2_batch.log"
ART_DIR="$ROOT/art"
GPX_DIR="$ROOT/art"

mkdir -p "$OUT_DIR" "$TMP_DIR"

SP=$(python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
NVLIB=""
for d in "$SP"/nvidia/*/lib; do [ -d "$d" ] && NVLIB="$d:$NVLIB"; done
export LD_LIBRARY_PATH="$NVLIB:${LD_LIBRARY_PATH:-}"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

T0=$(date +%s)
log "=== PHASE-2 BATCH START (hardened revision) ==="
log "input dir : $IN_DIR"
log "output dir: $OUT_DIR"

TITLE="ISO 8608 Class E Push"
GPX="$GPX_DIR/GPS-202606191651.gpx"

# 2026-06-19 push: 2 sessions, 7 camera-split MOVs (3 + 4).
# Offsets derived from sync_chime_detect.py against ART sync_pulse rows.
# Bookend drift was -0.62 s on session 1 and -1.69 s on session 2.
declare -A ART
declare -A OFFSET
ART[20240101_200028]=ART-202606191651.csv; OFFSET[20240101_200028]=-14.7
ART[20240101_201427]=ART-202606191651.csv; OFFSET[20240101_201427]=824.0
ART[20240101_202826]=ART-202606191651.csv; OFFSET[20240101_202826]=1662.6
ART[20240101_213448]=ART-202606191824.csv; OFFSET[20240101_213448]=17.9
ART[20240101_214847]=ART-202606191824.csv; OFFSET[20240101_214847]=856.5
ART[20240101_220246]=ART-202606191824.csv; OFFSET[20240101_220246]=1695.0
ART[20240101_221644]=ART-202606191824.csv; OFFSET[20240101_221644]=2533.6

BASES=(
    20240101_200028 20240101_201427 20240101_202826
    20240101_213448 20240101_214847 20240101_220246 20240101_221644
)
log "queued ${#BASES[@]} MOVs"

for base in "${BASES[@]}"; do
    SRC="$IN_DIR/${base}.MOV"
    if [ ! -f "$SRC" ]; then
        log "skip $base — source missing"; continue
    fi
    art_name="${ART[$base]}"
    offset="${OFFSET[$base]}"
    ART_PATH="$ART_DIR/$art_name"
    FINAL="$OUT_DIR/RW-${base#20240101_}-final.mp4"
    if [ -f "$FINAL" ] && [ "$(stat -c%s "$FINAL")" -gt 1000000 ]; then
        log "skip $base — $FINAL already exists ($(du -h "$FINAL" | cut -f1))"; continue
    fi

    DS="$TMP_DIR/${base}_1080p.mp4"
    DETECT_JSON="$TMP_DIR/${base}_detect.json"
    BLURRED="$TMP_DIR/${base}_consolidated.mp4"

    log "--- $base ---"
    log "  ART=$art_name  offset=${offset}s"
    t_a=$(date +%s)

    # 1) Downscale 4K -> 1080p (NVENC).
    log "[1/4] downscale 4K -> 1080p (h264_nvenc, 8 Mbps)"
    if ! ffmpeg -y -hide_banner -loglevel error \
        -hwaccel cuda -i "$SRC" \
        -vf "scale=1920:1080:flags=lanczos" \
        -c:v h264_nvenc -preset p4 -b:v 8M -pix_fmt yuv420p \
        -c:a copy "$DS"; then
        log "    DOWNSCALE FAILED — skipping $base"; continue
    fi
    t_b=$(date +%s)
    log "    downscale: $((t_b - t_a))s, $(du -h "$DS" | cut -f1)"

    # 2) osi007_detect.py — YOLO tracking + plate/face inside crops.
    log "[2/4] osi007_detect.py (YOLO + tracking + plate/face)"
    if ! python -u "$ROOT/osi007_detect.py" "$DS" "$DETECT_JSON" \
            > "$TMP_DIR/${base}_detect.log" 2>&1; then
        log "    DETECT FAILED — keeping intermediates, skipping $base"
        log "    see $TMP_DIR/${base}_detect.log"; continue
    fi
    t_c=$(date +%s)
    log "    detect:    $((t_c - t_b))s, $(du -h "$DETECT_JSON" 2>/dev/null | cut -f1) JSON"

    # 3) osi007_blur.py — temporal-persistent blur from track sidecar.
    log "[3/4] osi007_blur.py (temporal-persistent blur, NO BLINKING)"
    if ! python -u "$ROOT/osi007_blur.py" "$DS" "$DETECT_JSON" "$BLURRED" \
            > "$TMP_DIR/${base}_blur.log" 2>&1; then
        log "    BLUR FAILED — keeping intermediates, skipping $base"
        log "    see $TMP_DIR/${base}_blur.log"; continue
    fi
    t_d=$(date +%s)
    log "    blur:      $((t_d - t_c))s, $(du -h "$BLURRED" 2>/dev/null | cut -f1)"

    # 4) Dashboard HUD overlay.
    log "[4/4] dashboard HUD overlay"
    if ! python -u "$ROOT/osi007_dashboard.py" \
            --video "$BLURRED" \
            --art   "$ART_PATH" \
            --gpx   "$GPX" \
            --title "$TITLE" \
            --video-art-offset "$offset" \
            --out   "$FINAL" \
            > "$TMP_DIR/${base}_dash.log" 2>&1; then
        log "    DASHBOARD FAILED — keeping intermediates so we can resume"
        log "    see $TMP_DIR/${base}_dash.log"; continue
    fi
    t_e=$(date +%s)
    # Verify the final actually got written.
    if [ ! -f "$FINAL" ] || [ "$(stat -c%s "$FINAL")" -lt 1000000 ]; then
        log "    DASHBOARD produced no/empty output — keeping intermediates"; continue
    fi
    log "    dashboard: $((t_e - t_d))s, $(du -h "$FINAL" | cut -f1)"

    # 5) Optional: upload to YouTube (unlisted).
    # GATED OFF by default. Per SRS OSI-021 acceptance: stays OFF until
    # a frame-by-frame review of the OSI-021 output shows ZERO unblurred
    # plates and ZERO unblurred forward-facing faces. Flip explicitly:
    #     UPLOAD_TO_YOUTUBE=1 ~/waytrace-video/vtx1_phase2_batch.sh
    if [ "${UPLOAD_TO_YOUTUBE:-0}" = "1" ]; then
        log "[5/5] upload to YouTube (privacy=unlisted)"
        json_counts="$TMP_DIR/${base}_consolidated.json"
        if ! python -u "$ROOT/youtube_upload.py" \
                --video "$FINAL" \
                --title "$TITLE — $(date -d @"$t_e" +%Y-%m-%d)" \
                --privacy unlisted \
                --recorded "$(date -d @"$t_e" +%Y-%m-%d)" \
                ${json_counts:+--json-counts "$json_counts"} \
                > "$TMP_DIR/${base}_yt.log" 2>&1; then
            log "    UPLOAD FAILED — see $TMP_DIR/${base}_yt.log"
        else
            url=$(tail -1 "$TMP_DIR/${base}_yt.log")
            log "    uploaded -> $url"
        fi
    fi

    # Only now is it safe to clean.
    rm -f "$DS" "$DETECT_JSON" "$BLURRED"
    log "[DONE $base] total $((t_e - t_a))s -> $FINAL"
done

t_end=$(date +%s)
log "=== per-segment processing FINISHED — total $(printf '%dh%dm%ds' $((((t_end-T0)/3600))) $(((t_end-T0)/60%60)) $(((t_end-T0)%60))) ==="
log "per-segment outputs:"
ls -lh "$OUT_DIR" | tee -a "$LOG"

# ── Post-loop: concatenate all RW-*-final.mp4 segments into ONE per-push
# video for upload, per Vasil's "one logical push, one file" decision
# (2026-06-19). Strava activity = single session, so the camera-split
# segments are stitched back together. Lossless ffmpeg remux.
TS_PUSH="$(basename "$GPX" .gpx | sed -E 's/^GPS-//')"
PUSH_FINAL="$OUT_DIR/RW-PUSH-${TS_PUSH}-final.mp4"
if [ -x "$ROOT/concat_push.sh" ] && [ ! -f "$PUSH_FINAL" ]; then
    log "[concat] stitching segments -> RW-PUSH-${TS_PUSH}-final.mp4"
    if OUT_DIR="$OUT_DIR" "$ROOT/concat_push.sh" "$TS_PUSH" \
            >> "$LOG" 2>&1; then
        log "[concat] $(du -h "$PUSH_FINAL" 2>/dev/null | cut -f1) -> $PUSH_FINAL"
    else
        log "[concat] FAILED — per-segment files are still in $OUT_DIR"
    fi
fi

# ── Optional: upload the concatenated per-push video to YouTube. Same
# gate as the per-segment upload; runs only with UPLOAD_TO_YOUTUBE=1.
if [ "${UPLOAD_TO_YOUTUBE:-0}" = "1" ] && [ -f "$PUSH_FINAL" ]; then
    log "[concat upload] sending $PUSH_FINAL to YouTube (unlisted)"
    if ! python -u "$ROOT/youtube_upload.py" \
            --video "$PUSH_FINAL" \
            --title "$TITLE — $(date +%Y-%m-%d)" \
            --privacy unlisted \
            --recorded "$(date +%Y-%m-%d)" \
            > "$TMP_DIR/push_yt.log" 2>&1; then
        log "[concat upload] FAILED — see $TMP_DIR/push_yt.log"
    else
        log "[concat upload] $(tail -1 "$TMP_DIR/push_yt.log")"
    fi
fi
