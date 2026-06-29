#!/bin/bash
# Concat all RW-*-final.mp4 files in $OUT_DIR into ONE per-push video for
# YouTube upload. Lossless (-c copy), so this is just an ffmpeg remux —
# completes in seconds regardless of clip length.
#
# Default inputs live in ~/Videos/VIDEO_dashboard/ (where the Phase-2
# batch deposits its outputs). Output goes to the same directory with
# the SRS naming convention RW-PUSH-<timestamp>-final.mp4.
#
# Usage:
#     concat_push.sh                       # auto-name from current date
#     concat_push.sh 202606181630          # use this timestamp in the name
#     OUT_DIR=/other/dir concat_push.sh    # override input/output dir

set -euo pipefail

OUT_DIR="${OUT_DIR:-$HOME/Videos/VIDEO_dashboard}"
ts="${1:-$(date +%Y%m%d%H%M)}"
FINAL="$OUT_DIR/RW-PUSH-${ts}-final.mp4"

shopt -s nullglob
parts=( "$OUT_DIR"/RW-*-final.mp4 )
# Strip out any previous PUSH concat so we don't recursively include it.
filtered=()
for p in "${parts[@]}"; do
    case "$(basename "$p")" in
        RW-PUSH-*) ;;
        *)         filtered+=("$p") ;;
    esac
done
if [ "${#filtered[@]}" -eq 0 ]; then
    echo "no per-segment RW-*-final.mp4 in $OUT_DIR" >&2
    exit 1
fi

# Sort by name (chronological since names are camera-timestamp ordered).
IFS=$'\n' parts=( $(printf '%s\n' "${filtered[@]}" | sort) )
unset IFS

listfile=$(mktemp /tmp/concat.XXXXXX.txt)
for p in "${parts[@]}"; do
    printf "file '%s'\n" "$p" >> "$listfile"
done

echo "concatenating ${#parts[@]} segments → $FINAL"
printf '  %s\n' "${parts[@]}"

# -f concat -safe 0 lets us use absolute paths.
# -c copy keeps the original h264 + AAC streams; no re-encode.
ffmpeg -y -hide_banner -loglevel error \
    -f concat -safe 0 -i "$listfile" \
    -c copy "$FINAL"

rm -f "$listfile"
echo
echo "wrote $FINAL ($(du -h "$FINAL" | cut -f1))"
echo "duration: $(ffprobe -v error -show_entries format=duration \
                  -of default=nw=1:nk=1 "$FINAL" | \
                  awk '{printf "%d min %d s\n", $1/60, $1%60}')"
