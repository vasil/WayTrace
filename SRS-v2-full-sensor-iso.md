# WayTrace SRS update v2 — full-sensor capture and ISO 2631-1 compliant analysis

Status: **specification, not yet implemented**
Author: drafted with Claude Code
Date: 2026-05-14

This document defines the additions to the WayTrace Android app and the
Python analysis pipeline needed to (a) make road-quality reporting
ISO 2631-1 compliant rather than heuristic, and (b) capture all
phone-side signals relevant to wheelchair road experience.

The goal: replace the current invented "severity" score with
peer-review-defensible vibration metrics, and add the missing sensors
that explain *why* a section of road is rough (chassis tilt, slope,
heading, etc.) without changing the on-bike hardware.

---

## 1. Why this change

The current pipeline detects bumps and locates them on a map. That
part is solid. The "severity" score that ranks them, however, mixes
RMS, jerk, and event flags with weights I picked by hand, and it
treats the phone's Y-axis as "vertical" regardless of how the chassis
is actually oriented at that moment. Outside of this project no one
recognises the resulting numbers.

ISO 2631-1 (Mechanical vibration and shock — Evaluation of human
exposure to whole-body vibration) is the standard everyone in
transport engineering, occupational health, and accessibility
research uses. Producing ISO-compliant numbers means the data can be
cited in OSI advocacy materials, EU accessibility complaints, and
research papers without disclaiming that the units are local.

To compute ISO metrics correctly we need to know the **true vertical
axis** at each sample — i.e. we need the gravity vector. We also
need the **forward axis** to separate ride/push direction from
sideways motion. Both are derivable from sensors the phone already
has but the app does not currently log.

---

## 2. Android-side changes (the SRS)

### 2.1 New sensors to register and log

Add the following Android `SensorManager` registrations alongside the
existing accelerometer and gyroscope. All sample at the maximum rate
the device supports up to **60 Hz** (Xiaomi ceiling) and are written
to the same CSV with a new `sensor` value:

| `sensor` token   | Android type                          | units    | notes                                                                                                            |
|---               |---                                    |---       |---                                                                                                               |
| `gravity`        | `TYPE_GRAVITY`                        | m/s²     | low-pass-filtered gravity vector in phone frame                                                                  |
| `linaccel`       | `TYPE_LINEAR_ACCELERATION`            | m/s²     | accel with gravity already removed — convenient for vibration analysis but **not** a substitute for raw accel    |
| `mag`            | `TYPE_MAGNETIC_FIELD_UNCALIBRATED`    | µT       | three-axis magnetometer — for compass heading; uncalibrated variant retains raw readings + hard-iron bias        |
| `rotvec`         | `TYPE_ROTATION_VECTOR`                | quat     | fused orientation, x/y/z/w as a unit quaternion in the SI ENU world frame                                        |
| `pressure`       | `TYPE_PRESSURE`                       | hPa      | barometer — gives altitude (and via differencing: road slope) where the device has one                           |
| `step`           | `TYPE_STEP_DETECTOR`                  | event    | one row per push detection (Xiaomi pedometer; useful as "push cadence" proxy for self-propelled rides)           |
| `light`          | `TYPE_LIGHT`                          | lx       | ambient light, optional; useful only as context (daylight vs streetlight vs tunnel)                              |

Keep the existing rows:

| `sensor` token | source                  |
|---             |---                      |
| `accel`        | `TYPE_ACCELEROMETER`    |
| `gyro`         | `TYPE_GYROSCOPE`        |
| `pinpoint`     | UI button — unchanged   |

### 2.2 Updated CSV format

Six columns stay the same; the meaning of `x,y,z` depends on `sensor`.
For sensors whose native dimensionality is less than three (e.g.
`pressure`, `step`, `light`), the unused columns are empty.

```
timestamp_ms,sensor,x,y,z,event
```

`rotvec` has four components; we extend the convention with a "w" value
stored in the `event` field for that row only:

```
timestamp_ms,sensor,x,y,z,event
1234567,rotvec,0.012,-0.034,0.978,0.205
```

i.e. for `rotvec`, the quaternion is (x, y, z, w) where `w` lives in the
`event` column. This avoids an extra column for one sensor type. The
Python loader (§3.4) handles this.

### 2.3 Sampling and storage

- All sensors are registered with `SENSOR_DELAY_GAME` (≈50 Hz) or a
  faster rate where the device supports it.
- The foreground service keeps them all alive across screen-off and app
  background; the existing crash-recovery (`SharedPreferences` flush
  every second) is extended to cover the new sensors.
- File-size impact estimate: total bytes/second increases from ~600 B/s
  to ~2.3 kB/s. A two-hour push grows from ≈4 MB to ≈16 MB — still
  trivial for the phone and trivial for Drive sync.

### 2.4 SRS-derived UI requirements

No UI changes. The existing single-button state machine is unaffected.
The status line continues to show filename + elapsed + size; with the
new sensors the size grows ~4× faster, which the user is already used
to seeing.

### 2.5 SRS-derived test requirements

A new acceptance test: record 60 seconds of stationary data with the
phone flat on a table, then 60 seconds with the phone in the wheelchair
mount stationary. Both runs must:

1. produce `gravity` rows whose three-axis magnitude is 9.81 ± 0.05 m/s²
2. produce `rotvec` rows whose quaternion magnitude is 1.0 ± 0.001
3. produce `mag` rows whose three-axis magnitude is within 25–65 µT
   (the Earth-field magnitude band)

This validates that the sensors are wired correctly and that the
quaternion convention used by the CSV writer matches the one used by
the analyser.

---

## 3. Analysis-side changes (Python)

### 3.1 New module: `waytrace_iso.py`

Pure-function module implementing ISO 2631-1 weightings and the
metrics derived from them. No I/O. Intended to be imported by
`waytrace_analysis.py` and `waytrace_locate.py`.

Public API:

```python
def wk_filter(signal, fs):                   # Wk weighting (z-axis seated)
def wd_filter(signal, fs):                   # Wd weighting (x,y horizontal)
def weighted_rms(signal, fs, axis):          # axis ∈ {"z","x","y"}
def vdv(signal, fs, axis):                   # vibration dose value
def mtvv(signal, fs, axis, window=1.0):      # max transient vibration value
def crest_factor(signal, fs, axis):          # peak / weighted_rms
def one_third_octave_bands(signal, fs):      # band powers, 0.5 – 80 Hz
def comfort_category(weighted_rms):          # ISO 2631-1 Annex C
```

### 3.2 ISO 2631-1 weighting filters

The Wk and Wd filters are infinite-impulse-response biquad cascades
defined in Annex A of the standard. They are not free — implementing
them from scratch is a half-day of careful work — but they are widely
published and have reference implementations to verify against
(`pyrosm`, `vibration_toolbox`, and Excel templates from EU labs).

Acceptance: pass the ISO 2631-1 Annex A reference-tone tests (sinusoid
at 1, 4, 8, 16, 31.5 Hz must produce the published gain values within
±0.5 dB).

### 3.3 Metrics computed per ride

For each ride (i.e. each ART CSV) the analyser computes and reports:

| metric                | symbol      | unit         | computed from                                |
|---                    |---          |---           |---                                           |
| weighted RMS, z       | `a_wz`      | m/s²         | Wk-weighted vertical (world-frame z) accel   |
| weighted RMS, x       | `a_wx`      | m/s²         | Wd-weighted forward accel                    |
| weighted RMS, y       | `a_wy`      | m/s²         | Wd-weighted lateral accel                    |
| total weighted RMS    | `a_v`       | m/s²         | `√(1.4²·a_wx² + 1.4²·a_wy² + a_wz²)`         |
| **VDV (z)**           | `VDV_z`     | m·s⁻¹·⁷⁵    | `(∫ a_wz(t)⁴ dt)^(1/4)`                      |
| **MTVV (z)**          | `MTVV_z`    | m/s²         | max 1-second running RMS of `a_wz`           |
| **Crest factor (z)**  | `CF_z`      | dimensionless| peak(`a_wz`) / `a_wz`                        |
| Daily exposure A(8)   | `A(8)`      | m/s²         | extrapolated 8 h RMS, ISO §6.4.1             |
| Daily exposure VDV(8) | `VDV(8)`    | m·s⁻¹·⁷⁵    | extrapolated 8 h VDV, ISO §6.4.2             |
| 1/3-octave band power | `Pᵢ`        | m²/s⁴       | 0.5 – 80 Hz, for spectrogram and surface ID  |

Output per ride: a new `ISO-YYYYMMDDHHMM.txt` file with one section per
metric, plus the ISO 2631-1 Annex C comfort category for the
weighted total `a_v`:

```
< 0.315 m/s²            Not uncomfortable
0.315 – 0.63 m/s²       A little uncomfortable
0.5   – 1.0  m/s²       Fairly uncomfortable
0.8   – 1.6  m/s²       Uncomfortable
1.25  – 2.5  m/s²       Very uncomfortable
> 2.0 m/s²              Extremely uncomfortable
```

The existing `severity` score is **removed from the headline
ranking**. It may stay as an internal debug metric, marked as such.

### 3.4 Frame transformation

The biggest single accuracy improvement: rotate accelerations into the
**world frame** before computing anything.

For each sample with timestamp `t`:

1. Find the nearest `rotvec` row → unit quaternion `q(t)`.
2. Find the nearest `gravity` row → gravity vector `g(t)`.
3. Compute `a_linear(t) = a_raw(t) − g(t)` in phone frame.
4. Rotate `a_linear(t)` by `q(t)` to obtain `a_world(t) = R(q) · a_linear`.
5. `a_world.z` is now true vertical, irrespective of how the phone is
   mounted or how the chassis tilts when crossing a curb-ramp.

This eliminates a class of false positives where the current
"Y-axis = vertical" assumption picked up chassis-tilt as if it were a
bump (e.g. tipping back to climb a 5 cm lip looks like a 1 g spike on
phone-Y).

### 3.5 Heading and route geometry

With magnetometer + rotvec we can extract per-sample heading. This
lets the locator:

- distinguish "rough patch when heading east" from the same patch
  westbound (one-way pothole asymmetry)
- compute road slope by combining barometer-derived altitude
  derivative with horizontal speed
- correct bump severity for slope (going downhill loads the chassis
  differently from level)

### 3.6 Push cadence

`step` events from `TYPE_STEP_DETECTOR` give a rough push rhythm. Two
candidate features:

- mean cadence (pushes/min) per ride
- cadence change correlated with surface change (does the rider slow
  pushing on rough surface?)

Cadence is descriptive, not part of the ISO output. Goes into
`waytrace_analysis.py`.

### 3.7 Updated `waytrace_locate.py`

The locator stays the same in shape — find bad spots, plot them on a
map — but bad-spot detection is now driven by the weighted total
`a_v` (per 2-second window) rather than the home-grown severity
score. The thresholds become:

| trigger                                    | flag                |
|---                                         |---                  |
| `a_v` window > 0.8 m/s² (Uncomfortable)    | `UNCOMFORTABLE`     |
| `a_v` window > 1.25 m/s² (Very)            | `VERY_UNCOMFORTABLE`|
| `MTVV / a_v` > 9                           | `SHOCK_DOMINATED`   |
| `VDV` window contribution top 1 %          | `DOSE_HOTSPOT`      |
| `heavy_bump` event in window               | `EVENT`             |

The map and report formats are unchanged; what changes is the words on
the legend and the numbers in the columns.

---

## 4. Outputs (per ride)

After this change a single ride produces:

| file                          | content                                           |
|---                            |---                                                |
| `ART-YYYYMMDDHHMM.csv`         | raw multi-sensor log (Android side)              |
| `ANL-YYYYMMDDHHMM.png/txt`    | existing 5-panel analysis                         |
| `ISO-YYYYMMDDHHMM.txt`        | **new** — ISO 2631-1 weighted metrics + category  |
| `LOC-YYYYMMDDHHMM.png/txt`    | map + bad-spot ranking, driven by `a_v`           |
| `GPS-YYYYMMDDHHMM.gpx`        | Strava-fetched track (unchanged)                  |

---

## 5. Implementation order (suggested)

1. **`waytrace_iso.py`** with Wk/Wd filters and unit tests against
   ISO Annex A reference tones. Independent of Android changes.
2. Android: add `gravity`, `rotvec`, `mag` (in that priority). Test
   with the §2.5 stationary acceptance test.
3. Update `waytrace_locate.py` to run frame transformation when
   `rotvec`/`gravity` are present, fall back to current Y-axis-vertical
   behaviour otherwise (backwards compatible with existing ART files).
4. Wire `a_v` into bad-spot detection. Keep old severity column for
   one release as a regression check.
5. Add `pressure` → slope, `step` → cadence. Lowest priority — they
   are descriptive, not part of the ISO output.

Each step is independently shippable and independently testable.

---

## 6. What stays out of scope

- Audio recording (surface-texture-from-rolling-sound). Privacy and
  storage cost are non-trivial; revisit only if the visual + IMU
  data ever proves insufficient.
- Frame-by-frame fusion with the helmet camera. That is the planned
  next step but is its own SRS document, not part of this one.
- Real-time on-phone analysis. Everything still computes off-line on
  the desktop after the ride.

---

## 7. References

- ISO 2631-1:1997 + Amd 1:2010 — Mechanical vibration and shock —
  Evaluation of human exposure to whole-body vibration — Part 1:
  General requirements
- Griffin, M. J. *Handbook of Human Vibration*, Academic Press, 1990
- Wolf, E., Pearlman, J., et al. *Vibration exposure of individuals
  using wheelchairs over sidewalk surfaces.* Disability and
  Rehabilitation, 2005 — directly relevant prior art for the
  wheelchair use-case.
- Android Sensors API:
  https://developer.android.com/reference/android/hardware/Sensor
