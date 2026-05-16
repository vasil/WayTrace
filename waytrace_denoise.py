#!/usr/bin/env python3
"""
WayTrace Denoise — clean up helmet-camera audio noise from MOV/MP4 files.

The Akaso V50 X (and any action camera on a wheelchair) picks up:
  - wind rumble below ~150 Hz
  - caster-wheel rolling noise and frame vibration in the low mids
  - rim/gloves handling noise in the highs
  - general road ambience

This script has five modes:

  --analyze
        Read the audio, plot its spectrum, list the dominant frequency
        bands and any narrow peaks. Tells you what's actually in the file
        before you filter.

  (default — no flag)
        General-purpose clean-up using ffmpeg's built-in filters:
          highpass=80 Hz  → removes wind rumble
          afftdn          → spectral noise reduction (no profile needed)
          loudnorm        → broadcast-standard loudness
        Works without any prior knowledge of the noise. Less aggressive
        than profile-based modes but a safe default.

  --target-caster
        Preset tuned for wheelchair caster / frame mechanical noise:
          highpass=150 Hz (kills wind + wobble-induced low-freq rumble)
          notches at 200 Hz and 400 Hz (typical caster bearing rumble)
          stronger afftdn
        Tune the notches with the output of --analyze on your specific
        camera+chair combination. Combinable with --notch.

  --notch F1,F2,...
        Add specific frequencies to notch out (e.g. 200,400,820).
        Useful when --analyze reveals discrete tones. Can be combined
        with --target-caster to extend its built-in notches.

  --profile-from MM:SS-MM:SS
        Use the specified time range as a "this is just noise" profile.
        sox builds a noise spectrum from it, then subtracts that from the
        full audio. Best when you have a section where you're stopped or
        somewhere quiet. Cleanest result for known-stationary noise.
        Requires sox (apt install sox).

Video stream is copied as-is — no re-encoding, no quality loss on the
picture. Audio is replaced with the cleaned version.

Requires: ffmpeg, ffprobe. Additionally: sox (only for --profile-from).

Usage:
    python3 waytrace_denoise.py INPUT.MOV --analyze
    python3 waytrace_denoise.py INPUT.MOV                      # general clean-up
    python3 waytrace_denoise.py INPUT.MOV --target-caster
    python3 waytrace_denoise.py INPUT.MOV --notch 220,440
    python3 waytrace_denoise.py INPUT.MOV --profile-from 0:05-0:08

See DENOISE.md for the full manual.
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, capture=True, check=True):
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        sys.exit(f"Failed: {' '.join(map(str, cmd))}\n{r.stderr}")
    return r


def check_tools(need_sox: bool = False):
    required = ['ffmpeg', 'ffprobe'] + (['sox'] if need_sox else [])
    for t in required:
        if subprocess.run(['which', t], capture_output=True).returncode != 0:
            hint = "sudo apt install ffmpeg" + (" sox" if need_sox else "")
            sys.exit(f"Missing tool: {t}\nInstall with: {hint}")


def parse_ts(s: str) -> float:
    """Parse 'MM:SS', 'M:SS', 'SS', or 'MM:SS.fff' into seconds."""
    if ':' in s:
        parts = s.split(':')
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return float(s)


def analyze(input_path: Path):
    """Show audio spectrum and the dominant frequencies."""
    import numpy as np
    from scipy import signal as sp_signal
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        # Mono 22.05 kHz is plenty for spectrum analysis of action-cam audio
        run(['ffmpeg', '-y', '-loglevel', 'error',
             '-i', str(input_path), '-vn', '-ac', '1', '-ar', '22050',
             '-acodec', 'pcm_s16le', str(wav)])

        # Read with scipy
        from scipy.io import wavfile
        fs, x = wavfile.read(wav)
        x = x.astype(np.float64) / 32768.0

    duration = len(x) / fs
    print(f"Audio: {duration:.1f} s @ {fs} Hz, mono")

    # Welch PSD
    f, P = sp_signal.welch(x, fs=fs, nperseg=8192, noverlap=4096)

    # Quietest 2-second window — useful for --profile-from
    win_n = int(2.0 * fs)
    if len(x) > win_n:
        rms_per_sec = []
        step = int(0.5 * fs)
        for i in range(0, len(x) - win_n, step):
            seg = x[i:i + win_n]
            rms_per_sec.append((i / fs, float(np.sqrt(np.mean(seg ** 2)))))
        rms_per_sec.sort(key=lambda r: r[1])
        q_start = rms_per_sec[0][0]
        print(f"Quietest 2-s window starts at {int(q_start)//60:02d}:{int(q_start)%60:02d}"
              f" (RMS {rms_per_sec[0][1]:.4f})")
        print(f"  → consider: --profile-from {int(q_start)//60}:{int(q_start)%60:02d}-"
              f"{int(q_start+2)//60}:{int(q_start+2)%60:02d}")

    # Band power
    bands = [
        ("sub-bass / wind rumble", 20,    80),
        ("bass / frame vibration", 80,   250),
        ("low-mid / caster hum",  250,   500),
        ("mid / speech",          500,  2000),
        ("high-mid / rim noise", 2000,  5000),
        ("high / ambience",      5000, 10000),
    ]
    print("\nBand energy:")
    total = float(np.trapezoid(P, f))
    for name, lo, hi in bands:
        mask = (f >= lo) & (f <= hi)
        e = float(np.trapezoid(P[mask], f[mask])) if mask.any() else 0.0
        print(f"  {lo:>5}-{hi:<5} Hz  {name:<25}  {100*e/total:5.1f}% of total energy")

    # Find narrow peaks (mechanical-resonance candidates)
    log_P = 10 * np.log10(P + 1e-12)
    from scipy.ndimage import median_filter
    df_bin = f[1] - f[0]
    base_win = max(int(round(20.0 / df_bin)), 5)   # ~20 Hz local median window
    baseline = median_filter(log_P, size=base_win)
    above = log_P - baseline
    peaks, _ = sp_signal.find_peaks(above, height=5.0,
                                     distance=max(int(round(20.0 / df_bin)), 3))
    peaks = peaks[(f[peaks] >= 60) & (f[peaks] <= 5000)]
    print("\nNarrow peaks (≥5 dB above local baseline, 60–5000 Hz):")
    if len(peaks) == 0:
        print("  none — noise is broadband, no obvious mechanical resonance")
    else:
        for p in peaks[:15]:
            print(f"  {f[p]:6.1f} Hz   +{above[p]:.1f} dB")

    # Plot
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.semilogy(f, P, lw=1.2, color='#0070c0')
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Power spectral density")
    ax.set_xlim(20, min(fs / 2, 8000))
    ax.set_title(f"Audio spectrum — {input_path.name}")
    ax.grid(True, which='both', alpha=0.3)
    for p in peaks[:8]:
        ax.axvline(f[p], color='red', ls=':', alpha=0.5)
        ax.annotate(f"{f[p]:.0f}", (f[p], P[p]), xytext=(2, 5),
                    textcoords='offset points', fontsize=9, color='red')
    # Save next to the input if writable, else fall back to ~/Downloads
    primary = input_path.parent / f"{input_path.stem}-spectrum.png"
    try:
        plt.savefig(primary, dpi=130, bbox_inches='tight')
        out_png = primary
    except OSError:
        out_png = Path.home() / 'Downloads' / f"{input_path.stem}-spectrum.png"
        plt.savefig(out_png, dpi=130, bbox_inches='tight')
    print(f"\nSpectrum plot: {out_png}")


def denoise_with_profile(input_path: Path, output_path: Path,
                          profile_range: str, strength: float):
    """Use sox profile-based noise subtraction."""
    m = re.match(r"^\s*([\d:.]+)\s*-\s*([\d:.]+)\s*$", profile_range)
    if not m:
        sys.exit(f"--profile-from must look like MM:SS-MM:SS, got: {profile_range}")
    t_start, t_end = parse_ts(m.group(1)), parse_ts(m.group(2))
    if t_end <= t_start:
        sys.exit("Profile range must be increasing.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        full_wav  = tmp / "full.wav"
        noise_wav = tmp / "noise.wav"
        prof      = tmp / "noise.prof"
        clean_wav = tmp / "clean.wav"

        # Extract full audio
        run(['ffmpeg', '-y', '-loglevel', 'error',
             '-i', str(input_path), '-vn', '-acodec', 'pcm_s16le', str(full_wav)])
        # Extract just the noise sample window
        run(['ffmpeg', '-y', '-loglevel', 'error',
             '-ss', str(t_start), '-t', str(t_end - t_start),
             '-i', str(input_path), '-vn', '-acodec', 'pcm_s16le', str(noise_wav)])
        # Profile + reduce
        run(['sox', str(noise_wav), '-n', 'noiseprof', str(prof)])
        run(['sox', str(full_wav), str(clean_wav), 'noisered', str(prof), f"{strength:.3f}"])

        # Mux cleaned audio back with original video
        run(['ffmpeg', '-y', '-loglevel', 'error',
             '-i', str(input_path), '-i', str(clean_wav),
             '-map', '0:v', '-map', '1:a',
             '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
             str(output_path)])

    print(f"Cleaned: {output_path}")


def denoise_general(input_path: Path, output_path: Path):
    """ffmpeg-only general clean-up. No profile needed."""
    af = (
        "highpass=f=80,"            # kill wind rumble
        "lowpass=f=11000,"          # roll off ultrasonic noise
        "afftdn=nr=18:nf=-25,"      # FFT-based spectral noise reduction
        "loudnorm=I=-16:LRA=11:TP=-1.5"  # broadcast-loudness normalisation
    )
    run(['ffmpeg', '-y', '-loglevel', 'warning',
         '-i', str(input_path),
         '-af', af,
         '-c:v', 'copy',
         '-c:a', 'aac', '-b:a', '192k',
         str(output_path)])
    print(f"Cleaned (general defaults): {output_path}")


def denoise_caster(input_path: Path, output_path: Path, extra_notches=None):
    """Preset aimed at wheelchair caster / frame mechanical noise.

    Starting frequencies are *literature-derived placeholders* — frame
    natural frequencies sit in the 8-30 Hz mechanical band (Misch 2022),
    but the audio coupling shows up much higher because each wheel
    rotation creates a transient and harmonics stack. The two default
    notches at 200 and 400 Hz target the typical bearing/rolling rumble
    band. Tune them with `--analyze` output: if the spectrum shows
    sharp peaks elsewhere, add `--notch FREQ1,FREQ2,...`.
    """
    notches = [200.0, 400.0]
    if extra_notches:
        notches.extend(extra_notches)

    notch_chain = ",".join(
        f"bandreject=f={fc:.0f}:width_type=h:w=20" for fc in notches
    )

    af = (
        "highpass=f=150,"             # more aggressive: kill wind + wobble-induced rumble
        f"{notch_chain},"             # notch out caster/bearing tones
        "lowpass=f=10000,"            # tighter top-end (mechanical hiss has nothing above 10 kHz worth keeping)
        "afftdn=nr=24:nf=-28,"        # stronger spectral noise reduction
        "loudnorm=I=-16:LRA=11:TP=-1.5"
    )
    run(['ffmpeg', '-y', '-loglevel', 'warning',
         '-i', str(input_path),
         '-af', af,
         '-c:v', 'copy',
         '-c:a', 'aac', '-b:a', '192k',
         str(output_path)])
    print(f"Cleaned (caster preset, notches at {notches} Hz): {output_path}")


def denoise_with_notches(input_path: Path, output_path: Path, notches):
    """User-specified notch frequencies on top of the general clean-up."""
    notch_chain = ",".join(
        f"bandreject=f={fc:.0f}:width_type=h:w=20" for fc in notches
    )
    af = (
        "highpass=f=80,"
        f"{notch_chain},"
        "lowpass=f=11000,"
        "afftdn=nr=20:nf=-26,"
        "loudnorm=I=-16:LRA=11:TP=-1.5"
    )
    run(['ffmpeg', '-y', '-loglevel', 'warning',
         '-i', str(input_path),
         '-af', af,
         '-c:v', 'copy',
         '-c:a', 'aac', '-b:a', '192k',
         str(output_path)])
    print(f"Cleaned (notches at {notches} Hz): {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Clean helmet-camera audio noise from MOV/MP4 files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs='?', default=None,
                        help="output path (default: same folder, '-clean' suffix)")
    parser.add_argument("--analyze", action="store_true",
                        help="show spectrum and dominant bands, then exit")
    parser.add_argument("--profile-from", default=None,
                        metavar="MM:SS-MM:SS",
                        help="time range to use as the noise profile (best mode, needs sox)")
    parser.add_argument("--strength", type=float, default=0.21,
                        help="profile noise reduction amount, 0.0–1.0 (default 0.21)")
    parser.add_argument("--target-caster", action="store_true",
                        help="preset tuned for wheelchair caster/frame mechanical noise")
    parser.add_argument("--notch", default=None,
                        metavar="F1,F2,...",
                        help="comma-separated centre frequencies (Hz) to notch out, "
                             "e.g. 200,400,820")
    args = parser.parse_args()

    # Parse --notch list
    extra_notches = None
    if args.notch:
        try:
            extra_notches = [float(x) for x in args.notch.split(',') if x.strip()]
        except ValueError:
            sys.exit(f"--notch list must be numeric (Hz), got: {args.notch}")

    if not args.input.exists():
        sys.exit(f"File not found: {args.input}")

    if args.analyze:
        check_tools(need_sox=False)
        analyze(args.input)
        return

    out = args.output or args.input.with_name(args.input.stem + "-clean" + args.input.suffix)

    check_tools(need_sox=bool(args.profile_from))

    if args.profile_from:
        denoise_with_profile(args.input, out, args.profile_from, args.strength)
    elif args.target_caster:
        denoise_caster(args.input, out, extra_notches=extra_notches)
    elif extra_notches:
        denoise_with_notches(args.input, out, extra_notches)
    else:
        denoise_general(args.input, out)


if __name__ == "__main__":
    main()
