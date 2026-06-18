#!/usr/bin/env python3
"""Detect OSI-016 "Push Off" SYNC clapper chimes in a MOV's audio track.

The chime is a fixed 5-note sequence C5-E5-G5-A5-C6 (~1.8 s), played by the
WayTrace Android app on a SYNC tap. The Akaso V50 X dashcam mic records
it into the MOV audio track. Matching detected chimes against the ART CSV
sync_pulse rows gives the per-MOV video↔ART time offset, which is the
foundation for the OSI-007 Phase-2 dashboard.

Usage:
    sync_chime_detect.py INPUT.mov [INPUT2.mov ...]
        Scans each MOV, prints detected chime timestamps (seconds into MOV).
        Optionally writes a sidecar JSON next to each MOV:
            INPUT.sync_chimes.json   = {"file": ..., "chimes_s": [t1, t2, ...]}

Method:
    1. Demux audio with ffmpeg → 22050 Hz mono PCM.
    2. Cross-correlate against the C5+E5+G5+A5+C6 expected note centres
       using a Goertzel-style narrow-band power envelope per note.
    3. Find rising-edge bursts of the C5→C6 sequence within 1.8±0.4 s
       windows. Score by how well all five note bands light up in order.
    4. Peak-pick scores above a threshold, with a 5 s min-distance to
       avoid double-detecting a single chime.

Designed to be robust against road noise (low-frequency rumble,
voices < ~800 Hz, wind) — the chime sits 523-1047 Hz, intentionally above
the noise floor of an urban push.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
import numpy as np

CHIME_NOTES_HZ = [523.25, 659.26, 783.99, 880.00, 1046.50]   # C5 E5 G5 A5 C6
NOTE_DUR_S     = [0.22, 0.22, 0.22, 0.50, 0.50]              # nominal
CHIME_TOTAL_S  = sum(NOTE_DUR_S) + 0.05 * (len(NOTE_DUR_S) - 1)  # ~1.66 s
FS_AUDIO       = 22050
ANALYSIS_HOP_S = 0.025      # 25 ms hop → analysis frame rate 40 Hz
ANALYSIS_WIN_S = 0.080      # 80 ms window per note-band power estimate
MIN_CHIME_GAP  = 5.0        # peaks closer than this collapse into one


def demux_audio(mov_path: Path) -> np.ndarray:
    """Use ffmpeg to extract mono 22050 Hz s16le audio, return float32 in [-1,1]."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(mov_path),
        "-ac", "1", "-ar", str(FS_AUDIO), "-f", "s16le", "pipe:1",
    ]
    raw = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return arr


def note_band_power(audio: np.ndarray, freq: float) -> np.ndarray:
    """Sliding-window narrow-band power around `freq`. Returns one value per
    analysis hop. Uses a coherent integrator (single-bin DFT) over a window
    of ANALYSIS_WIN_S — equivalent to a single Goertzel filter."""
    win = int(ANALYSIS_WIN_S * FS_AUDIO)
    hop = int(ANALYSIS_HOP_S * FS_AUDIO)
    n_hops = max(0, (len(audio) - win) // hop + 1)
    if n_hops <= 0:
        return np.zeros(0)
    # Pre-compute the complex exponential.
    k = np.arange(win)
    twid = np.exp(-2j * np.pi * freq * k / FS_AUDIO)
    # Sliding-window dot products.
    out = np.empty(n_hops, dtype=np.float32)
    # Window the audio (Hann) so leakage doesn't smear our peak.
    hann = 0.5 - 0.5 * np.cos(2 * np.pi * k / (win - 1))
    twid_h = twid * hann
    for i in range(n_hops):
        seg = audio[i * hop : i * hop + win]
        out[i] = np.abs(np.dot(seg, twid_h))
    # Normalise by window energy so silence ~ 0, full-sine peak ~ 1.
    return out / (win / 2.0)


def detect_chimes(audio: np.ndarray, mov_secs: float):
    """Scan audio, return list of (t_seconds, score) for each detected chime."""
    bands = [note_band_power(audio, f) for f in CHIME_NOTES_HZ]
    if not bands or bands[0].size == 0:
        return []
    bands = np.stack(bands)  # shape (5, n_hops)
    n_hops = bands.shape[1]
    times  = np.arange(n_hops) * ANALYSIS_HOP_S

    # Per-hop noise floor (median over 30-s window) — robust to traffic.
    win_hops = int(30.0 / ANALYSIS_HOP_S)
    if n_hops > 2 * win_hops:
        bg = np.zeros_like(bands)
        for b in range(bands.shape[0]):
            # rolling median via 1-D pool — cheap approx
            for i in range(0, n_hops, win_hops // 4):
                lo = max(0, i - win_hops // 2)
                hi = min(n_hops, i + win_hops // 2)
                bg[b, i:min(i + win_hops // 4, n_hops)] = \
                    float(np.median(bands[b, lo:hi]))
        # SNR
        snr = bands / np.maximum(bg, 1e-6)
    else:
        snr = bands / max(1e-6, float(np.median(bands)))

    # Template: each of the 5 notes should peak in order within ~1.8 s of
    # the chime's start. Compute, for every candidate start hop i, a score:
    # for each note k, take the max SNR of band k inside [i + note_start_k,
    # i + note_end_k]. Score = geometric mean of those 5 maxima.
    cum_starts = np.concatenate(([0.0], np.cumsum(NOTE_DUR_S)[:-1])) + \
                 0.05 * np.arange(len(NOTE_DUR_S))   # add small inter-note gap
    note_hop_starts = (cum_starts / ANALYSIS_HOP_S).astype(int)
    note_hop_lens   = (np.array(NOTE_DUR_S) / ANALYSIS_HOP_S).astype(int)
    chime_hops_len  = int(CHIME_TOTAL_S / ANALYSIS_HOP_S) + 4

    scores = np.zeros(max(0, n_hops - chime_hops_len), dtype=np.float32)
    for i in range(scores.size):
        per_note = np.empty(5, dtype=np.float32)
        for k in range(5):
            a = i + note_hop_starts[k]
            b = a + note_hop_lens[k]
            per_note[k] = snr[k, a:b].max() if b <= n_hops else 0
        # Geometric mean — every note must light up; one weak note → low score.
        # Guard against zeros.
        if per_note.min() <= 0:
            scores[i] = 0
        else:
            scores[i] = float(np.exp(np.mean(np.log(per_note))))

    # Peak-pick: threshold + min-distance.
    THRESH = 6.0   # SNR ≥ ~6 per note geomean — chime stands out vs noise
    above = scores >= THRESH
    if not above.any():
        return []
    picks = []
    last = -1e9
    # Scan in time order, taking the local max within a 1-s window per cluster.
    i = 0
    while i < scores.size:
        if scores[i] >= THRESH:
            j = i
            best = i
            while j < scores.size and (j - i) * ANALYSIS_HOP_S < 1.0:
                if scores[j] > scores[best]:
                    best = j
                j += 1
            t = best * ANALYSIS_HOP_S
            if t - last >= MIN_CHIME_GAP:
                picks.append((float(t), float(scores[best])))
                last = t
            i = j
        else:
            i += 1
    return picks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mov", nargs="+", help="One or more MOV files")
    ap.add_argument("--save-json", action="store_true",
                    help="Write INPUT.sync_chimes.json next to each MOV")
    args = ap.parse_args()

    for mov in args.mov:
        mov_path = Path(mov)
        if not mov_path.exists():
            print(f"[skip] {mov_path}: not found", file=sys.stderr); continue
        print(f"\n=== {mov_path.name} ===", flush=True)
        try:
            audio = demux_audio(mov_path)
        except subprocess.CalledProcessError as e:
            print(f"  ffmpeg failed: {e}", file=sys.stderr); continue
        secs = len(audio) / FS_AUDIO
        print(f"  audio: {secs:.1f} s, {len(audio):,} samples @ {FS_AUDIO} Hz")
        picks = detect_chimes(audio, secs)
        print(f"  detected chimes: {len(picks)}")
        for t, s in picks:
            print(f"    t = {t:7.2f} s   ({int(t//60):>3}m{t%60:05.2f}s)   "
                  f"score = {s:5.1f}")
        if args.save_json:
            out = mov_path.with_suffix(mov_path.suffix + ".sync_chimes.json")
            out.write_text(json.dumps(
                {"file": mov_path.name,
                 "duration_s": secs,
                 "chimes_s": [t for t, _ in picks],
                 "scores":   [s for _, s in picks]}, indent=2))
            print(f"  wrote {out.name}")


if __name__ == "__main__":
    main()
