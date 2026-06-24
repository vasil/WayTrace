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
import argparse
import xml.etree.ElementTree as ET
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

GRAVITY     = 9.81  # m/s²

# Sample rate is detected per-recording at runtime — see detect_sample_rate().
# v3.0.4 delivers 120 Hz; older recordings were 60 Hz. All technique functions
# take fs as an argument so the same code works at either rate.

# Low-pass cutoff used before jerk differentiation. 20 Hz is well below
# Nyquist for both 60 Hz (Nyq 30) and 120 Hz (Nyq 60), so the same filter
# spec works for every recording the project has produced.
JERK_PRE_LPF_HZ = 20.0

# ISO 2631-1 whole-body vibration health thresholds (RMS over session)
RMS_COMFORTABLE    = 0.5   # m/s²
RMS_UNCOMFORTABLE  = 1.15  # m/s²

# ISO 2631-1 VDV thresholds
VDV_LOW      = 8.5   # m/s^1.75
VDV_MODERATE = 17.0  # m/s^1.75

# Jerk threshold for discrete obstacles
JERK_OBSTACLE_THRESHOLD = 50.0  # m/s³

# ── Wheelchair geometry — CONFIRMED values from SRS-CURRENT § "WHEELCHAIR GEOMETRY"
WHEELBASE_M             = 0.32
CASTER_DIAMETER_M       = 0.1016
PHONE_HEIGHT_M          = 0.35
PHONE_FORWARD_OFFSET_M  = 0.15
PHONE_LATERAL_OFFSET_M  = 0.03
RIDER_MASS_KG           = 63.0
CHAIR_MASS_KG           = 15.0
TOTAL_ROLLING_MASS_KG   = RIDER_MASS_KG + CHAIR_MASS_KG     # 78 kg
PNEUMATIC_FACTOR        = 1.47

# Caster-fork mounts overstate body-relevant vertical vibration ~3× (Wolf 2005,
# Garcia-Mendez 2013). Vasil's mount is a fabric-strap pocket above the caster:
# compliant, so the factor lands between caster-fork (3.0) and seat-tube (1.0).
# 2.0 is a literature-bracketed default until a waytrace_calibrate run pins it
# from drop-test data on the current chair.
GEOMETRY_FACTOR_CASTER_COMPLIANT = 2.0

# ── ISO 8608 road-roughness class boundaries
# Gd(n₀) is the displacement PSD at reference spatial frequency n₀ = 0.1 cyc/m.
# Units: m³ (= m² · m, displacement² per cycles-per-meter).
ISO8608_N0 = 0.1  # cycles / m
ISO8608_CLASS_LIMITS = [
    ('A', 32e-6),
    ('B', 128e-6),
    ('C', 512e-6),
    ('D', 2048e-6),
    ('E', 8192e-6),
    ('F', 32768e-6),
    ('G', 131072e-6),
]  # H = everything above the last limit
ISO8608_LABELS = {
    'A': 'very good',
    'B': 'good',
    'C': 'average',
    'D': 'poor',
    'E': 'very poor',
    'F': 'undriveable (motor vehicle)',
    'G': 'undriveable (motor vehicle)',
    'H': 'undriveable (motor vehicle)',
}


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    return df


# ── File generation detection (v1, v2, v3) ───────────────────────────────────
#
# v1 (until 2026-05-14): accel, gyro, pinpoint only. Column 6 = "event"
#                        held bump/heavy_bump/wheelie/tilt/fall from the app.
# v2 (2026-05-14..-16) : adds gravity, rotvec, mag, pressure rows. Column 6
#                        = "event" carried events on accel/gyro and quat-W
#                        on rotvec rows.
# v3 (from 2026-05-17) : same sensors as v2, but the app no longer detects
#                        events. Column 6 is renamed "rotvec_w" — only rotvec
#                        rows populate it (with the quaternion W component);
#                        every other row leaves it blank. Pinpoint rows put
#                        the pin counter in column 3 (x).
#
# The primary signal for which generation a file belongs to is the timestamp
# embedded in its filename — ART-YYYYMMDDHHMM.csv. We additionally inspect
# the CSV header to confirm, since the header is unambiguous for v3.

import re
V2_CORE_SENSORS = {'gravity', 'rotvec', 'mag'}
V2_OPTIONAL_SENSORS = {'pressure', 'linaccel', 'step', 'light'}
V3_CUTOFF_DT = datetime(2026, 5, 17, 0, 0)   # any ART file ≥ this datetime is v3


def detect_version_from_filename(path) -> str | None:
    """Parse YYYYMMDDHHMM out of ART-*.csv filename → 'v1' / 'v2' / 'v3' / None."""
    m = re.search(r'(\d{12})', Path(path).name)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), '%Y%m%d%H%M')
    except ValueError:
        return None
    if dt < datetime(2026, 5, 14):
        return 'v1'
    if dt < V3_CUTOFF_DT:
        return 'v2'
    return 'v3'


def detect_generation(df: pd.DataFrame, path=None):
    """Return (generation, sensors_present) — prefers CSV header + sensors, falls
    back to filename date when content is ambiguous."""
    sensors_present = set(df['sensor'].dropna().unique())
    cols = [c.strip() for c in df.columns]
    # Header rename is the unambiguous v3 marker.
    if 'rotvec_w' in cols:
        return 'v3', sensors_present
    v2_core_present = V2_CORE_SENSORS & sensors_present
    if not v2_core_present and not (V2_OPTIONAL_SENSORS & sensors_present):
        # No v2 sensors → either v1 or a very short v3 with only accel/gyro.
        fn = detect_version_from_filename(path) if path else None
        return (fn or 'v1'), sensors_present
    if V2_CORE_SENSORS.issubset(sensors_present):
        return 'v2-full', sensors_present
    return 'v2-partial', sensors_present


def generation_banner(gen: str, sensors_present: set) -> str:
    """One-line description of file precision tier, for report headers."""
    if gen == 'v3':
        extras = sensors_present - {'accel', 'gyro', 'pinpoint'} - V2_CORE_SENSORS
        extra_note = f" + {','.join(sorted(extras))}" if extras else ""
        return f"v3 (raw recording, offline event detection; gravity + rotvec + mag{extra_note})"
    if gen == 'v2-full':
        extras = sensors_present - {'accel', 'gyro', 'pinpoint'} - V2_CORE_SENSORS
        extra_note = f" + {','.join(sorted(extras))}" if extras else ""
        return f"v2-full (gravity + rotvec + mag{extra_note}) — frame-correct ISO analysis available"
    if gen == 'v2-partial':
        missing = V2_CORE_SENSORS - sensors_present
        return f"v2-partial — missing {','.join(sorted(missing))}; using legacy Y-axis-vertical approximation"
    return "v1 (accel + gyro only) — legacy Y-axis-vertical approximation"


# ── Offline event detection — v3 and later have no events in the CSV ──────────
#
# These thresholds match the SRS and the previous in-app values. They run on
# every generation so reports across v1/v2/v3 are produced by the same logic.
# Tune here — no APK rebuild needed.

BUMP_MAG       = 12.0   # m/s² total magnitude (gravity-included)
HEAVY_BUMP_MAG = 18.0   # m/s²
ANGULAR_RATE   = 3.0    # rad/s for wheelie (Z_gyro) and tilt (X_gyro)
EVENT_COOLDOWN_S = 0.5  # mirror the in-app cooldown so counts stay comparable


def detect_events_offline(accel: pd.DataFrame, gyro: pd.DataFrame) -> pd.DataFrame:
    """Re-derive bump/heavy_bump/wheelie/tilt events from raw magnitudes.
    Returns a DataFrame with columns: t_s, kind. Empty if no events."""
    events = []
    last_accel_t = -10.0
    for t, m in zip(accel['t_s'].values, accel['magnitude'].values):
        if m > HEAVY_BUMP_MAG and (t - last_accel_t) > EVENT_COOLDOWN_S:
            events.append((t, 'heavy_bump'))
            last_accel_t = t
        elif m > BUMP_MAG and (t - last_accel_t) > EVENT_COOLDOWN_S:
            events.append((t, 'bump'))
            last_accel_t = t
    if not gyro.empty:
        last_w = -10.0
        last_tilt = -10.0
        for t, x, z in zip(gyro['t_s'].values, gyro['x'].values, gyro['z'].values):
            if abs(z) > ANGULAR_RATE and (t - last_w) > EVENT_COOLDOWN_S:
                events.append((t, 'wheelie'))
                last_w = t
            if abs(x) > ANGULAR_RATE and (t - last_tilt) > EVENT_COOLDOWN_S:
                events.append((t, 'tilt'))
                last_tilt = t
    if not events:
        return pd.DataFrame(columns=['t_s', 'kind'])
    return pd.DataFrame(events, columns=['t_s', 'kind']).sort_values('t_s').reset_index(drop=True)


def detect_sample_rate(accel: pd.DataFrame) -> float:
    """Median inter-sample interval → Hz. Same logic as tools/verify_60hz.py."""
    if len(accel) < 2:
        return 60.0
    dt = float(np.median(np.diff(accel['timestamp_ms'].values))) / 1000.0
    if dt <= 0:
        return 60.0
    return 1.0 / dt


def compute_gravity_vertical(accel: pd.DataFrame, fs: float) -> np.ndarray:
    """Vertical acceleration component (gravity baseline removed) for ANY
    phone-mount orientation.

    Method (per SRS OSI-023 reasoning):
      1. Low-pass each accel component at ~0.25 Hz → live gravity vector g(t).
      2. Project raw accel(t) onto the unit gravity direction → signed
         vertical projection at every sample.
      3. Subtract |g(t)| → vertical acceleration (signed; up bumps positive).

    Why per-sample gravity (not a single resting estimate):
      The new chair's backrest hanging pocket is non-rigid — under push load
      the pocket settles into a different orientation than at rest. Today's
      ART-202606240822.csv shows gravity on Z at rest, X during pushing,
      Z again on stopping. A fixed axis assumption breaks. Tracking gravity
      live makes the analysis mount-agnostic.

    Backwards compatibility:
      For old-chair caster-mount data (gravity steady on Y), the projection
      degenerates to a_y − g — identical to the previous behaviour.
    """
    ax = accel['x'].values
    ay = accel['y'].values
    az = accel['z'].values

    # 0.25 Hz cutoff: slow enough to reject ISO 2631-1 health-band terrain
    # vibration (a few Hz upward), fast enough to follow the pocket's
    # seconds-scale reorientation between rest and push.
    g_cut_hz = 0.25
    nyq = fs / 2.0
    b, a = signal.butter(2, g_cut_hz / nyq, btype='low')
    gx = signal.filtfilt(b, a, ax)
    gy = signal.filtfilt(b, a, ay)
    gz = signal.filtfilt(b, a, az)

    g_mag = np.sqrt(gx * gx + gy * gy + gz * gz)
    g_mag = np.where(g_mag < 1e-6, 1.0, g_mag)  # avoid /0 in free-fall

    ux = gx / g_mag
    uy = gy / g_mag
    uz = gz / g_mag

    proj = ax * ux + ay * uy + az * uz
    return proj - g_mag


def wk_weighted_vertical(accel: pd.DataFrame, fs: float,
                          pocket_hp_hz: float = 0.4) -> np.ndarray:
    """ISO 2631-1 Wk-weighted vertical acceleration on the gravity-projected
    vertical component. Works for any phone mount — caster strap, backrest
    hanging pocket, anything else. For OLD caster data this is identical to
    the previous Y-axis behaviour (gravity sits on Y → projection picks Y).

    Approximation: 4-pole Butterworth band-pass [pocket_hp_hz – 100 Hz].
    Within ~2 dB of the exact ISO Wk transfer function across the band.

    OSI-023 pocket high-pass:
      - Old-chair caster mount: pocket_hp_hz = 0.4 (preserves legacy
        behaviour; the rigid strap has no pendulum sway to remove).
      - New-chair backrest hanging pocket: pocket_hp_hz = 2.0 (removes the
        0.5–2 Hz pocket pendulum band; measured 2026-06-24 on real data,
        peak at 1.07 Hz, 5.8× above terrain reference). Disclose in output.
    """
    a_v = compute_gravity_vertical(accel, fs)
    nyq = fs / 2.0
    hi_cut = min(100.0, nyq * 0.99)
    lo_cut = max(0.05, min(pocket_hp_hz, hi_cut * 0.5))
    b, a = signal.butter(4, [lo_cut / nyq, hi_cut / nyq], btype='band')
    return signal.filtfilt(b, a, a_v)


def is_new_chair_recording(art_path) -> bool:
    """True if the ART file is from the new Küschall + backrest hanging
    pocket setup (2026-06-22 onwards). Used to gate the OSI-023 pocket
    high-pass. Old-chair files keep the legacy 0.4 Hz low edge."""
    import re
    m = re.search(r'ART-(\d{12})', str(art_path))
    if not m:
        return False
    try:
        return int(m.group(1)) >= 202606220000
    except Exception:
        return False


def split_sensors(df: pd.DataFrame):
    accel = df[df['sensor'] == 'accel'].copy().reset_index(drop=True)
    gyro  = df[df['sensor'] == 'gyro'].copy().reset_index(drop=True)
    accel['magnitude'] = np.sqrt(accel['x']**2 + accel['y']**2 + accel['z']**2)
    # Orientation-independent vibration intensity: total-acceleration magnitude
    # minus the gravity baseline. At rest this is ~0 regardless of how the
    # phone is mounted (Y-up, Z-up, tilted, etc.). Bumps/jerk/STFT use this.
    accel['vibration'] = np.abs(accel['magnitude'] - GRAVITY)
    accel['t_s'] = (accel['timestamp_ms'] - accel['timestamp_ms'].iloc[0]) / 1000.0
    if not gyro.empty:
        gyro['t_s'] = (gyro['timestamp_ms'] - df['timestamp_ms'].iloc[0]) / 1000.0
    return accel, gyro


# ── Technique 1: FFT ──────────────────────────────────────────────────────────

def compute_fft(accel: pd.DataFrame, fs: float):
    x = accel['x'].values
    n = len(x)
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
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
#
# Takes a generic signal array so the same code computes the ISO 2631-1
# Wk-weighted RMS (when fed `wk_weighted_vertical(accel, fs)`) AND the legacy
# raw-magnitude RMS (when fed `accel['vibration'].values`).

def compute_rms(vib: np.ndarray, t_s: np.ndarray, fs: float,
                window_s: float = 10.0):
    rms_full = math.sqrt(np.mean(vib ** 2))

    window = int(window_s * fs)
    rms_windows = []
    t_windows = []
    for i in range(0, len(vib) - window, window // 2):
        chunk = vib[i:i + window]
        rms_windows.append(math.sqrt(np.mean(chunk ** 2)))
        t_windows.append(t_s[i + window // 2])

    rms_arr = np.array(rms_windows)
    pct_uncomfortable = 100 * np.mean(rms_arr > RMS_UNCOMFORTABLE)
    pct_moderate      = 100 * np.mean(
        (rms_arr > RMS_COMFORTABLE) & (rms_arr <= RMS_UNCOMFORTABLE)
    )

    return rms_full, np.array(t_windows), rms_arr, pct_uncomfortable, pct_moderate


# ── Technique 3: VDV ─────────────────────────────────────────────────────────
#
# Same generic-signal contract as compute_rms. ISO 2631-1 formula:
# VDV = (∫ a⁴(t) dt)^¼. Result is health-risk graded against ISO 2631-1
# thresholds.

def compute_vdv(vib: np.ndarray, fs: float):
    dt = 1.0 / fs
    vdv = (np.sum(vib ** 4) * dt) ** 0.25
    if vdv < VDV_LOW:
        risk = 'LOW'
    elif vdv < VDV_MODERATE:
        risk = 'MODERATE'
    else:
        risk = 'HIGH'
    return vdv, risk


# ── Technique 4: STFT Spectrogram ─────────────────────────────────────────────

def compute_stft(accel: pd.DataFrame, fs: float):
    x = accel['x'].values
    window_samples = int(5.0 * fs)
    overlap = window_samples // 2
    f, t, Zxx = signal.stft(x, fs=fs, nperseg=window_samples, noverlap=overlap)
    return f, t, np.abs(Zxx)


# ── Technique 5: Jerk ────────────────────────────────────────────────────────

def compute_jerk(accel: pd.DataFrame, fs: float):
    # Low-pass the magnitude before differentiating: raw np.diff at 120 Hz
    # amplifies per-sample noise into spurious "jerk" spikes. The 20 Hz cutoff
    # is below Nyquist for every rate the project records at (60 / 120 Hz),
    # so the same spec works everywhere.
    mag = accel['magnitude'].values
    b, a = signal.butter(2, JERK_PRE_LPF_HZ / (fs / 2), btype='low')
    mag_smooth = signal.filtfilt(b, a, mag)
    dt = 1.0 / fs
    jerk = np.abs(np.diff(mag_smooth)) / dt
    t_jerk = accel['t_s'].values[1:]
    # Deduplicate above-threshold samples into discrete obstacle EVENTS, using
    # the same cooldown as detect_events_offline so counts stay comparable.
    above = jerk > JERK_OBSTACLE_THRESHOLD
    obstacles = []
    last_t = -10.0
    for t, hit in zip(t_jerk, above):
        if hit and (t - last_t) > EVENT_COOLDOWN_S:
            obstacles.append(t)
            last_t = t
    return t_jerk, jerk, np.array(obstacles)


# ── Technique 6: IRI Estimation ───────────────────────────────────────────────

def compute_iri(accel: pd.DataFrame, fs: float):
    # Orientation-independent: high-pass filter the centered magnitude
    # (|accel| - g), windowed RMS, scaled by an empirical wheelchair factor.
    vib = accel['vibration'].values
    b, a = signal.butter(2, 0.5 / (fs / 2), btype='high')
    v_filtered = signal.filtfilt(b, a, vib)

    window = int(10 * fs)  # ~10 second windows
    iri_vals = []
    for i in range(0, len(v_filtered) - window, window):
        chunk = v_filtered[i:i + window]
        rms_chunk = math.sqrt(np.mean(chunk ** 2))
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


# ── Technique 7a: Geometry correction (rider exposure) ───────────────────────
#
# Raw vertical at the phone overstates body-relevant vibration because the
# caster-fork mount couples directly to the small front wheel. Wolf 2005 and
# Garcia-Mendez 2013 measure the overstate at ~3× for rigid caster-fork
# mounts. Vasil's mount is a fabric strap above the caster (compliant), so
# the factor lands between caster-fork and seat-tube — 2.0 by default,
# overridable from a future waytrace_calibrate run.

def compute_rider_exposure(a_w: np.ndarray,
                           geom_factor: float = GEOMETRY_FACTOR_CASTER_COMPLIANT
                           ) -> np.ndarray:
    """Approximate the rider-seat Wk-weighted vertical from the phone's
    Wk-weighted vertical, by dividing by the mount geometry factor."""
    if geom_factor <= 0:
        return a_w
    return a_w / geom_factor


# ── Technique 7b: ED — vibration energy density delivered per meter ─────────
#
# Operational definition: the work integral of the vertical vibration force
# acting on the rolling mass, divided by the distance pushed.
#
#   F_w(t) = m × a_w(t)             [N]    vibration force
#   v_w(t) = ∫ a_w(t) dt            [m/s]  Wk-weighted vertical velocity
#   P(t)   = |F_w · v_w|            [W]    instantaneous absolute mech power
#   E      = ∫ P dt                 [J]    total energy delivered
#   ED     = E / distance           [J/m]  = N, interpretable as the average
#                                          vertical resistive force exerted
#                                          on the rolling mass by the road.
#
# NAMING NOTE — see RECONCILIATION-LEDGER.md Part A: this metric was briefly
# called "RFC" in the first OSI-006b commit, but RFC is reserved for the
# 0-10 dimensionless relative-fatigue-cost scale. The physical-energy-per-
# meter metric is ED (Energy Density). The actual RFC formula lives in the
# open-streets-initiative repo (commands/report.py) and is a separate score.

def compute_ed_j_per_m(a_w: np.ndarray, fs: float, distance_m: float,
                       mass_kg: float = TOTAL_ROLLING_MASS_KG) -> float:
    if not distance_m or distance_m <= 0:
        return float('nan')
    # Mean-subtract before integrating so we don't accumulate a velocity drift.
    v_w = np.cumsum(a_w - a_w.mean()) / fs
    dt = 1.0 / fs
    e_total = mass_kg * float(np.sum(np.abs(a_w * v_w))) * dt
    return e_total / distance_m


# ── Technique 7c: ISO 8608 road-roughness class A–H ─────────────────────────
#
# Standard road classification from the displacement PSD evaluated at
# reference spatial frequency n₀ = 0.1 cycles/m. We have acceleration vs
# time, so the conversion is:
#
#   1. Welch PSD of a_w(t) → G_a(f)              [(m/s²)² / Hz]
#   2. Displacement PSD: G_d(f) = G_a(f) / (2π f)⁴    [m² / Hz]
#   3. Convert temporal → spatial freq via mean speed v̄:
#        n = f / v̄          [cycles / m]
#        G_d(n) = G_d(f) × v̄                       [m² · m = m³]
#   4. Interpolate G_d at n₀ = 0.1 cyc/m → look up class.

def compute_iso8608_class(a_w: np.ndarray, fs: float, mean_speed_ms: float):
    if not mean_speed_ms or mean_speed_ms <= 0.1:
        return None, float('nan')
    # Welch PSD of acceleration. 16 s window gives 0.0625 Hz resolution, enough
    # for n₀=0.1 cyc/m at walking speed (~1 m/s → 0.1 Hz) without extrapolation.
    nperseg = min(int(16 * fs), len(a_w))
    f, g_a = signal.welch(a_w, fs=fs, nperseg=nperseg)
    f = f[1:]; g_a = g_a[1:]              # skip f=0 to avoid /0
    g_d_temporal = g_a / (2.0 * np.pi * f) ** 4
    n = f / mean_speed_ms
    g_d_spatial = g_d_temporal * mean_speed_ms
    # ISO 8608 assumes G_d(n) = G_d(n₀)·(n/n₀)^(-w) with w≈2. Fit log-log in
    # the band 0.1 ≤ n ≤ 10 cyc/m (the standard's range of interest) and
    # extrapolate to n₀ if our lowest sample falls above 0.1 — this is the
    # case at wheelchair speeds (slower vehicle → higher minimum spatial freq).
    log_n = np.log(n); log_g = np.log(np.clip(g_d_spatial, 1e-30, None))
    mask = (n >= max(n.min(), ISO8608_N0 * 0.5)) & (n <= 10.0)
    if mask.sum() < 4:
        return None, float('nan')
    slope, intercept = np.polyfit(log_n[mask], log_g[mask], 1)
    gd_n0 = float(np.exp(slope * np.log(ISO8608_N0) + intercept))
    for letter, upper in ISO8608_CLASS_LIMITS:
        if gd_n0 < upper:
            return letter, gd_n0
    return 'H', gd_n0


# ── Spatial header (from GPX) ────────────────────────────────────────────────

def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def parse_gpx(path: Path):
    """Return ndarray of (lat, lon) for every <trkpt>."""
    ns = 'http://www.topografix.com/GPX/1/1'
    pts = []
    for tp in ET.parse(path).getroot().iter(f'{{{ns}}}trkpt'):
        pts.append((float(tp.attrib['lat']), float(tp.attrib['lon'])))
    return np.array(pts) if pts else np.zeros((0, 2))


def auto_discover_gpx(csv_path: Path) -> Path | None:
    """Look for GPS-<same-timestamp>.gpx next to ART-<timestamp>.csv."""
    m = re.search(r'(\d{12})', csv_path.name)
    if not m:
        return None
    candidate = csv_path.parent / f'GPS-{m.group(1)}.gpx'
    return candidate if candidate.exists() else None


def compute_spatial_header(gpx_path: Path, duration_s: float,
                           effective_duration_s: float | None = None):
    """Distance, mean speed, bounding box, start/end coords. All from GPX.
    If `effective_duration_s` is provided (= duration minus any pause-gaps
    > 30 s in the accel timestamps), mean speed is computed against that
    instead — gives the actual pushing speed when a session contains a
    long pause for a coffee break."""
    pts = parse_gpx(gpx_path)
    if len(pts) < 2:
        return None
    lat = pts[:, 0]; lon = pts[:, 1]
    seg = _haversine_m(lat[:-1], lon[:-1], lat[1:], lon[1:])
    distance_m = float(seg.sum())
    effdur = effective_duration_s if effective_duration_s else duration_s
    speed_ms = distance_m / effdur if effdur > 0 else 0.0
    return {
        'start':       (float(lat[0]),  float(lon[0])),
        'end':         (float(lat[-1]), float(lon[-1])),
        'bbox':        (float(lat.min()), float(lat.max()),
                        float(lon.min()), float(lon.max())),
        'distance_m':  distance_m,
        'speed_ms':    speed_ms,
        'duration_s':  duration_s,
        'effective_s': effdur,
        'gpx_name':    gpx_path.name,
    }


def effective_duration(accel: pd.DataFrame, gap_threshold_s: float = 30.0
                       ) -> tuple[float, list[tuple[float, float]]]:
    """Subtract big gaps in the accel timestamps from the total duration.
    Returns (effective_seconds, list of (t_start, gap_seconds) for each gap)."""
    t = accel['t_s'].to_numpy()
    if len(t) < 2:
        return float(t[-1] - t[0]) if len(t) else 0.0, []
    dt = np.diff(t)
    big = np.where(dt > gap_threshold_s)[0]
    gaps = [(float(t[i]), float(dt[i])) for i in big]
    total = float(t[-1] - t[0])
    effective = total - float(dt[big].sum())
    return effective, gaps


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
                events, session_name, out_path):

    bump_times = events[events['kind'] == 'bump']['t_s'].values if not events.empty else np.array([])

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

    bump_count       = int((events['kind'] == 'bump').sum()) if not events.empty else 0
    heavy_bump_count = int((events['kind'] == 'heavy_bump').sum()) if not events.empty else 0
    tilt_count       = int(events['kind'].isin(['tilt', 'wheelie']).sum()) if not events.empty else 0

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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('csv', help='Input ART-YYYYMMDDHHMM.csv')
    ap.add_argument('--gpx', help='Override GPX path (default: auto-discover '
                                  'GPS-<same-timestamp>.gpx next to the CSV).')
    ap.add_argument('--geom-factor', type=float,
                    default=GEOMETRY_FACTOR_CASTER_COMPLIANT,
                    help='Mount geometry factor for rider exposure (default '
                         f'{GEOMETRY_FACTOR_CASTER_COMPLIANT} for fabric-strap '
                         'pocket over the caster).')
    ap.add_argument('--mass', type=float, default=TOTAL_ROLLING_MASS_KG,
                    help=f'Rolling mass in kg for ED (default '
                         f'{TOTAL_ROLLING_MASS_KG}).')
    args = ap.parse_args()

    csv_path = Path(args.csv)
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

    generation, sensors_present = detect_generation(df, csv_path)
    fs = detect_sample_rate(accel)
    duration_s = float(accel['t_s'].iloc[-1])
    print(f"File generation: {generation_banner(generation, sensors_present)}")
    print(f"Rows loaded   : {len(df)} total ({len(accel)} accel, {len(gyro)} gyro)")
    print(f"Duration      : {duration_s:.1f} s")
    print(f"Sample rate   : {fs:.1f} Hz")

    # ── Effective duration (subtract any pause-gaps in the recording) ──────
    effdur_s, gaps = effective_duration(accel)
    if gaps:
        print(f"\nRecording gaps (pause+resume) > 30 s:")
        for t_at, gap_s in gaps:
            print(f"  at {t_at/60:.1f} min: {gap_s/60:.1f} min paused")
        print(f"Effective recording time: {effdur_s/60:.1f} min "
              f"(gross {duration_s/60:.1f} min)")

    # ── Spatial header (GPX) ────────────────────────────────────────────────
    gpx_path = Path(args.gpx) if args.gpx else auto_discover_gpx(csv_path)
    spatial = None
    if gpx_path and gpx_path.exists():
        spatial = compute_spatial_header(gpx_path, duration_s, effdur_s)
    if spatial:
        print(f"\nSpatial (from {spatial['gpx_name']}):")
        s_lat, s_lon = spatial['start']
        e_lat, e_lon = spatial['end']
        bl_lat, tr_lat, bl_lon, tr_lon = spatial['bbox']
        print(f"  start         : {s_lat:.5f}, {s_lon:.5f}")
        print(f"  end           : {e_lat:.5f}, {e_lon:.5f}")
        print(f"  bbox          : {bl_lat:.4f}–{tr_lat:.4f}, {bl_lon:.4f}–{tr_lon:.4f}")
        print(f"  distance      : {spatial['distance_m']/1000:.2f} km")
        print(f"  mean speed    : {spatial['speed_ms']*3.6:.2f} km/h "
              f"(over {spatial['effective_s']/60:.1f} min of pushing)")
    else:
        print("\nSpatial: no GPX found (skipped distance, mean-speed, ISO 8608)")

    # ── Wk-weighted vertical: ISO 2631-1 canonical input ────────────────────
    # OSI-023: for new-chair (backrest hanging pocket) data, apply a 2.0 Hz
    # high-pass to remove the pocket pendulum band (peak ~1.07 Hz, measured
    # 2026-06-24). Old-chair caster data keeps the legacy 0.4 Hz low edge.
    new_chair = is_new_chair_recording(args.csv)
    pocket_hp = 2.0 if new_chair else 0.4
    a_w = wk_weighted_vertical(accel, fs, pocket_hp_hz=pocket_hp)
    if new_chair:
        print(f"\nOSI-023 mount note: NEW chair (backrest hanging pocket)")
        print(f"  Wk high-pass: {pocket_hp:.1f} Hz "
              f"(removes pocket pendulum band; legacy was 0.4 Hz)")
    else:
        print(f"\nMount: OLD chair (caster strap) — Wk low edge "
              f"{pocket_hp:.1f} Hz (legacy)")
    t_s_arr = accel['t_s'].to_numpy()

    # Per-technique calls.
    freqs, psd                          = compute_fft(accel, fs)
    dom_band, band_power                = dominant_band(freqs, psd)
    # Road-profile (raw phone, no geometry correction).
    rms_full, t_rms, rms_windows, pct_bad, pct_mod = compute_rms(
        a_w, t_s_arr, fs)
    vdv, vdv_risk                       = compute_vdv(a_w, fs)
    # Legacy raw-magnitude RMS, kept for backwards comparison with old reports.
    rms_raw, _, _, _, _                 = compute_rms(
        accel['vibration'].to_numpy(), t_s_arr, fs)
    vdv_raw, vdv_raw_risk               = compute_vdv(
        accel['vibration'].to_numpy(), fs)
    # Rider exposure (geometry correction).
    a_w_rider                           = compute_rider_exposure(
        a_w, args.geom_factor)
    rms_rider, _, _, _, _               = compute_rms(
        a_w_rider, t_s_arr, fs)
    vdv_rider, vdv_rider_risk           = compute_vdv(a_w_rider, fs)
    # Other techniques.
    f_stft, t_stft, stft_mag            = compute_stft(accel, fs)
    t_jerk, jerk, obstacles             = compute_jerk(accel, fs)
    iri, iri_condition, _               = compute_iri(accel, fs)
    stats                               = compute_stats(accel)

    # ── ED J/m  energy density per meter (needs distance from GPX) ──────────
    distance_m = spatial['distance_m'] if spatial else 0.0
    ed = compute_ed_j_per_m(a_w, fs, distance_m, args.mass)
    # ── ISO 8608 class (needs mean speed from GPX) ──────────────────────────
    mean_speed = spatial['speed_ms'] if spatial else 0.0
    iso_class, gd_n0 = compute_iso8608_class(a_w, fs, mean_speed)

    # Offline event detection — works the same for v1, v2, and v3 files.
    events = detect_events_offline(accel, gyro)
    bump_count       = int((events['kind'] == 'bump').sum())
    heavy_bump_count = int((events['kind'] == 'heavy_bump').sum())
    wheelie_count    = int((events['kind'] == 'wheelie').sum())
    tilt_count       = int((events['kind'] == 'tilt').sum())

    # ── Console summary ─────────────────────────────────────────────────────
    print(f"\nRoad profile (raw phone, Wk-weighted vertical):")
    if iso_class:
        print(f"  ISO 8608 class : {iso_class}  (Gd(n₀)={gd_n0:.2e} m³)"
              f"  →  {ISO8608_LABELS.get(iso_class, '')}")
    print(f"  RMS (Wk, raw)  : {rms_full:.3f} m/s²")
    print(f"  VDV (Wk, raw)  : {vdv:.2f} m/s^1.75  →  {vdv_risk} health risk")
    print(f"  IRI proxy      : {iri:.1f} m/km (custom RMS×12 scaling)  →  {iri_condition}")

    print(f"\nRider exposure (geometry factor {args.geom_factor:.1f}):")
    print(f"  RMS (Wk, rider): {rms_rider:.3f} m/s²")
    print(f"  VDV (Wk, rider): {vdv_rider:.2f} m/s^1.75  →  {vdv_rider_risk} health risk")

    print(f"\nEnergy density (ED, J/m — NOT the dimensionless RFC; see RECONCILIATION-LEDGER):")
    if np.isfinite(ed):
        print(f"  ED             : {ed:.2f} J/m  (m={args.mass:.0f} kg, "
              f"distance {distance_m/1000:.2f} km from GPX)")
    else:
        print(f"  ED             : n/a (no distance — GPX missing)")

    print(f"\nLegacy (raw |a|−g magnitude, pre-ISO-audit):")
    print(f"  RMS (raw |a|)  : {rms_raw:.3f} m/s²")
    print(f"  VDV (raw |a|)  : {vdv_raw:.2f} m/s^1.75  →  {vdv_raw_risk} health risk")

    print(f"\nEvents:")
    print(f"  Dominant freq band     : {dom_band}")
    print(f"  Bumps ≥{BUMP_MAG} m/s²        : {bump_count}")
    print(f"  Heavy bumps ≥{HEAVY_BUMP_MAG} m/s² : {heavy_bump_count}")
    print(f"  Wheelies / tilts (≥{ANGULAR_RATE} rad/s): {wheelie_count} / {tilt_count}")
    print(f"  Jerk obstacles         : {len(obstacles)}")
    print(f"  Max magnitude          : {stats['max']:.2f} m/s²")
    print(f"  p95 magnitude          : {stats['p95']:.2f} m/s²")

    # One-liner log entry (now with the ISO-correct numbers and class).
    iso_str = f"ISO8608:{iso_class}" if iso_class else "ISO8608:n/a"
    ed_str = f"{ed:.1f}J/m" if np.isfinite(ed) else "n/a"
    log_line = (
        f"Session {session_name} | "
        f"Duration: {duration_s:.1f}s | "
        f"{iso_str} | "
        f"RMS_Wk: {rms_full:.2f} m/s² | "
        f"VDV_Wk: {vdv:.1f} ({vdv_risk}) | "
        f"VDV_rider: {vdv_rider:.1f} ({vdv_rider_risk}) | "
        f"ED: {ed_str} | "
        f"IRI proxy: {iri:.1f} m/km | "
        f"Bumps: {bump_count} | "
        f"Heavy bumps: {heavy_bump_count} | "
        f"Obstacles: {len(obstacles)}"
    )
    print(f"\n{log_line}")

    # ── Text report ─────────────────────────────────────────────────────────
    sections = [f"File generation: {generation_banner(generation, sensors_present)}\n"]
    sections.append(f"Sample rate: {fs:.1f} Hz")
    sections.append(f"Duration: {duration_s:.1f} s\n")
    if spatial:
        s_lat, s_lon = spatial['start']; e_lat, e_lon = spatial['end']
        bl_lat, tr_lat, bl_lon, tr_lon = spatial['bbox']
        sections.append(
            "Spatial:\n"
            f"  start         : {s_lat:.5f}, {s_lon:.5f}\n"
            f"  end           : {e_lat:.5f}, {e_lon:.5f}\n"
            f"  bbox          : {bl_lat:.4f}–{tr_lat:.4f}, {bl_lon:.4f}–{tr_lon:.4f}\n"
            f"  distance      : {spatial['distance_m']/1000:.2f} km\n"
            f"  mean speed    : {spatial['speed_ms']*3.6:.2f} km/h\n"
            f"  gpx source    : {spatial['gpx_name']}"
        )
    road_block = ["Road profile (raw phone, Wk-weighted vertical):"]
    if iso_class:
        road_block.append(f"  ISO 8608 class : {iso_class}  (Gd(n₀)={gd_n0:.2e} m³)")
    road_block.append(f"  RMS (Wk, raw)  : {rms_full:.3f} m/s²")
    road_block.append(f"  VDV (Wk, raw)  : {vdv:.2f} m/s^1.75 ({vdv_risk})")
    road_block.append(f"  IRI proxy      : {iri:.1f} m/km ({iri_condition})")
    sections.append("\n".join(road_block))
    sections.append(
        "Rider exposure (geometry factor {0:.1f}):\n"
        "  RMS (Wk, rider): {1:.3f} m/s²\n"
        "  VDV (Wk, rider): {2:.2f} m/s^1.75 ({3})".format(
            args.geom_factor, rms_rider, vdv_rider, vdv_rider_risk)
    )
    sections.append(
        f"Energy density (ED, J/m):\n"
        f"  ED : {ed:.2f} J/m  (m={args.mass:.0f} kg, "
        f"distance from GPX)" if np.isfinite(ed)
        else "Energy density (ED, J/m):\n  ED : n/a (GPX missing)"
    )
    sections.append(
        f"Legacy (raw |a|−g magnitude, pre-ISO-audit):\n"
        f"  RMS (raw |a|) : {rms_raw:.3f} m/s²\n"
        f"  VDV (raw |a|) : {vdv_raw:.2f} m/s^1.75 ({vdv_raw_risk})"
    )
    sections.append(
        "Events:\n" + log_line
    )
    sections.append("Band power:\n" +
                    "\n".join(f"  {k}: {v:.4f}" for k, v in band_power.items()))
    sections.append("Stats:\n" +
                    "\n".join(f"  {k}: {v:.4f}" for k, v in stats.items()))
    txt_path.write_text("\n\n".join(sections) + "\n", encoding='utf-8')

    # Plot (unchanged — top-line numbers still come from the same techniques).
    print(f"\nGenerating chart → {png_path.name} ...")
    plot_report(accel, freqs, psd, dom_band, band_power,
                t_rms, rms_windows,
                f_stft, t_stft, stft_mag,
                t_jerk, jerk, obstacles,
                stats, rms_full, vdv, vdv_risk, iri, iri_condition,
                events, session_name, png_path)

    print(f"Done. Report saved to:\n  {png_path}\n  {txt_path}")


if __name__ == '__main__':
    main()
