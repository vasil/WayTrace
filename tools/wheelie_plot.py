#!/usr/bin/env python3
"""
Wheelie timeline plot — pitch angle vs time with detected events highlighted.

Uses the gravity sensor to derive pitch relative to the chair's "neutral"
mount angle (median of the recording). A wheelie is a sustained positive
excursion above threshold.

Usage:
    python wheelie_plot.py ~/Downloads/ART-202605242125.csv [output.png]
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def detect_events(signal, t, threshold, min_dur_s=0.3, min_gap_s=1.0):
    above = signal > threshold
    events = []
    i = 0
    n = len(signal)
    while i < n:
        if above[i]:
            j = i
            while j < n and above[j]:
                j += 1
            dur = t[j-1] - t[i]
            if dur >= min_dur_s:
                events.append((t[i], t[j-1], signal[i:j].max(), dur))
            i = j
        else:
            i += 1
    merged = []
    for e in events:
        if merged and (e[0] - merged[-1][1]) < min_gap_s:
            s, _, pk, _ = merged[-1]
            merged[-1] = (s, e[1], max(pk, e[2]), e[1] - s)
        else:
            merged.append(e)
    return merged


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(2)
    csv_path = Path(argv[1]).expanduser()
    out_path = Path(argv[2]) if len(argv) > 2 else csv_path.with_name(
        csv_path.stem.replace("ART-", "WHL-") + ".png")

    df = pd.read_csv(csv_path)
    g = df[df["sensor"] == "gravity"].sort_values("timestamp_ms").reset_index(drop=True)
    if g.empty:
        sys.exit("no gravity rows")
    t_ms = g["timestamp_ms"].to_numpy()
    t = (t_ms - t_ms[0]) / 1000.0
    Xg = g["x"].to_numpy(dtype=float)
    Yg = g["y"].to_numpy(dtype=float)
    pitch = np.degrees(np.arctan2(Xg, Yg))
    neutral = float(np.median(pitch))
    pitch_rel = pitch - neutral

    THRESHOLDS = [(10, "C2", "lenient ≥10°"),
                  (15, "C1", "deliberate ≥15°"),
                  (20, "C3", "extreme ≥20°")]
    events_by_thr = {thr: detect_events(pitch_rel, t, threshold=thr)
                     for thr, _, _ in THRESHOLDS}

    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(t, pitch_rel, lw=0.5, color="black", label="pitch above neutral")
    ax.axhline(0, color="grey", lw=0.6, ls="--", alpha=0.6)

    # Threshold lines
    for thr, color, label in THRESHOLDS:
        ax.axhline(thr, color=color, lw=0.7, ls=":", alpha=0.7,
                   label=f"{label}: {len(events_by_thr[thr])} events")

    # Highlight ≥15° events as the canonical "real" wheelies
    for k, (s, e, pk, d) in enumerate(events_by_thr[15], 1):
        ax.axvspan(s, e, color="C1", alpha=0.2)
        # Label the event at its peak
        mid = (s + e) / 2
        ax.annotate(f"#{k}\n{d:.1f}s\n{pk:.0f}°",
                    xy=(mid, pk),
                    xytext=(mid, pk + 4),
                    ha="center", va="bottom", fontsize=8,
                    color="C1", fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color="C1", lw=0.6))

    span = t[-1]
    ax.set_xlim(0, span)
    ax.set_ylim(min(-15, pitch_rel.min() - 3), max(30, pitch_rel.max() + 8))
    ax.set_xlabel("time (s)")
    ax.set_ylabel("pitch above neutral (degrees, + = back-tilt / wheelie)")
    ax.set_title(f"Wheelie timeline — {csv_path.name}\n"
                 f"span {span:.0f} s   neutral mount = {neutral:+.1f}°   "
                 f"highlighted bands = ≥15° events ({len(events_by_thr[15])} total)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    print(f"wrote {out_path}")
    for k, (s, e, pk, d) in enumerate(events_by_thr[15], 1):
        print(f"  #{k}: {int(s//60):02d}:{s%60:05.2f}–{int(e//60):02d}:{e%60:05.2f}  "
              f"dur={d:5.2f}s  peak={pk:+5.1f}°")


if __name__ == "__main__":
    main(sys.argv)
