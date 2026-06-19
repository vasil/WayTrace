#!/bin/bash
# OSI-007 Phase-2 batch watcher — in-place redraw, no flicker.
# Refreshes every 3 s; Ctrl-C to exit. Auto-closes when batch is done.

OUT_DIR="$HOME/Videos/VIDEO_dashboard"
LOG="$HOME/waytrace-video/phase2_batch.log"
TMP_DIR="$HOME/waytrace-video/tmp"
TOTAL=8
BAR=40
REFRESH=3

B='\033[1m'; D='\033[2m'; R='\033[0m'
RED='\033[31m'; YEL='\033[33m'; GRN='\033[32m'; CYN='\033[36m'; MAG='\033[35m'

BAR_FULL='█'; BAR_EMPTY='░'
PREV_LINES=0; FIRST=1

cleanup() { printf '\033[?25h'; printf '\n'; exit 0; }
trap cleanup INT TERM
printf '\033[?25l'

cpu_temp() {
    local t=""
    if command -v sensors >/dev/null 2>&1; then
        t=$(sensors 2>/dev/null | awk '
            /Package id 0:/ {gsub(/[+°C]/,"",$4); print $4; exit}
            /Tctl:/         {gsub(/[+°C]/,"",$2); print $2; exit}
            /Tdie:/         {gsub(/[+°C]/,"",$2); print $2; exit}
        ')
    fi
    if [ -z "$t" ]; then
        for f in /sys/class/thermal/thermal_zone*/temp; do
            [ -r "$f" ] || continue
            t=$(awk '{printf "%.1f", $1/1000}' "$f"); [ -n "$t" ] && break
        done
    fi
    echo "${t:-0}"
}
temp_color() {
    local t=${1%.*}
    if   [ "$t" -lt 55 ]; then printf '%s' "$GRN"
    elif [ "$t" -lt 75 ]; then printf '%s' "$YEL"
    else                       printf '%s' "$RED"; fi
}
draw_bar() {
    local done=$1 total=$2 width=$3 color=$4
    local filled=$((done * width / total))
    local empty=$((width - filled))
    printf '%b' "$color"
    for (( i=0; i<filled; i++ )); do printf '%s' "$BAR_FULL"; done
    printf '%b' "$D"
    for (( i=0; i<empty;  i++ )); do printf '%s' "$BAR_EMPTY"; done
    printf '%b' "$R"
}
human_dur() {
    local s=$1
    if [ "$s" -ge 3600 ]; then printf '%dh%02dm' $((s/3600)) $((s%3600/60))
    elif [ "$s" -ge 60 ]; then printf '%dm%02ds' $((s/60)) $((s%60))
    else                       printf '%ds' "$s"; fi
}
render() {
    local out="$1"
    local lines
    lines=$(printf '%s\n' "$out" | wc -l)
    if [ "$FIRST" -eq 0 ] && [ "$PREV_LINES" -gt 0 ]; then
        printf '\033[%dA' "$PREV_LINES"
    fi
    printf '%s\n' "$out" | sed $'s/$/\033[K/'
    FIRST=0; PREV_LINES=$lines
}

# Inner progress estimate for step 2 (osi007). Step 1 + 3 are quick.
file_progress() {
    local base=$1 step=$2
    case "$step" in
        2) local f="$TMP_DIR/${base}_consolidated.mp4.video.mp4"
           [ -f "$f" ] || { echo 0; return; }
           local sz=$(stat -c%s "$f" 2>/dev/null || echo 0)
           local pct=$(( sz * 100 / (2200 * 1024 * 1024) ))
           [ "$pct" -gt 100 ] && pct=100
           echo "$pct"
           ;;
        *) echo 0 ;;
    esac
}

T0=$(date +%s)
SPIN=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏'); SP_I=0

while true; do
    spinner=${SPIN[$SP_I]}; SP_I=$(((SP_I + 1) % ${#SPIN[@]}))

    done=$(ls -1 "$OUT_DIR"/*.mp4 2>/dev/null | wc -l)
    pct=$((done * 100 / TOTAL))
    elapsed=$(( $(date +%s) - T0 ))
    overall_bar=$(draw_bar "$done" "$TOTAL" "$BAR" "$CYN")

    cur='—'; step='—'; step_num=0
    if [ -f "$LOG" ]; then
        c=$(grep -E '^\[[0-9:]+\] --- ' "$LOG" | tail -1 \
            | sed -E 's/^\[[0-9:]+\] --- //; s/ ---$//')
        s=$(grep -E '^\[[0-9:]+\] \[[0-9]/3\] ' "$LOG" | tail -1 \
            | sed -E 's/^\[[0-9:]+\] //')
        [ -n "$c" ] && cur="$c"
        [ -n "$s" ] && step="$s"
        step_num=$(echo "$step" | grep -oE '\[[0-9]/3\]' | head -1 \
                    | tr -dc '0-9' | head -c1)
    fi

    if [ "$step_num" = "2" ] && [ "$cur" != "—" ]; then
        fp=$(file_progress "$cur" 2)
        fcolor=$GRN
        [ "$fp" -ge 50 ] && fcolor=$CYN
        [ "$fp" -ge 80 ] && fcolor=$MAG
        file_bar=$(draw_bar "$fp" 100 "$BAR" "$fcolor")
        file_pct=$(printf '%3d%%' "$fp")
    else
        idx=$(( SP_I % BAR ))
        file_bar=""
        for (( i=0; i<BAR; i++ )); do
            if [ $i -eq $idx ] || [ $i -eq $(((idx+1)%BAR)) ]; then
                file_bar="${file_bar}${CYN}${BAR_FULL}${R}"
            else
                file_bar="${file_bar}${D}${BAR_EMPTY}${R}"
            fi
        done
        file_pct=' ·· '
    fi

    gpu=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used \
            --format=csv,noheader,nounits 2>/dev/null | head -1)
    if [ -n "$gpu" ]; then
        IFS=', ' read -r gtemp gutil gmem <<<"$gpu"
        gcol=$(temp_color "$gtemp")
        gpu_line=$(printf "${gcol}%s°C${R}   util ${B}%s%%${R}   mem ${B}%s${R} MiB" \
                          "$gtemp" "$gutil" "$gmem")
    else
        gpu_line='(no nvidia-smi)'
    fi
    ctemp=$(cpu_temp); ccol=$(temp_color "$ctemp")
    cpu_line=$(printf "${ccol}%s°C${R}" "$ctemp")
    elapsed_str=$(human_dur "$elapsed")

    block=$(printf '%b\n' \
        "${B}${CYN}╭─ OSI-007 Phase-2 batch ───────────────────────────────────────${R}  $spinner $(date +%H:%M:%S)" \
        "${CYN}│${R}" \
        "${CYN}│${R}  ${B}Files${R}    $overall_bar  ${B}$done/$TOTAL${R} ($pct%)" \
        "${CYN}│${R}  ${B}Current${R}  $file_bar  $file_pct" \
        "${CYN}│${R}" \
        "${CYN}│${R}  Now      ${MAG}$cur${R}" \
        "${CYN}│${R}  Step     $step" \
        "${CYN}│${R}  Watching $elapsed_str" \
        "${CYN}│${R}" \
        "${CYN}│${R}  ${B}GPU${R}    $gpu_line" \
        "${CYN}│${R}  ${B}CPU${R}    $cpu_line" \
        "${B}${CYN}╰───────────────────────────────────────────────────────────────${R}" \
        "${D}Ctrl-C to exit${R}")

    render "$block"

    if ! pgrep -f vtx1_phase2_batch.sh >/dev/null && [ "$done" -ge "$TOTAL" ]; then
        printf '\n%b\n' "${B}${GRN}=== PHASE-2 BATCH COMPLETE ===${R}"
        cleanup
    fi
    sleep "$REFRESH"
done
