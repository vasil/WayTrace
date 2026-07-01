#!/bin/bash
# push_watch.sh — live watcher for MERGE-FIRST push batch on VT-X1.
# Progress bars for overall stage AND current frame counter.
# Refreshes every 3 s, exits when no batch is running. Ctrl-C to quit.

REFRESH=3
BAR=40

B='\033[1m'; D='\033[2m'; R='\033[0m'
RED='\033[31m'; YEL='\033[33m'; GRN='\033[32m'; CYN='\033[36m'; MAG='\033[35m'
BAR_F='█'; BAR_E='░'

trap 'printf "\033[?25h\n"; exit 0' INT TERM
printf '\033[?25l'

draw_bar() {
    # draw_bar <done> <total> <width> <color>
    local done=$1 total=$2 width=$3 color=$4
    [ "$total" -le 0 ] && total=1
    local filled=$(( done * width / total ))
    [ "$filled" -gt "$width" ] && filled=$width
    local empty=$(( width - filled ))
    printf '%b' "$color"
    for (( i=0; i<filled; i++ )); do printf '%s' "$BAR_F"; done
    printf '%b' "$D"
    for (( i=0; i<empty;  i++ )); do printf '%s' "$BAR_E"; done
    printf '%b' "$R"
}

human_dur() {
    local s=$1
    if [ "$s" -ge 3600 ]; then printf '%dh%02dm' $((s/3600)) $((s%3600/60))
    elif [ "$s" -ge 60 ]; then printf '%dm%02ds' $((s/60)) $((s%60))
    else printf '%ds' "$s"; fi
}

WAIT_START_EPOCH=$(date +%s)

while true; do
    BATCH_PID=$(pgrep -f vtx1_push_batch.sh | head -1)
    LATEST_LOG=$(ls -t ~/waytrace-video/push_batch_*.log 2>/dev/null | head -1)
    LOG_AGE=99999
    [ -n "$LATEST_LOG" ] && LOG_AGE=$(( $(date +%s) - $(stat -c %Y "$LATEST_LOG") ))

    # WAITING MODE: no running batch AND (no log OR latest log is stale).
    # Don't exit — poll so the user can fire the batch in another terminal
    # and the watcher latches on automatically.
    if [ -z "$BATCH_PID" ] && { [ -z "$LATEST_LOG" ] || [ "$LOG_AGE" -gt 120 ]; }; then
        WAIT_S=$(( $(date +%s) - WAIT_START_EPOCH ))
        printf '\033[H\033[J'
        printf '%bWAITING FOR PUSH BATCH%b   %s\n\n' "$YEL" "$R" "$(date +%H:%M:%S)"
        if [ -n "$LATEST_LOG" ]; then
            printf '%blast log:%b %s   (%s old)\n' \
                "$D" "$R" "$(basename "$LATEST_LOG")" "$(human_dur "$LOG_AGE")"
        else
            printf '%bno batch logs in ~/waytrace-video/ yet%b\n' "$D" "$R"
        fi
        printf '\n%bstart a new batch in another terminal, this watcher%b\n' "$D" "$R"
        printf '%bwill latch on automatically. waiting %s · Ctrl-C to quit%b\n' \
            "$D" "$(human_dur "$WAIT_S")" "$R"
        sleep $REFRESH
        continue
    fi

    PUSH_TS=$(grep -oP 'RW-\K\d{12}' "$LATEST_LOG" 2>/dev/null | head -1)

    printf '\033[H\033[J'
    printf '%bMERGE-FIRST PUSH BATCH WATCHER%b  push=%b%s%b  %s\n\n' \
        "$B" "$R" "$CYN" "$PUSH_TS" "$R" "$(date +%H:%M:%S)"

    # ── Step progression with completion glyphs ───────────────────────────
    # Stage states: 0 not started, 1 in progress, 2 done
    declare -A STATE
    for s in downscale detect blur dashboard; do STATE[$s]=0; done
    # Stages started: count [1/4]..[4/4]
    grep -oE '\[[1-4]/4\]' "$LATEST_LOG" 2>/dev/null | while read -r m; do
        case "$m" in
            '[1/4]') echo downscale ;; '[2/4]') echo detect ;;
            '[3/4]') echo blur ;;      '[4/4]') echo dashboard ;;
        esac
    done > /tmp/.pw_started
    for s in $(cat /tmp/.pw_started); do STATE[$s]=1; done
    # Stages done = those with a "completion" log line
    for s in downscale detect blur dashboard; do
        grep -qE "^\[.*\] +${s}:" "$LATEST_LOG" 2>/dev/null && STATE[$s]=2
    done
    # Check for [DONE] line
    grep -q '\[DONE' "$LATEST_LOG" 2>/dev/null && for s in downscale detect blur dashboard; do
        STATE[$s]=2
    done

    NUM_DONE=0
    printf '%bOverall:%b ' "$B" "$R"
    for s in downscale detect blur dashboard; do
        case "${STATE[$s]}" in
            2) printf '%b✔%b ' "$GRN" "$R"; NUM_DONE=$((NUM_DONE+1)) ;;
            1) printf '%b▶%b ' "$CYN" "$R" ;;
            0) printf '%b○%b ' "$D" "$R" ;;
        esac
        printf '%-10s' "$s"
    done
    printf '\n'
    draw_bar $NUM_DONE 4 $BAR "$GRN"
    printf '  %d/4 stages\n\n' "$NUM_DONE"

    # ── Current frame progress (within whichever inner log is fresh) ─────
    NOW=$(date +%s)
    for STAGE in dashboard blur detect; do
        case "$STAGE" in
            dashboard) F=~/waytrace-video/tmp/${PUSH_TS}_dash.log;   COLOR=$MAG ;;
            blur)      F=~/waytrace-video/tmp/${PUSH_TS}_blur.log;   COLOR=$YEL ;;
            detect)    F=~/waytrace-video/tmp/${PUSH_TS}_detect.log; COLOR=$CYN ;;
        esac
        [ -f "$F" ] || continue
        [ $(( NOW - $(stat -c %Y "$F") )) -gt 30 ] && continue

        printf '%bCurrent stage: %s%b\n' "$COLOR" "$STAGE" "$R"
        LINE=$(tail -1 "$F")
        # Extract "frame X/Y"
        XY=$(echo "$LINE" | grep -oE 'frame [0-9]+/[0-9]+' | head -1 | awk '{print $2}')
        X=${XY%/*}; Y=${XY#*/}
        if [[ "$X" =~ ^[0-9]+$ && "$Y" =~ ^[0-9]+$ && "$Y" -gt 0 ]]; then
            PCT=$(( X * 100 / Y ))
            draw_bar $X $Y $BAR "$COLOR"
            # Optional: extract fps and ETA if present
            FPS=$(echo "$LINE"  | grep -oE '[0-9.]+ fps'  | head -1 | awk '{print $1}')
            ETA=$(echo "$LINE"  | grep -oE 'ETA +[0-9]+s' | head -1 | awk '{print $2}')
            EXTRA=""
            [ -n "$FPS" ] && EXTRA="  $FPS fps"
            [ -n "$ETA" ] && EXTRA="$EXTRA  ETA $(human_dur ${ETA%s})"
            printf '  %d%% (%d/%d)%s\n' "$PCT" "$X" "$Y" "$EXTRA"
        else
            printf '%s\n' "$LINE"
        fi
        break
    done
    printf '\n'

    # ── Last few log events for context ───────────────────────────────────
    printf '%bLast events:%b\n' "$D" "$R"
    grep -E '\[[1-4]/4\]|(downscale|detect|blur|dashboard):|DONE' \
        "$LATEST_LOG" 2>/dev/null | tail -5

    if [ -z "$BATCH_PID" ]; then
        printf '\n%bBatch script not running.%b\n' "$YEL" "$R"
        FINAL=$HOME/Videos/VIDEO_dashboard/RW-PUSH-${PUSH_TS}-final.mp4
        if [ -f "$FINAL" ]; then
            SZ=$(du -h "$FINAL" | cut -f1)
            printf '%bFINAL READY:%b %s  %s\n' "$GRN" "$R" "$FINAL" "$SZ"
        fi
        printf '\033[?25h\n'
        exit 0
    fi

    printf '\n'
    GPU=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,temperature.gpu \
          --format=csv,noheader 2>/dev/null)
    printf '%bGPU:%b  %s   ' "$B" "$R" "${GPU:-n/a}"
    printf '%bDisk:%b %s\n' "$B" "$R" "$(df -h / | tail -1 | awk '{print $4" free ("$5")"}')"

    printf '\n%brefresh %ss · Ctrl-C to quit%b' "$D" "$REFRESH" "$R"
    sleep $REFRESH
done
