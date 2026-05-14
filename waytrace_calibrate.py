#!/usr/bin/env python3
"""
WayTrace Calibrate — derive a per-chair calibration profile.

You record one ~5-minute session with the WayTrace Android app and tap
the PIN button at seven known moments:

    pin 1 — start of stationary phase (phone mounted, chair not moving)
    pin 2 — end of stationary / start of corridor push
    pin 3 — end of corridor push (smooth indoor surface, ~1 m/s, ~60 s)
    pin 4 — landing of first drop (off a measured ~5 cm threshold)
    pin 5 — landing of second drop
    pin 6 — landing of third drop
    pin 7 — end of calibration session

This script reads the resulting ART-*.csv, locates the seven pinpoints,
and writes a JSON profile to ~/.config/waytrace/chairs/<chair-id>.json.

The profile captures:
    - stationary: gravity magnitude (sanity), noise floor RMS
    - corridor:   raw vertical RMS over the smooth push (chair baseline)
    - drops:      ensemble peak acceleration and ringdown over 5 cm step

Usage:
    python3 waytrace_calibrate.py <ART-CAL-*.csv> --chair NAME [--mount LOC]

Args:
    --chair         identifier (e.g. "foldable-2026" or "rigid-2027")
    --mount         "caster-fork" | "seat-tube" | "backrest"
                    (literature flags caster-fork as overstating ~3×)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from waytrace_analysis import (
    GRAVITY, SAMPLE_RATE, load_csv, split_sensors,
    detect_generation, generation_banner,
)

CHAIRS_DIR = Path.home() / ".config" / "waytrace" / "chairs"
EXPECTED_PINS = 7


def find_pinpoint_times(df) -> list[float]:
    """Return seconds-since-start for each pinpoint, in pinpoint-number order."""
    pins = df[df['sensor'] == 'pinpoint'].copy()
    if pins.empty:
        sys.exit("No pinpoint rows found. Did you tap PIN seven times during calibration?")

    def pin_num(s):
        try:
            return int(str(s).split('_')[-1])
        except Exception:
            return -1

    pins['n'] = pins['event'].map(pin_num)
    pins = pins.sort_values('n').reset_index(drop=True)

    t0 = float(df['timestamp_ms'].iloc[0])
    pins['t_s'] = (pins['timestamp_ms'].astype(float) - t0) / 1000.0
    return list(pins['t_s'].values), list(pins['n'].values)


def vertical_rms_minus_gravity(accel_window) -> float:
    """RMS of (accel.y - 9.81). Legacy 'Y is vertical' approximation —
    used until the ISO Wk filter and gravity-vector world-frame transform
    are wired in by the next plan."""
    y = accel_window['y'].values
    return float(np.sqrt(np.mean((y - GRAVITY) ** 2)))


def magnitude_rms(accel_window) -> float:
    """RMS of total acceleration magnitude — gravity-included, raw."""
    m = np.sqrt(accel_window['x'].values ** 2 +
                accel_window['y'].values ** 2 +
                accel_window['z'].values ** 2)
    return float(np.sqrt(np.mean(m ** 2)))


def slice_accel(accel, t_start_s, t_end_s):
    """Return accel rows whose t_s is in [t_start_s, t_end_s]."""
    m = (accel['t_s'] >= t_start_s) & (accel['t_s'] <= t_end_s)
    return accel[m].copy().reset_index(drop=True)


def analyse_stationary(accel, df, t_start, t_end):
    seg = slice_accel(accel, t_start, t_end)
    if len(seg) < 30:
        return {'error': f'too few samples in stationary phase ({len(seg)})'}

    mag = np.sqrt(seg['x'].values ** 2 + seg['y'].values ** 2 + seg['z'].values ** 2)
    out = {
        'duration_s': float(t_end - t_start),
        'accel_magnitude_mean': float(np.mean(mag)),
        'accel_magnitude_std':  float(np.std(mag)),
        'noise_floor_rms':      float(np.std(mag)),  # std of mag at rest = noise
    }

    # If v2 gravity rows are present, also report gravity-vector magnitude.
    grav = df[(df['sensor'] == 'gravity') &
              (df['timestamp_ms'].astype(float) >= seg['timestamp_ms'].iloc[0]) &
              (df['timestamp_ms'].astype(float) <= seg['timestamp_ms'].iloc[-1])]
    if not grav.empty:
        gmag = np.sqrt(grav['x'].astype(float) ** 2 +
                       grav['y'].astype(float) ** 2 +
                       grav['z'].astype(float) ** 2)
        out['gravity_magnitude_mean'] = float(np.mean(gmag))
        out['gravity_magnitude_std']  = float(np.std(gmag))
    return out


def analyse_corridor(accel, t_start, t_end):
    seg = slice_accel(accel, t_start, t_end)
    if len(seg) < 60:
        return {'error': f'too few samples in corridor phase ({len(seg)})'}
    return {
        'duration_s':        float(t_end - t_start),
        'sample_count':      int(len(seg)),
        'raw_rms_y_minus_g': vertical_rms_minus_gravity(seg),
        'raw_rms_magnitude': magnitude_rms(seg),
        # NOTE: weighted_rms_z (Wk-filtered) gets added by the v2 ISO plan.
    }


def analyse_drops(accel, drop_times_s):
    """Each drop is a pinpoint near a 5 cm-step landing. We look at the
    100 ms window immediately AFTER each pin (landing transient) and the
    400 ms after that (ring-down)."""
    drops = []
    for t in drop_times_s:
        # Landing window: 100 ms immediately after the pin
        landing = slice_accel(accel, t, t + 0.10)
        if len(landing) < 3:
            continue
        mag = np.sqrt(landing['x'].values ** 2 +
                      landing['y'].values ** 2 +
                      landing['z'].values ** 2)
        peak = float(np.max(mag))

        # Ring-down window: next 400 ms
        ring = slice_accel(accel, t + 0.10, t + 0.50)
        if len(ring) >= 8:
            ymag = np.sqrt(ring['x'].values ** 2 +
                           ring['y'].values ** 2 +
                           ring['z'].values ** 2) - GRAVITY
            # Dominant freq via simple FFT
            n = len(ymag)
            fft_mag = np.abs(np.fft.rfft(ymag - np.mean(ymag)))
            freqs   = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE)
            if len(freqs) > 1:
                dom_freq = float(freqs[1 + int(np.argmax(fft_mag[1:]))])
            else:
                dom_freq = float('nan')
        else:
            dom_freq = float('nan')

        drops.append({'pin_t_s': float(t), 'peak_accel': peak,
                      'ringdown_freq_hz': dom_freq})

    if not drops:
        return {'error': 'no usable drop windows'}

    peaks = np.array([d['peak_accel'] for d in drops])
    freqs = np.array([d['ringdown_freq_hz'] for d in drops])
    return {
        'count':                 len(drops),
        'peak_accel_mean':       float(np.mean(peaks)),
        'peak_accel_std':        float(np.std(peaks)),
        'ringdown_freq_hz_mean': float(np.nanmean(freqs)),
        'per_drop':              drops,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Derive a per-chair calibration profile from a 7-pin ART CSV.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--chair", required=True,
                        help="chair identifier, e.g. 'foldable-2026'")
    parser.add_argument("--mount", default="unknown",
                        choices=["caster-fork", "seat-tube", "backrest",
                                 "footrest", "unknown"],
                        help="phone mount location; 'caster-fork' overstates "
                             "body-relevant vibration ~3× per Wolf 2005.")
    args = parser.parse_args()

    csv_path = args.csv_path
    if not csv_path.exists():
        sys.exit(f"File not found: {csv_path}")

    print(f"Loading {csv_path.name} ...")
    df = load_csv(csv_path)
    accel, _ = split_sensors(df)
    generation, sensors_present = detect_generation(df)
    print(f"   file generation: {generation_banner(generation, sensors_present)}")

    pin_times, pin_nums = find_pinpoint_times(df)
    if len(pin_times) != EXPECTED_PINS:
        sys.exit(f"Expected {EXPECTED_PINS} pinpoints, found {len(pin_times)} "
                 f"(numbers: {pin_nums}). Re-record the calibration.")

    # Map by position: pins are pinpoint_1 .. pinpoint_7
    p1, p2, p3, p4, p5, p6, p7 = pin_times
    print(f"   pin times (s): "
          f"stationary {p1:.1f}–{p2:.1f}, "
          f"corridor {p2:.1f}–{p3:.1f}, "
          f"drops @ {p4:.1f}/{p5:.1f}/{p6:.1f}, end {p7:.1f}")

    stationary = analyse_stationary(accel, df, p1, p2)
    corridor   = analyse_corridor(accel, p2, p3)
    drops      = analyse_drops(accel, [p4, p5, p6])

    profile = {
        'chair_id':         args.chair,
        'mount_location':   args.mount,
        'source_file':      csv_path.name,
        'source_generation': generation,
        'sensors_present':   sorted(sensors_present),
        'captured_utc':     datetime.now(timezone.utc).isoformat(),
        'stationary':       stationary,
        'corridor':         corridor,
        'drops':            drops,
    }

    CHAIRS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CHAIRS_DIR / f"{args.chair}.json"
    out_path.write_text(json.dumps(profile, indent=2) + "\n", encoding='utf-8')

    print()
    print(f"Profile written: {out_path}")
    print(f"   stationary noise floor RMS: {stationary.get('noise_floor_rms', float('nan')):.4f} m/s²")
    print(f"   corridor raw RMS (y-g):     {corridor.get('raw_rms_y_minus_g', float('nan')):.4f} m/s²")
    print(f"   corridor raw RMS (|a|):     {corridor.get('raw_rms_magnitude', float('nan')):.4f} m/s²")
    print(f"   drops peak mean:            "
          f"{drops.get('peak_accel_mean', float('nan')):.2f} ± "
          f"{drops.get('peak_accel_std', float('nan')):.2f} m/s²  "
          f"(n={drops.get('count', 0)})")

    if args.mount == "caster-fork":
        print("\nNOTE: caster-fork mount overstates body-relevant vibration ~3×")
        print("      (Wolf 2005, Garcia-Mendez 2013). Re-mount on seat tube near")
        print("      the hip before publishing ISO-compliant absolute numbers.")


if __name__ == "__main__":
    main()
