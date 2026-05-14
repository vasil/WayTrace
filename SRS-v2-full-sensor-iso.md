# WayTrace SRS update v2 — full-sensor capture and ISO 2631-1 compliant analysis

Status: **in progress** (see § "Implementation status" below)
Author: drafted with Claude Code (on desktop)
Last updated: 2026-05-14

This document defines the additions to the WayTrace Android app and the
Python analysis pipeline needed to (a) make road-quality reporting
ISO 2631-1 compliant rather than heuristic, and (b) capture all
phone-side signals relevant to wheelchair road experience.

The goal: replace the current invented "severity" score with
peer-review-defensible vibration metrics, and add the missing sensors
that explain *why* a section of road is rough (chassis tilt, slope,
heading, etc.) without changing the on-bike hardware.

---

## Implementation status (handoff snapshot)

This section is the **source of truth for where we are**. Read this
first. The detailed sections below are the original spec — some of
their items are now [DONE], some are [TODO], a few are [SUPERSEDED].

### [DONE] Android v2 data capture
- New sensors recorded in `RecorderService.kt`:
  `gravity`, `mag` (calibrated `TYPE_MAGNETIC_FIELD`), `rotvec`,
  `pressure` (absent on this Xiaomi — silently null).
- Manifest declares `compass` and `barometer` as optional `uses-feature`.
- CSV header is **unchanged**: still `timestamp_ms,sensor,x,y,z,event`.
  The quaternion's `w` rides in the `event` column on `rotvec` rows.
- `accel` / `gyro` / `pinpoint` rows are **byte-for-byte identical** to
  v1 builds. Existing on-phone `bump` / `heavy_bump` / `wheelie` / `tilt`
  detection is unchanged. UI is unchanged.
- Stationary acceptance test passes:
  gravity magnitude **9.8067 m/s²**, quaternion magnitude **1.000000**,
  geomagnetic field **51 µT** (Prilep is in the 49 µT band; 100 % of
  samples in 25–65 µT).
- Sample rate confirmed at **60 Hz** (Xiaomi hardware ceiling) when
  the app is foregrounded. One earlier recording showed a transient
  30 Hz throttle; subsequent screen-on / screen-off A/B tests both
  delivered 60 Hz, so the throttle was not reproducible. If it
  reappears, the fix is to whitelist WayTrace in MIUI battery saver.
- APK in the repo: `app/build/outputs/apk/debug/WT-202605140351.apk`.

### [DONE] Python pipeline — generation awareness and speed filter
- `waytrace_analysis.detect_generation()` returns one of `v1`,
  `v2-partial`, `v2-full`. Both `waytrace_analysis.py` and
  `waytrace_locate.py` print the result in their report headers.
- `waytrace_locate.py` has the Wolf-2005 speed-normalisation filter
  built in (0.8–1.5 m/s window). In-range segments are ranked in the
  headline table; out-of-range hotspots get a separate sanity-check
  section. Each ranked bad spot prints the speed at which it was
  crossed.
- `waytrace_locate.py --chair NAME` accepts a per-chair calibration
  profile from `~/.config/waytrace/chairs/<NAME>.json`. Silent
  fallback when the profile is absent.
- **First v2-full real ride is recorded**: ART-202605141445.csv +
  GPS-202605141445.gpx, 5.62 km, 54m52s, "Data Takes Flight Push".
  Generation banner reads "v2-full — frame-correct ISO analysis
  available".

### [SUPERSEDED] Calibration via seven pinpoint taps
- `waytrace_calibrate.py` exists and works on a 7-pin file, but the
  protocol is impractical: lifting a wheelchair-mounted phone to tap
  the screen mid-ride and remounting it is friction the rider cannot
  reasonably accept. The script will be **rewritten** (see TODO).

### [DONE] Wheelchair-vibration literature review
- `LITERATURE-wheelchair-vibration.md` summarises the canonical
  studies: Wolf 2005, VanSickle 2001, Garcia-Mendez 2013,
  Chénier 2014, Misch 2022, Larivière 2021 systematic review,
  WheelShare / MyPath smartphone work.
- Headline finding: **caster-fork mount overstates body-relevant
  vibration ~3×.** ISO 2631-1 is written for the seat. The phone
  mount should be on the **seat tube near the hip** before any
  ISO-compliant absolute numbers are published.
- 60–120 Hz sampling is **sufficient** for Wk-weighted RMS / VDV /
  MTVV per Garcia-Mendez 2013.
- Relative rankings on the same chair are preserved across rides
  even without calibration; absolute numbers and cross-chair
  comparisons require an anchor.

### [DONE] Repository state
- Committed and pushed to `origin/main` (commit `53302a7`).
- 13 files changed, +2050 / -13.
- `linaccel`, `step`, `light` were dropped before commit (no
  scientific value for the road-quality goal; `linaccel` is
  derivable from `accel - gravity`).

---

## Pending tasks (TODOs ordered by priority)

### [TODO 1] Rewrite `waytrace_calibrate.py` for pin-free auto-segmentation
**Why:** the user cannot tap PIN seven times during a wheelchair
ride. Mounted phone is out of reach; unmounting and remounting
between taps is unworkable.

**How:** the script reads a normal recording and auto-detects:
- **Stationary phase** = longest stretch where `accel` magnitude std
  is < 0.05 m/s² over at least 20 s.
- **Corridor phase** = longest stretch where the rider is *moving*
  (GPS speed in 0.8–1.5 m/s) AND `accel` std is low (smooth surface).
- **Drop events** = top-N sharpest accel peaks separated by > 2 s.

Output JSON profile is identical in shape to today's; no user input
required beyond `--chair NAME --mount LOCATION`.

### [TODO 2] Implement `waytrace_iso.py` — ISO 2631-1 weightings
- Wk filter (vertical seat z-axis), Wd filter (horizontal x/y) as
  IIR biquad cascades per ISO Annex A.
- Weighted RMS, VDV, MTVV, crest factor, 1/3-octave band power,
  Annex C comfort category.
- Acceptance: pass ISO Annex A reference-tone tests (1 / 4 / 8 / 16
  / 31.5 Hz sinusoids must produce published gain values within
  ±0.5 dB).
- Independent of Android side. Can be done now against the existing
  v2-full ride.

### [TODO 3] World-frame transformation in `waytrace_locate.py`
- Per sample: `a_world(t) = R(q(t)) · (a_raw(t) − g(t))`.
- Uses `rotvec` quaternion + `gravity` vector — both already recorded.
- Eliminates the current Y-axis-vertical false positives that happen
  when the chair tilts (curb ramps, wheelies).

### [TODO 4] Wire ISO weighted total `a_v` into bad-spot detection
- Replace the heuristic severity score with `a_v` per 2-second window.
- Thresholds: `> 0.8 m/s²` UNCOMFORTABLE, `> 1.25 m/s²` VERY
  UNCOMFORTABLE, `MTVV / a_v > 9` SHOCK_DOMINATED.
- Keep custom severity as internal debug only.

### [TODO 5] Re-mount the phone on the seat tube
- Hardware action by the user. Until done, all "ISO-compliant"
  numbers in reports carry a "caster-fork mount, overstated ~3×"
  caveat. The literature is clear on this; no software fix.

### [TODO 6] Heading-aware route geometry
- From `rotvec` and `mag`: per-sample compass heading.
- Enables one-way-pothole detection: same lat/lon, different
  direction, asymmetric severity.

### [TODO 7] Helmet-camera fusion — camera-clock offset captured
- **Setup data captured today (2026-05-14):**
  the user shot a frame of a wall-clock with the helmet camera
  while a phone displaying its own clock was visible. The point of
  the exercise is to measure `camera_clock - phone_clock` offset so
  future video can be aligned to sensor/GPS timestamps.
- **Phone-side timestamp captured (from app screenshots, Drive):**
  - `Screenshot_2026-05-14-14-45-11-876_com.vasil.sensorlogger.jpg`
    — phone time **14:45:11.876** at the start of the ride.
  - `Screenshot_2026-05-14-14-45-17-804_com.miui.home.jpg`
    — phone time **14:45:17.804**.
  - `Screenshot_2026-05-14-15-27-26-744_com.vasil.sensorlogger.jpg`
    — phone time **15:27:26.744**, mid-ride.
- **Camera-side data is not yet uploaded** — still on the Akaso SD
  card on `vt-x1`. Once the matching helmet-camera frame is
  available, read the clock-display time and compute:
  `offset = camera_clock_displayed_time - phone_clock_in_screenshot`.
- **Status:** documented, blocked on camera footage transfer.

### [TODO 8] Optional — Bluetooth remote button for mid-ride pinpoints
- Cheap BT shutter remote → app receives a key event → emit a
  `pinpoint_N` row.
- Removes the "lift phone to tap" friction entirely.
- Useful far beyond calibration: marking any landmark on the route.

### [TODO 9] Slope estimation (waiting for hardware)
- `pressure` rows can give altitude via barometric formula; combined
  with GPS speed → road grade.
- **Blocked**: this Xiaomi has no barometer. The handler is in place;
  rows simply never appear. Defer.

---

## Open issues / decisions deferred

- **Sample-rate transient throttling under MIUI.** Seen once, not
  reproducible in A/B tests. If it reappears in real-ride data,
  whitelist WayTrace in MIUI battery saver. No code fix yet.
- **Foldable → rigid chair transition.** Vasil will get a rigid chair
  soon. Cross-chair absolute comparisons require an anchor — see
  TODO 1 (calibration) + the `LITERATURE` doc for the drop-test
  protocol.
- **Today's "Data Takes Flight Push" had zero in-range bad spots.**
  All 5 bad segments were crossed at < 0.21 m/s — start/end/pauses,
  not road. Either the route was genuinely clean, or the still-on-
  caster-fork mount is amplifying start/stop transients. TODO 5
  resolves the ambiguity.

---

(Original specification follows — kept verbatim. Annotate against
the status section above when reading.)

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

| `sensor` token   | Android type                          | units    | status   |
|---               |---                                    |---       |---       |
| `gravity`        | `TYPE_GRAVITY`                        | m/s²     | [DONE]   |
| `linaccel`       | `TYPE_LINEAR_ACCELERATION`            | m/s²     | [DROPPED] derivable from `accel - gravity` |
| `mag`            | `TYPE_MAGNETIC_FIELD` (calibrated)    | µT       | [DONE]   |
| `rotvec`         | `TYPE_ROTATION_VECTOR`                | quat     | [DONE]   |
| `pressure`       | `TYPE_PRESSURE`                       | hPa      | [DONE] (sensor absent on this Xiaomi) |
| `step`           | `TYPE_STEP_DETECTOR`                  | event    | [DROPPED] not useful for wheelchair |
| `light`          | `TYPE_LIGHT`                          | lx       | [DROPPED] no analysis uses it |

Keep the existing rows: `accel`, `gyro`, `pinpoint`. [DONE — unchanged.]

### 2.2 Updated CSV format
[DONE.] Six columns stay the same. `rotvec` rides `w` in the `event`
column for the one row that needs four numbers.

### 2.3 Sampling and storage
[DONE.] All sensors at 8333 µs request. Actual delivered rate ≈ 60 Hz.
Foreground service unchanged. SharedPreferences crash-recovery unchanged.

### 2.4 SRS-derived UI requirements
[DONE.] Zero UI changes shipped.

### 2.5 SRS-derived test requirements
[DONE.] All three acceptance tests pass.

---

## 3. Analysis-side changes (Python)

### 3.1 `waytrace_iso.py` — [TODO 2]
Not yet implemented. Pure-function module with Wk/Wd filters, weighted
RMS, VDV, MTVV, crest factor, 1/3-octave bands, comfort category.
Will live in its own file so it can be unit-tested against ISO Annex
A reference tones.

### 3.2 ISO 2631-1 weighting filters — [TODO 2]
Wk and Wd biquad cascades per ISO Annex A.

### 3.3 Metrics computed per ride — [TODO 2]
Output: a new `ISO-YYYYMMDDHHMM.txt` file with one section per
metric. The existing custom `severity` score will be retained as
internal debug but removed from the headline ranking.

### 3.4 Frame transformation — [TODO 3]
`a_world(t) = R(q(t)) · (a_raw(t) − g(t))`. Required for ISO axis-
correctness on a tilting chair.

### 3.5 Heading and route geometry — [TODO 6]

### 3.6 Push cadence — [DROPPED with `step` sensor]

### 3.7 Updated `waytrace_locate.py` — partially done
- [DONE] Speed normalisation (Wolf 2005 window).
- [DONE] `--chair` argument.
- [DONE] Generation banner in report headers.
- [TODO 4] Bad-spot detection driven by ISO `a_v` instead of the
  custom severity score.

---

## 4. Outputs (per ride)

| file                          | content                                           | status   |
|---                            |---                                                |---       |
| `ART-YYYYMMDDHHMM.csv`         | raw multi-sensor log (Android side)              | [DONE]   |
| `ANL-YYYYMMDDHHMM.png/txt`    | existing 5-panel analysis                         | [DONE — generation banner added] |
| `ISO-YYYYMMDDHHMM.txt`        | ISO 2631-1 weighted metrics + category            | [TODO 2] |
| `LOC-YYYYMMDDHHMM.png/txt`    | map + bad-spot ranking, driven by `a_v`           | [PARTIAL — current builds use heuristic severity, switch to `a_v` is TODO 4] |
| `GPS-YYYYMMDDHHMM.gpx`        | Strava-fetched track                              | [DONE]   |
| `CAL-<chair>.json`            | per-chair calibration profile                     | [TODO 1] |

---

## 5. Implementation order (suggested)
1. [DONE] `waytrace_iso.py`... wait — that's still TODO 2 above. Was
   listed first in the original plan because it's pure-DSP and
   independent of Android. Promote it as the next-up task.
2. [DONE] Android: gravity, rotvec, mag, pressure.
3. [TODO 3] Frame transformation in `waytrace_locate.py`.
4. [TODO 4] Wire `a_v` into bad-spot detection.
5. [TODO 6] Heading / slope features.

---

## 6. What stays out of scope
- Audio recording (privacy + storage cost).
- Real-time on-phone analysis.
- Frame-by-frame fusion with the helmet camera — that is **TODO 7**
  in this update.

---

## 7. References
See `LITERATURE-wheelchair-vibration.md` for the full prior-art
brief. Key citations: ISO 2631-1:1997 Amd 1:2010; Griffin's *Handbook
of Human Vibration*; Wolf et al. 2005; Garcia-Mendez et al. 2013;
Misch & Sprigle 2022; Larivière et al. 2021 systematic review.
