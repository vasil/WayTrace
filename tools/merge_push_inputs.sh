#!/bin/bash
# merge_push_inputs.sh — implement SRS PIPELINE CORRECTION 2026-06-21
#                        "MERGE FIRST, SYNC ONCE".
#
# Given a directory of camera-split MOVs (Akaso V50 X) and a directory
# of ART CSVs for ONE push, produce:
#   - ONE merged MOV (lossless ffmpeg concat -c copy)
#   - ONE merged ART CSV (if 2+ ART files; sorted by timestamp_ms,
#     pause gap preserved)
#
# Usage:
#   merge_push_inputs.sh \
#       --video-dir ~/Videos/VIDEO \
#       --art-dir   ~/waytrace-video/art \
#       --art-glob  'ART-2026062118*.csv,ART-2026062119*.csv' \
#       --push-ts   202606211835 \
#       --out-dir   ~/waytrace-video/push
#
# Outputs:
#   <out-dir>/RW-<push-ts>.MOV          (merged video, lossless)
#   <out-dir>/ART-MERGED-<push-ts>.csv  (merged ART; only if 2+ inputs)

set -euo pipefail

VIDEO_DIR=""; ART_DIR=""; ART_GLOB=""; PUSH_TS=""; OUT_DIR=""

while [ $# -gt 0 ]; do
    case "$1" in
        --video-dir) VIDEO_DIR="$2"; shift 2;;
        --art-dir)   ART_DIR="$2";   shift 2;;
        --art-glob)  ART_GLOB="$2";  shift 2;;
        --push-ts)   PUSH_TS="$2";   shift 2;;
        --out-dir)   OUT_DIR="$2";   shift 2;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done

[ -n "$VIDEO_DIR" ] && [ -n "$PUSH_TS" ] && [ -n "$OUT_DIR" ] \
    || { echo "usage: see header"; exit 2; }
mkdir -p "$OUT_DIR"

# ── 1) merge MOVs ────────────────────────────────────────────────────────
MERGED_MOV="$OUT_DIR/RW-${PUSH_TS}.MOV"
LIST="$(mktemp /tmp/merge_movs.XXXXXX.txt)"
shopt -s nullglob
movs=( "$VIDEO_DIR"/*.MOV )
shopt -u nullglob
if [ "${#movs[@]}" -eq 0 ]; then
    echo "no MOVs in $VIDEO_DIR" >&2; exit 1
fi
# Sort by name (camera-timestamp ordered already — 20240101_HHMMSS.MOV)
IFS=$'\n' movs=( $(printf '%s\n' "${movs[@]}" | sort) ); unset IFS
echo "merging ${#movs[@]} MOVs into $MERGED_MOV ..."
for m in "${movs[@]}"; do
    printf "file '%s'\n" "$m" >> "$LIST"
    echo "  + $(basename "$m")"
done
ffmpeg -y -hide_banner -loglevel error \
    -f concat -safe 0 -i "$LIST" -c copy "$MERGED_MOV"
rm -f "$LIST"
echo "  -> $(du -h "$MERGED_MOV" | cut -f1)"
dur=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$MERGED_MOV")
printf "  duration: %.1f min\n" "$(echo "$dur / 60" | bc -l)"

# ── 2) merge ART CSVs (only if 2+ matching) ─────────────────────────────
if [ -n "$ART_DIR" ] && [ -n "$ART_GLOB" ]; then
    # Resolve csv list from comma-separated globs
    csvs=()
    IFS=',' read -r -a globs <<< "$ART_GLOB"
    for g in "${globs[@]}"; do
        shopt -s nullglob
        for f in "$ART_DIR"/$g; do csvs+=( "$f" ); done
        shopt -u nullglob
    done
    if [ "${#csvs[@]}" -gt 1 ]; then
        MERGED_ART="$OUT_DIR/ART-MERGED-${PUSH_TS}.csv"
        echo "merging ${#csvs[@]} ART CSVs into $MERGED_ART ..."
        # Header from first file, rest sorted numerically by timestamp_ms (col 1).
        head -1 "${csvs[0]}" > "$MERGED_ART"
        # tail -n +2 drops the header from each input; sort merges on col 1.
        for f in "${csvs[@]}"; do tail -n +2 "$f"; done \
            | sort -t, -k1,1n >> "$MERGED_ART"
        rows_in=$(awk 'END{print NR-1}' <(cat "${csvs[@]}" \
            | awk 'NR==1{print; next} !/^timestamp_ms/{print}'))
        rows_out=$(awk 'END{print NR-1}' "$MERGED_ART")
        echo "  -> $rows_out merged rows  ($(du -h "$MERGED_ART" | cut -f1))"
    elif [ "${#csvs[@]}" -eq 1 ]; then
        echo "single ART file (${csvs[0]}); no merge needed"
        # Symlink for uniform downstream pathing
        ln -sf "${csvs[0]}" "$OUT_DIR/ART-MERGED-${PUSH_TS}.csv"
        echo "  -> symlinked to $OUT_DIR/ART-MERGED-${PUSH_TS}.csv"
    else
        echo "WARNING: no ART CSVs matched --art-glob '$ART_GLOB' in $ART_DIR" >&2
    fi
fi

echo "done."
