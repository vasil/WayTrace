#!/usr/bin/env python3
"""Detect OSI-016 "Push Off" SYNC clapper chimes in a MOV's audio track.

The chime is a fixed 5-note sequence C5-E5-G5-A5-C6 (~1.8 s), played by the
WayTrace Android app on a SYNC tap. The Akaso V50 X dashcam mic records
it into the MOV audio track. Matching detected chimes against the ART CSV
sync_pulse rows gives the per-MOV video↔ART time offset, which is the
foundation for the OSI-007 Phase-2 dashboard.

Usage:
    sync_chime_detect.py INPUT.mov [INPUT2.mov ...]

Method:
    Direct template cross-correlation against the bundled chime WAV.
    1. Demux audio with ffmpeg → 22050 Hz mono PCM (matches the template's
       native sample rate so resampling drift is zero).
    2. Build the template = same chime, generated from the in-repo
       waytrace_theme.wav (downsampled 44.1 → 22.05 kHz) and normalised.
    3. Compute the normalised cross-correlation NCC(t) = corr(x[t..t+L], h)
       / (||x[t..t+L]|| * ||h||) using FFT-based convolution.
    4. Threshold NCC ≥ 0.20 — a very selective bar against road noise; the
       chime is loud and frequency-unique enough that a real hit scores
       well above this. Peak-pick with a 5 s min-distance.

Output:
    INPUT.sync_chimes.json next to each MOV (chimes_s = [t1, t2, …]).
"""
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path
import numpy as np
from scipy.signal import fftconvolve

TEMPLATE_WAV_REPO = (Path(__file__).resolve().parents[1]
                     / "app/src/main/res/raw/waytrace_theme.wav")
# Allow overriding the template path so this tool can run from a host
# where the full WayTrace repo isn't checked out — drop a copy of the
# WAV next to the script, or pass --template.
TEMPLATE_WAV_FALLBACK = Path(__file__).resolve().parent / "waytrace_theme.wav"
TEMPLATE_WAV = (TEMPLATE_WAV_REPO if TEMPLATE_WAV_REPO.exists()
                else TEMPLATE_WAV_FALLBACK)
FS_AUDIO     = 22050
NCC_THRESH   = 0.20
MIN_GAP_S    = 5.0


def demux_audio(mov_path: Path) -> np.ndarray:
    """Use ffmpeg to extract mono 22050 Hz s16le audio, return float32 in [-1,1]."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(mov_path),
        "-ac", "1", "-ar", str(FS_AUDIO), "-f", "s16le", "pipe:1",
    ]
    raw = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def load_template() -> np.ndarray:
    """Load waytrace_theme.wav, downsample to FS_AUDIO if needed, normalise."""
    with wave.open(str(TEMPLATE_WAV), "rb") as w:
        n_ch = w.getnchannels()
        sw   = w.getsampwidth()
        fs   = w.getframerate()
        n    = w.getnframes()
        raw  = w.readframes(n)
    assert sw == 2 and n_ch == 1, f"unexpected template format: {n_ch}ch {sw*8}-bit"
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    # Downsample from 44.1 → 22.05 kHz (assume integer ratio 2; otherwise
    # would need scipy.signal.resample_poly).
    if fs == 2 * FS_AUDIO:
        audio = audio[::2]
    elif fs != FS_AUDIO:
        raise SystemExit(f"template fs={fs}, expected {FS_AUDIO} or {2*FS_AUDIO}")
    audio -= audio.mean()
    return audio


def detect_chimes(audio: np.ndarray, template: np.ndarray):
    """Return list of (t_seconds, ncc_score) for each detected chime."""
    L = len(template)
    # Normalised cross-correlation via FFT-based convolution.
    # corr(x[t..t+L], h) = sum_i x[t+i] * h[i] = (x ⋆ flip(h))[t+L-1]
    h = template
    norm_h = np.sqrt(np.dot(h, h))
    if norm_h == 0:
        return []
    flipped = h[::-1]
    num = fftconvolve(audio, flipped, mode="full")
    # We want output at lags t = 0 .. len(audio)-L, which lives at
    # indices [L-1 .. L-1+len(audio)-L] in the "full" result.
    num = num[L - 1 : L - 1 + len(audio) - L + 1]

    # Sliding ||x[t..t+L]|| — rolling window L^2 norm via cumulative sum
    # of squares.
    sq = audio * audio
    cs = np.concatenate(([0.0], np.cumsum(sq, dtype=np.float64)))
    win_energy = cs[L : L + len(num)] - cs[: len(num)]
    win_norm   = np.sqrt(np.maximum(win_energy, 1e-12))

    ncc = num.astype(np.float64) / (win_norm * norm_h)
    # Peak-pick: threshold + min-distance.
    above = ncc >= NCC_THRESH
    if not above.any():
        return []
    picks = []
    last_t = -1e9
    min_gap = int(MIN_GAP_S * FS_AUDIO)
    # Walk through; collapse clusters of consecutive above-threshold samples
    # taking the local max.
    i = 0
    n = len(ncc)
    while i < n:
        if ncc[i] >= NCC_THRESH:
            j = i
            best = i
            while j < n and j - i < min_gap // 2 and ncc[j] >= NCC_THRESH * 0.5:
                if ncc[j] > ncc[best]:
                    best = j
                j += 1
            t = best / FS_AUDIO
            if t - last_t >= MIN_GAP_S:
                picks.append((float(t), float(ncc[best])))
                last_t = t
            i = j
        else:
            i += 1
    return picks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mov", nargs="+", help="One or more MOV files")
    ap.add_argument("--save-json", action="store_true",
                    help="Write INPUT.sync_chimes.json next to each MOV")
    global NCC_THRESH, TEMPLATE_WAV
    ap.add_argument("--threshold", type=float, default=NCC_THRESH,
                    help=f"NCC threshold (default {NCC_THRESH})")
    ap.add_argument("--template", type=Path, default=None,
                    help="Override path to waytrace_theme.wav")
    args = ap.parse_args()
    NCC_THRESH = args.threshold
    if args.template:
        TEMPLATE_WAV = args.template

    template = load_template()
    print(f"template: {len(template)} samples = {len(template)/FS_AUDIO:.2f} s "
          f"@ {FS_AUDIO} Hz   NCC threshold = {NCC_THRESH:.2f}", flush=True)

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
        picks = detect_chimes(audio, template)
        print(f"  detected chimes: {len(picks)}")
        for t, s in picks:
            print(f"    t = {t:7.2f} s   ({int(t//60):>3}m{t%60:05.2f}s)   "
                  f"NCC = {s:.3f}")
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
