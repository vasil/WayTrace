# WayTrace

Part of the **Open Streets Initiative** — accessibility and pavement
quality mapping through sensor data collection.

WayTrace is an Android app that records the physical experience of moving
through a city in a wheelchair (or any other rolling/walking device), and
a set of Python tools that turn that raw sensor data into quantified
road-quality metrics suitable for OpenStreetMap contribution and urban
accessibility advocacy.

---

## Project layout

```
WayTrace/
├── app/                       Android app source (Kotlin, AGP 8.13, SDK 34)
├── waytrace_analysis.py       7-technique signal analysis toolkit
├── waytrace_merge.py          Combine multi-part recordings into one session
├── waytrace_strava.py         Fetch GPS track from the latest Strava activity
├── waytrace_locate.py         Combine ART CSV + GPX → bad-spot map of the route
├── waytrace_calibrate.py      Derive a per-chair calibration profile from a 7-pin session
├── waytrace_denoise.py        Clean helmet-camera audio noise (analyze + filter modes)
├── waytrace_fetch.py          Pull video files from Akaso V50 X over WiFi
├── README.md                  This file
├── MERGE.md                   How to merge multi-part sessions
├── STRAVA.md                  One-time setup for the Strava API client
├── CALIBRATION.md             5-minute per-chair calibration protocol
├── DENOISE.md                 Manual for waytrace_denoise.py
├── LITERATURE-...md           Wheelchair-vibration prior-art brief (ISO 2631-1 etc.)
└── SRS-v2-full-sensor-iso.md  Spec for the v2 multi-sensor upgrade
```

---

## The Android app

Records **accelerometer + gyroscope at 60 Hz** (Xiaomi hardware ceiling)
from a phone mounted on the right side of the wheelchair, above the front
right caster wheel.

**Foreground service:** keeps recording when the screen is off, the app is
backgrounded, or the system tries to kill it.

**Crash recovery:** state is persisted to SharedPreferences every second.
If Android kills the process during a long pause, reopening the app
restores to PAUSED so the same CSV file can be appended to.

**File naming:** `ART-YYYYMMDDHHMM.csv` (Acceleration, Rotation, Time)
saved to **Downloads** via the MediaStore API.

### CSV format (v3 — current build)

Six columns, exactly:

```
timestamp_ms,sensor,x,y,z,rotvec_w
```

| column | meaning |
|---|---|
| `timestamp_ms` | milliseconds since device boot |
| `sensor`       | `accel`, `gyro`, `gravity`, `mag`, `rotvec`, `pressure`, `pinpoint` |
| `x, y, z`      | meaning depends on `sensor` (see table below) |
| `rotvec_w`     | only populated on `rotvec` rows — quaternion W component. Empty otherwise. |

| sensor    | x          | y          | z          | rotvec_w | units    | notes |
|---        |---         |---         |---         |---       |---       |---    |
| `accel`   | forward    | vertical   | lateral    | empty    | m/s²     | raw, gravity-included |
| `gyro`    | roll       | yaw        | pitch      | empty    | rad/s    | |
| `gravity` | gravity-x  | gravity-y  | gravity-z  | empty    | m/s²     | software-fused gravity vector |
| `mag`     | field-x    | field-y    | field-z    | empty    | µT       | calibrated magnetometer (`TYPE_MAGNETIC_FIELD`) |
| `rotvec`  | quat-x     | quat-y     | quat-z     | **W**    | unit-quat| 4-D orientation; W is the scalar component |
| `pressure`| hPa        | (empty)    | (empty)    | empty    | hPa      | barometer (where the device has one) |
| `pinpoint`| **N**      | 0          | 0          | empty    | counter  | `x = N` is the pinpoint counter (1, 2, 3…) |

**No event detection in v3.** From v3 on, the app records only raw sensor
data. Events (`bump`, `heavy_bump`, `wheelie`, `tilt`) are detected
**offline** by `waytrace_analysis.py` and `waytrace_locate.py` from the
raw magnitudes. This means you can retune thresholds (or invent new
event types) without rebuilding the APK.

### Backwards compatibility

The Python tools read **all three** schema generations and produce
identical reports. The generation is inferred primarily from the
`YYYYMMDDHHMM` timestamp in the filename, with the CSV header used to
confirm.

| generation | filename date range | column 6 | sensors | event detection |
|---|---|---|---|---|
| **v1** | before `2026-05-14` | `event` (bump/heavy_bump/wheelie/tilt/fall) | accel, gyro, pinpoint | in-app, written to col 6 |
| **v2** | `2026-05-14` – `2026-05-16` | `event` (events + rotvec W) | + gravity, mag, rotvec, pressure | in-app, written to col 6 |
| **v3** | from `2026-05-17` | `rotvec_w` (only W on rotvec rows) | same as v2 | offline, in Python tools |

For v1 and v2 files the Python tools ignore the in-CSV event labels and
re-compute them from raw magnitudes, so cross-generation counts are
directly comparable.

### Axis mapping (phone in portrait, screen facing rider)

| axis | meaning |
|---|---|
| Y_accel | VERTICAL (carries ~9.8 m/s² gravity) |
| X_accel | FORWARD / BACKWARD (direction of travel) |
| Z_accel | LATERAL (side to side) |
| Y_gyro  | YAW (turning) |
| X_gyro  | ROLL (tip-over risk) |
| Z_gyro  | PITCH (wheelie / forward-back tipping) |

### UI

A single full-screen button doubles as the state indicator. The rule is:

> **Background = current STATE. Text color = next ACTION (previews next state).**

| button | bg | text |
|---|---|---|
| START   | grey   | green  |
| PAUSE   | green  | orange |
| RESUME  | orange | green  |
| STOP    | red    | white  |
| PIN N   | blue   | white  |

The status line below the button shows
`ART-202605091751.csv  04:33  1.2MB`
(filename + elapsed `MM:SS` + current file size).

### Building

```bash
cd app
./gradlew assembleDebug
# APK lands in app/build/outputs/apk/debug/WT-YYYYMMDDHHMM.apk
```

The build script renames the APK with a timestamp so successive builds
do not overwrite each other.

---

## Python analysis toolkit

### `waytrace_analysis.py` — signal analysis

Runs **seven techniques** on a single ART CSV:

1. **FFT** — frequency spectrum, dominant band (push rhythm /
   surface texture / sharp impacts)
2. **RMS** — overall vibration intensity, with ISO 2631-1
   health thresholds
3. **VDV** — vibration dose value (weights sharp peaks heavily,
   relevant for wheelchair users), ISO 2631-1
4. **STFT** — spectrogram showing how surface character changes
   over time, with bump events overlaid
5. **Jerk** — rate of change of acceleration, distinguishes
   continuous roughness from discrete obstacles
6. **IRI estimate** — international roughness index proxy in m/km
7. **Statistical profile** — mean, std, skewness, kurtosis,
   percentiles (p50 / p90 / p95 / p99), max

```bash
python3 waytrace_analysis.py ART-YYYYMMDDHHMM.csv
```

Produces, in the same folder:

- `ANL-YYYYMMDDHHMM.png` — 5-panel chart with all techniques laid out
- `ANL-YYYYMMDDHHMM.txt` — one-line log entry + band power + stats

### `waytrace_merge.py` — combine multi-part recordings

When one ride was split across multiple ART files (the app was paused
and restarted, or the phone restarted), use this to merge them into one
continuous session before analysis.

```bash
python3 waytrace_merge.py PART_1.csv PART_2.csv [PART_3.csv ...]
# or with a glob:
python3 waytrace_merge.py ~/Downloads/ART-202605091*.csv
```

See [MERGE.md](MERGE.md) for full details — when to use it, how the
gap-closing works, what the seam markers mean.

### `waytrace_strava.py` — fetch GPS from Strava

Sensor recordings on the phone don't include GPS; the Strava app
records GPS for the same ride in parallel. This script pulls the
most recent activity via the Strava API and writes the standard
`GPS-YYYYMMDDHHMM.gpx` file alongside it.

```bash
python3 waytrace_strava.py --auth          # one-time browser OAuth
python3 waytrace_strava.py --latest        # → ~/Downloads/GPS-*.gpx
python3 waytrace_strava.py --activity-id N # specific activity by id
```

First-time setup (Strava API app creation, ~3 minutes) is in
[STRAVA.md](STRAVA.md).

### `waytrace_locate.py` — pin road problems to actual streets

Combines an ART sensor CSV with its matching Strava GPX. Aligns by
wall-clock time, detects bad spots (RMS vibration + jerk peaks +
recorded bump events), and plots them on a map of the route.

```bash
python3 waytrace_locate.py ~/Downloads/ART-YYYYMMDDHHMM.csv \
                           ~/Downloads/GPS-YYYYMMDDHHMM.gpx \
                           [--chair NAME]
```

The optional `--chair NAME` argument loads a per-chair calibration
profile from `~/.config/waytrace/chairs/<NAME>.json` if one exists.
See [CALIBRATION.md](CALIBRATION.md) for the 5-minute protocol.

Speed normalisation (Wolf 2005): bad spots crossed at 0.8–1.5 m/s are
ranked in the main table; spots crossed outside that range are listed
separately as "out-of-range hotspots" — useful as a sanity check but
excluded from cross-segment ranking because uncontrolled speed is a
major confound.

Outputs (in the same folder as the ART file):

- `LOC-YYYYMMDDHHMM.png` — route on a map, colored by road roughness,
  numbered pins on the top-10 worst in-range spots
- `LOC-YYYYMMDDHHMM.txt` — ranked text list with lat/lon coordinates,
  speed-at-cross for each segment, and the out-of-range section

### `waytrace_calibrate.py` — derive a per-chair calibration profile

One ~5-minute recording with seven pinpoint taps at known moments
produces a per-chair JSON profile in
`~/.config/waytrace/chairs/<chair-id>.json`. Used by `waytrace_locate.py`
via `--chair` to subtract chair baseline and (eventually) anchor
cross-chair comparisons after a chair change.

```bash
python3 waytrace_calibrate.py ~/Downloads/ART-CAL-*.csv \
        --chair foldable-2026 --mount caster-fork
```

Full protocol in [CALIBRATION.md](CALIBRATION.md).

### `waytrace_denoise.py` — clean helmet-camera audio noise

Action-camera footage of a wheelchair push picks up wind rumble,
caster rolling noise, frame vibration, and rim handling. This script
cleans it up. The video stream is **copied bit-for-bit** (no
re-encoding); only the audio is replaced.

```bash
# 1) Analyse first
python3 waytrace_denoise.py PUSH.mov --analyze

# 2) General clean-up (no extra deps)
python3 waytrace_denoise.py PUSH.mov

# 3) Wheelchair-tuned preset
python3 waytrace_denoise.py PUSH.mov --target-caster

# 4) Custom notch frequencies (Hz)
python3 waytrace_denoise.py PUSH.mov --notch 220,440

# 5) Profile-based — best, needs sox
python3 waytrace_denoise.py PUSH.mov --profile-from 0:05-0:08
```

Full manual in [DENOISE.md](DENOISE.md).

### `waytrace_fetch.py` — pull video from the camera

Connects to the Akaso V50 X helmet camera over WiFi and downloads
the latest `.MP4` / `.MOV` file as `RW-YYYYMMDDHHMM.mp4`.

Camera SSID: `AKASO_V50X_B-A5D6` | Password: `1234567890`
Camera IP after connect: `192.168.42.1`

```bash
python3 waytrace_fetch.py
```

### Required Python libraries

```bash
pip install numpy scipy matplotlib pandas requests
```

---

## End-to-end workflow for one ride

```bash
# 1. Pull the CSV(s) off the phone (USB or sync) into ~/Downloads/
# 2. (Optional) merge if the ride was recorded in multiple parts
python3 waytrace_merge.py ~/Downloads/ART-202605091751.csv \
                          ~/Downloads/ART-202605091852.csv

# 3. Run the signal analysis on the merged (or single) CSV
python3 waytrace_analysis.py ~/Downloads/ART-MERGED-202605091949.csv

# 4. Fetch GPS from Strava and pin bad spots to the actual street map
python3 waytrace_strava.py --latest
python3 waytrace_locate.py ~/Downloads/ART-MERGED-202605091949.csv \
                           ~/Downloads/GPS-202605091751.gpx
```

Output: a `.png` road-quality report and a `.txt` one-liner you can
paste into a session log or commit message.

---

## File naming conventions

| pattern | what it is |
|---|---|
| `ART-YYYYMMDDHHMM.csv`        | Acceleration / Rotation / Time — raw sensor data |
| `ART-MERGED-YYYYMMDDHHMM.csv` | Multiple ART files merged into one session |
| `ANL-YYYYMMDDHHMM.png/txt`    | Analysis report from `waytrace_analysis.py` |
| `WT-YYYYMMDDHHMM.apk`         | WayTrace Android build |
| `RW-YYYYMMDDHHMM.mp4`         | Rear Window video from the helmet camera |
| `GPS-YYYYMMDDHHMM.gpx`        | GPS track fetched by `waytrace_strava.py` |
| `LOC-YYYYMMDDHHMM.png/txt`    | Location-tagged bad-spot report from `waytrace_locate.py` |

Timestamps are local time — minute resolution, no seconds.

---

## License

Open source — part of Open Streets Initiative.
