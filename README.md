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
├── app/                    Android app source (Kotlin, AGP 8.13, SDK 34)
├── waytrace_analysis.py    7-technique signal analysis toolkit
├── waytrace_merge.py       Combine multi-part recordings into one session
├── waytrace_fetch.py       Pull video files from Akaso V50 X over WiFi
├── README.md               This file
└── MERGE.md                How to merge multi-part sessions
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

### CSV format

Six columns, exactly:

```
timestamp_ms,sensor,x,y,z,event
```

| column | meaning |
|---|---|
| `timestamp_ms` | milliseconds since device boot |
| `sensor`       | `accel`, `gyro`, or `pinpoint` |
| `x, y, z`      | m/s² (accel) or rad/s (gyro) |
| `event`        | empty, or `bump`, `heavy_bump`, `wheelie`, `tilt`, `fall`, `pinpoint_N` |

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
pip install numpy scipy matplotlib pandas
```

---

## End-to-end workflow for one ride

```bash
# 1. Pull the CSV(s) off the phone (USB or sync) into ~/Downloads/
# 2. (Optional) merge if the ride was recorded in multiple parts
python3 waytrace_merge.py ~/Downloads/ART-202605091751.csv \
                          ~/Downloads/ART-202605091852.csv

# 3. Run the analysis on the merged (or single) CSV
python3 waytrace_analysis.py ~/Downloads/ART-MERGED-202605091949.csv
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
| `GPS-YYYYMMDDHHMM.gpx`        | GPS track exported from Strava (manual) |

Timestamps are local time — minute resolution, no seconds.

---

## License

Open source — part of Open Streets Initiative.
