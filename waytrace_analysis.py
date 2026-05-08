#!/usr/bin/env python3
"""
WayTrace Signal Analysis Toolkit
Part of Open Streets Initiative — Vasil Taneski, Prilep, North Macedonia

Usage:
    python waytrace_analysis.py sensors_YYYYMMDD_HHMMSS.csv
    (or drag a CSV file onto this script)

Output:
    - Console summary
    - waytrace_report_YYYYMMDD_HHMMSS.png  (charts)
    - waytrace_report_YYYYMMDD_HHMMSS.txt  (text summary)
"""

import sys
import csv
import math
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy import signal
from scipy.stats import skew, kurtosis
from pathlib import Path
from datetime import datetime


# ── Axis mapping (confirmed from real data, phone on right side of wheelchair)
# Y_accel = VERTICAL (gravity ~+9.8 m/s²)
# X_accel = FORWARD/BACKWARD
# Z_accel = LATERAL
# Y_gyro  = YAW (turning)
# X_gyro  = ROLL (sideways lean)
# Z_gyro  = PITCH (wheelies)

SAMPLE_RATE = 60.0  # Hz (Xiaomi hardware ceiling)
GRAVITY     = 9.81  # m/s²

# ISO 2631-1 whole-body vibration health thresholds (RMS over session)
RMS_COMFORTABLE    = 0.5   # m/s²
RMS_UNCOMFORTABLE  = 1.15  # m/s²

# ISO 2631-1 VDV thresholds
VDV_LOW      = 8.5   # m/s^1.75
VDV_MODERATE = 17.0  # m/s^1.75

# ISO 2631-5 single-event shock thresholds (total magnitude, gravity included)
# At rest the phone reads ~9.8 m/s². These thresholds represent excess above baseline:
# BUMP_ISO:       15.0 m/s²  =  ~5.2 m/s² excess  =  ~0.53g  →  "quite uncomfortable" (ISO 2631-1)
# HEAVY_BUMP_ISO: 20.0 m/s²  = ~10.2 m/s² excess  =  ~1.04g  →  clinically significant shock (ISO 2631-5)
BUMP_ISO       = 12.0  # m/s²
HEAVY_BUMP_ISO = 18.0  # m/s²

# Jerk threshold for discrete obstacles
JERK_OBSTACLE_THRESHOLD = 50.0  # m/s³


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    return df


def split_sensors(df: pd.DataFrame):
    accel = df[df['sensor'] == 'accel'].copy().reset_index(drop=True)
    gyro  = df[df['sensor'] == 'gyro'].copy().reset_index(drop=True)
    accel['magnitude'] = np.sqrt(accel['x']**2 + accel['y']**2 + accel['z']**2)
    accel['t_s'] = (accel['timestamp_ms'] - accel['timestamp_ms'].iloc[0]) / 1000.0
    if not gyro.empty:
        gyro['t_s'] = (gyro['timestamp_ms'] - df['timestamp_ms'].iloc[0]) / 1000.0
    return accel, gyro


# ── Technique 1: FFT ──────────────────────────────────────────────────────────

def compute_fft(accel: pd.DataFrame):
    x = accel['x'].values
    n = len(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / SAMPLE_RATE)
    fft_vals = np.abs(np.fft.rfft(x)) ** 2 / n  # PSD
    return freqs, fft_vals


def dominant_band(freqs, psd):
    bands = {
        'Push rhythm (0.5–2 Hz)':   (0.5, 2.0),
        'Surface texture (2–5 Hz)': (2.0, 5.0),
        'Sharp impacts (5–10 Hz)':  (5.0, 10.0),
    }
    band_power = {}
    for name, (lo, hi) in bands.items():
        mask = (freqs >= lo) & (freqs <= hi)
        band_power[name] = psd[mask].sum() if mask.any() else 0.0
    dominant = max(band_power, key=band_power.get)
    return dominant, band_power


# ── Technique 2: RMS ─────────────────────────────────────────────────────────

def compute_rms(accel: pd.DataFrame, window_s: float = 10.0):
    mag = accel['magnitude'].values
    rms_full = math.sqrt(np.mean(mag ** 2))

    window = int(window_s * SAMPLE_RATE)
    rms_windows = []
    t_windows = []
    for i in range(0, len(mag) - window, window // 2):
        chunk = mag[i:i + window]
        rms_windows.append(math.sqrt(np.mean(chunk ** 2)))
        t_windows.append(accel['t_s'].iloc[i + window // 2])

    pct_uncomfortable = 100 * np.mean(np.array(rms_windows) > RMS_UNCOMFORTABLE)
    pct_moderate      = 100 * np.mean(
        (np.array(rms_windows) > RMS_COMFORTABLE) & (np.array(rms_windows) <= RMS_UNCOMFORTABLE)
    )

    return rms_full, np.array(t_windows), np.array(rms_windows), pct_uncomfortable, pct_moderate


# ── Technique 3: VDV ─────────────────────────────────────────────────────────

def compute_vdv(accel: pd.DataFrame):
    mag = accel['magnitude'].values
    dt = 1.0 / SAMPLE_RATE
    vdv = (np.sum(mag ** 4) * dt) ** 0.25
    if vdv < VDV_LOW:
        risk = 'LOW'
    elif vdv < VDV_MODERATE:
        risk = 'MODERATE'
    else:
        risk = 'HIGH'
    return vdv, risk


# ── Technique 4: STFT Spectrogram ─────────────────────────────────────────────

def compute_stft(accel: pd.DataFrame):
    x = accel['x'].values
    window_samples = int(5.0 * SAMPLE_RATE)
    overlap = window_samples // 2
    f, t, Zxx = signal.stft(x, fs=SAMPLE_RATE, nperseg=window_samples, noverlap=overlap)
    return f, t, np.abs(Zxx)


# ── Technique 5: Jerk ────────────────────────────────────────────────────────

def compute_jerk(accel: pd.DataFrame):
    mag = accel['magnitude'].values
    dt = 1.0 / SAMPLE_RATE
    jerk = np.abs(np.diff(mag)) / dt
    t_jerk = accel['t_s'].values[1:]
    obstacles = t_jerk[jerk > JERK_OBSTACLE_THRESHOLD]
    return t_jerk, jerk, obstacles


# ── Technique 6: IRI Estimation ───────────────────────────────────────────────

def compute_iri(accel: pd.DataFrame):
    # Simplified: high-pass filter Z_accel (lateral in our mounting acts as proxy
    # for vertical relative to motion), compute windowed RMS, scale to IRI
    z = accel['z'].values
    b, a = signal.butter(2, 0.5 / (SAMPLE_RATE / 2), btype='high')
    z_filtered = signal.filtfilt(b, a, z)

    window = int(10 * SAMPLE_RATE)  # ~10 second windows
    iri_vals = []
    for i in range(0, len(z_filtered) - window, window):
        chunk = z_filtered[i:i + window]
        rms_chunk = math.sqrt(np.mean(chunk ** 2))
        # Empirical calibration factor for wheelchair/smartphone setup
        iri_vals.append(rms_chunk * 12.0)

    iri_mean = float(np.mean(iri_vals)) if iri_vals else 0.0

    if iri_mean < 2:
        condition = 'Smooth (new asphalt)'
    elif iri_mean < 4:
        condition = 'Good urban road'
    elif iri_mean < 8:
        condition = 'Worn / noticeable roughness'
    elif iri_mean < 16:
        condition = 'Damaged road'
    else:
        condition = 'Severely damaged'

    return iri_mean, condition, iri_vals


# ── Technique 7: Statistical Profile ─────────────────────────────────────────

def compute_stats(accel: pd.DataFrame):
    mag = accel['magnitude'].values
    return {
        'mean':   float(np.mean(mag)),
        'std':    float(np.std(mag)),
        'skew':   float(skew(mag)),
        'kurt':   float(kurtosis(mag)),
        'p50':    float(np.percentile(mag, 50)),
        'p90':    float(np.percentile(mag, 90)),
        'p95':    float(np.percentile(mag, 95)),
        'p99':    float(np.percentile(mag, 99)),
        'max':    float(np.max(mag)),
    }


# ── Plotting ──────────────────────────────────────────────────────────────────

def plot_report(accel, freqs, psd, dom_band, band_power,
                t_rms, rms_windows,
                f_stft, t_stft, stft_mag,
                t_jerk, jerk, obstacles,
                stats, rms_full, vdv, vdv_risk, iri, iri_condition,
                session_name, out_path):

    bump_times = accel[accel['event'] == 'bump']['t_s'].values

    fig = plt.figure(figsize=(18, 14), facecolor='#1a1a2e')
    fig.suptitle(f'WayTrace Road Quality Report — {session_name}',
                 fontsize=15, color='white', fontweight='bold', y=0.98)

    gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.35,
                           left=0.07, right=0.97, top=0.93, bottom=0.06)

    ax_style = dict(facecolor='#0d0d1a', labelcolor='#cccccc', titlecolor='white')

    # ── 1. FFT spectrum ───────────────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor(ax_style['facecolor'])
    mask = freqs <= 10
    ax1.plot(freqs[mask], psd[mask], color='#00ccff', linewidth=1.2)
    ax1.axvspan(0.5, 2.0, alpha=0.15, color='green',  label='Push rhythm')
    ax1.axvspan(2.0, 5.0, alpha=0.15, color='yellow', label='Surface texture')
    ax1.axvspan(5.0, 10.0, alpha=0.15, color='red',   label='Sharp impacts')
    ax1.set_xlabel('Frequency (Hz)', color='#cccccc')
    ax1.set_ylabel('Power', color='#cccccc')
    ax1.set_title('FFT — Frequency Spectrum (X accel)', color='white')
    ax1.tick_params(colors='#cccccc')
    ax1.legend(fontsize=7, labelcolor='#cccccc', facecolor='#1a1a2e')
    short = dom_band.split('(')[0].strip()
    ax1.text(0.97, 0.95, f'Dominant:\n{short}', transform=ax1.transAxes,
             ha='right', va='top', color='#ffcc00', fontsize=8)

    # ── 2. RMS over time ──────────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor(ax_style['facecolor'])
    ax2.plot(t_rms, rms_windows, color='#00ff88', linewidth=1.2, label='RMS (10s window)')
    ax2.axhline(RMS_COMFORTABLE,   color='yellow', linestyle='--', linewidth=0.8, label=f'ISO limit 1 ({RMS_COMFORTABLE} m/s²)')
    ax2.axhline(RMS_UNCOMFORTABLE, color='red',    linestyle='--', linewidth=0.8, label=f'ISO limit 2 ({RMS_UNCOMFORTABLE} m/s²)')
    for bt in bump_times:
        ax2.axvline(bt, color='orange', alpha=0.4, linewidth=0.6)
    ax2.set_xlabel('Time (s)', color='#cccccc')
    ax2.set_ylabel('RMS (m/s²)', color='#cccccc')
    ax2.set_title(f'RMS Vibration — Session avg: {rms_full:.2f} m/s²', color='white')
    ax2.tick_params(colors='#cccccc')
    ax2.legend(fontsize=7, labelcolor='#cccccc', facecolor='#1a1a2e')

    # ── 3. STFT spectrogram ───────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[1, 0])
    ax3.set_facecolor(ax_style['facecolor'])
    freq_mask = f_stft <= 10
    ax3.pcolormesh(t_stft, f_stft[freq_mask], stft_mag[freq_mask, :],
                   shading='gouraud', cmap='inferno')
    for bt in bump_times:
        ax3.axvline(bt, color='cyan', alpha=0.5, linewidth=0.8)
    ax3.set_xlabel('Time (s)', color='#cccccc')
    ax3.set_ylabel('Frequency (Hz)', color='#cccccc')
    ax3.set_title('STFT Spectrogram — Road character over time', color='white')
    ax3.tick_params(colors='#cccccc')
    ax3.text(0.01, 0.97, 'cyan lines = bumps', transform=ax3.transAxes,
             color='cyan', fontsize=7, va='top')

    # ── 4. Jerk ───────────────────────────────────────────────────────────────
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.set_facecolor(ax_style['facecolor'])
    ax4.plot(t_jerk, jerk, color='#ff6666', linewidth=0.8, alpha=0.8)
    ax4.axhline(JERK_OBSTACLE_THRESHOLD, color='white', linestyle='--',
                linewidth=0.8, label=f'Obstacle threshold ({JERK_OBSTACLE_THRESHOLD} m/s³)')
    ax4.set_xlabel('Time (s)', color='#cccccc')
    ax4.set_ylabel('Jerk (m/s³)', color='#cccccc')
    ax4.set_title(f'Jerk — {len(obstacles)} discrete obstacles detected', color='white')
    ax4.tick_params(colors='#cccccc')
    ax4.legend(fontsize=7, labelcolor='#cccccc', facecolor='#1a1a2e')

    # ── 5. Statistical summary table ──────────────────────────────────────────
    ax5 = fig.add_subplot(gs[2, :])
    ax5.set_facecolor(ax_style['facecolor'])
    ax5.axis('off')

    bump_count       = len(accel[accel['event'] == 'bump'])
    heavy_bump_count = len(accel[accel['event'] == 'heavy_bump'])
    tilt_count       = len(accel[accel['event'].isin(['tilt', 'wheelie', 'fall'])])

    summary = [
        ['Metric', 'Value', '', 'Metric', 'Value'],
        ['RMS (full session)', f"{rms_full:.3f} m/s²", '',
         'VDV', f"{vdv:.2f} m/s^1.75  →  {vdv_risk} risk"],
        ['IRI estimate', f"{iri:.1f} m/km  →  {iri_condition}", '',
         'Bumps logged', str(bump_count)],
        ['Mean magnitude', f"{stats['mean']:.3f} m/s²", '',
         'Discrete obstacles (jerk)', str(len(obstacles))],
        ['Std deviation', f"{stats['std']:.3f}", '',
         'p95 magnitude', f"{stats['p95']:.3f} m/s²"],
        ['Skewness', f"{stats['skew']:.3f}", '',
         'p99 magnitude', f"{stats['p99']:.3f} m/s²"],
        ['Kurtosis', f"{stats['kurt']:.3f}", '',
         'Max magnitude', f"{stats['max']:.3f} m/s²"],
        ['Dominant freq band', dom_band.split('(')[0].strip(), '',
         'Duration', f"{accel['t_s'].iloc[-1]:.1f} s"],
    ]

    col_widths = [0.20, 0.22, 0.03, 0.22, 0.28]
    x_positions = [0.01, 0.21, 0.43, 0.46, 0.68]
    y_start = 0.92

    for r_idx, row in enumerate(summary):
        y = y_start - r_idx * 0.13
        is_header = r_idx == 0
        for c_idx, cell in enumerate(row):
            color = '#ffcc00' if is_header else ('#aaaaaa' if c_idx in (0, 3) else 'white')
            fontsize = 8.5 if is_header else 8
            ax5.text(x_positions[c_idx], y, cell, transform=ax5.transAxes,
                     color=color, fontsize=fontsize,
                     fontweight='bold' if is_header else 'normal', va='top')

    ax5.set_title('Statistical Profile & Summary', color='white', pad=8)

    plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
    plt.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python waytrace_analysis.py <sensors_YYYYMMDD_HHMMSS.csv>")
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"File not found: {csv_path}")
        sys.exit(1)

    session_name = csv_path.stem
    out_dir = csv_path.parent
    png_path = out_dir / f"ANL-{session_name.replace('ART-', '')}.png"
    txt_path = out_dir / f"ANL-{session_name.replace('ART-', '')}.txt"

    print(f"\nWayTrace Analysis — {session_name}")
    print("=" * 60)

    df = load_csv(csv_path)
    accel, gyro = split_sensors(df)

    if len(accel) < 10:
        print("Not enough accelerometer data.")
        sys.exit(1)

    print(f"Rows loaded   : {len(df)} total ({len(accel)} accel, {len(gyro)} gyro)")
    print(f"Duration      : {accel['t_s'].iloc[-1]:.1f} s")

    # Run all techniques
    freqs, psd                          = compute_fft(accel)
    dom_band, band_power                = dominant_band(freqs, psd)
    rms_full, t_rms, rms_windows, pct_bad, pct_mod = compute_rms(accel)
    vdv, vdv_risk                       = compute_vdv(accel)
    f_stft, t_stft, stft_mag            = compute_stft(accel)
    t_jerk, jerk, obstacles             = compute_jerk(accel)
    iri, iri_condition, _               = compute_iri(accel)
    stats                               = compute_stats(accel)

    # Count from logged labels (recorded by app)
    bump_logged       = len(accel[accel['event'] == 'bump'])
    heavy_bump_logged = len(accel[accel['event'] == 'heavy_bump'])

    # Re-evaluate from raw magnitude using ISO thresholds (independent of app version)
    mag = accel['magnitude'].values
    bump_count       = int(np.sum(
        (mag >= BUMP_ISO) & (mag < HEAVY_BUMP_ISO) &
        (np.diff(np.concatenate([[0], (mag >= BUMP_ISO).astype(int)])) == 1)
    ))
    heavy_bump_count = int(np.sum(
        (mag >= HEAVY_BUMP_ISO) &
        (np.diff(np.concatenate([[0], (mag >= HEAVY_BUMP_ISO).astype(int)])) == 1)
    ))

    # Console summary
    print(f"\nRMS           : {rms_full:.3f} m/s²")
    print(f"VDV           : {vdv:.2f} m/s^1.75  →  {vdv_risk} health risk")
    print(f"IRI estimate  : {iri:.1f} m/km  →  {iri_condition}")
    print(f"Dominant freq : {dom_band}")
    print(f"Bumps ≥{BUMP_ISO} m/s²       : {bump_count}  [logged by app: {bump_logged}]")
    print(f"Heavy bumps ≥{HEAVY_BUMP_ISO} m/s²  : {heavy_bump_count}  [logged by app: {heavy_bump_logged}]")
    print(f"Jerk obstacles: {len(obstacles)}")
    print(f"Max magnitude : {stats['max']:.2f} m/s²")
    print(f"p95 magnitude : {stats['p95']:.2f} m/s²")

    # One-liner log entry
    log_line = (
        f"Session {session_name} | "
        f"Duration: {accel['t_s'].iloc[-1]:.1f}s | "
        f"RMS: {rms_full:.2f} m/s² | "
        f"VDV: {vdv:.1f} ({vdv_risk}) | "
        f"IRI est: {iri:.1f} m/km | "
        f"Bumps: {bump_count} | "
        f"Heavy bumps: {heavy_bump_count} | "
        f"Obstacles: {len(obstacles)}"
    )
    print(f"\n{log_line}")

    # Save text report
    txt_path.write_text(log_line + "\n\nBand power:\n" +
                        "\n".join(f"  {k}: {v:.4f}" for k, v in band_power.items()) +
                        f"\n\nStats:\n" +
                        "\n".join(f"  {k}: {v:.4f}" for k, v in stats.items()),
                        encoding='utf-8')

    # Plot
    print(f"\nGenerating chart → {png_path.name} ...")
    plot_report(accel, freqs, psd, dom_band, band_power,
                t_rms, rms_windows,
                f_stft, t_stft, stft_mag,
                t_jerk, jerk, obstacles,
                stats, rms_full, vdv, vdv_risk, iri, iri_condition,
                session_name, png_path)

    print(f"Done. Report saved to:\n  {png_path}\n  {txt_path}")


if __name__ == '__main__':
    main()
