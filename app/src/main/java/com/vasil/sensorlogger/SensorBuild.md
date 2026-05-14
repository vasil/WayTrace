# WayTrace sensor CSV tokens (v2)

The Android service writes one CSV per recording session
(`ART-YYYYMMDDHHMM.csv`). The header is **fixed**:

```
timestamp_ms,sensor,x,y,z,event
```

`sensor` is the only field that switches behaviour. All other columns
follow the same convention regardless of generation.

## v1 tokens (every WayTrace build can produce these)

| sensor token | x         | y         | z         | event                                                |
|---           |---        |---        |---        |---                                                   |
| `accel`      | forward   | vertical  | lateral   | empty, or `bump`, `heavy_bump`, `fall`               |
| `gyro`       | roll      | yaw       | pitch     | empty, or `wheelie`, `tilt`                          |
| `pinpoint`   | 0         | 0         | 0         | `pinpoint_N` where N starts at 1 for each session    |

## v2 additions (this build onward — present when device supports them)

| sensor token | x          | y         | z         | event                       | source Android type                       |
|---           |---         |---        |---        |---                          |---                                        |
| `gravity`    | gravity-x  | gravity-y | gravity-z | empty                       | `TYPE_GRAVITY`                            |
| `mag`        | field-x    | field-y   | field-z   | empty                       | `TYPE_MAGNETIC_FIELD` (calibrated)        |
| `rotvec`     | quat-x     | quat-y    | quat-z    | **quat-w** (scalar)         | `TYPE_ROTATION_VECTOR`                    |
| `pressure`   | hPa        | (empty)   | (empty)   | empty                       | `TYPE_PRESSURE`                           |

Notes:

- The high-rate IMU-class sensors (`accel`, `gyro`, `gravity`,
  `mag`, `rotvec`) are all throttled to ~120 Hz by the same
  `INTERVAL_NS = 8_333_333L` ns guard in `RecorderService.kt`.
- `pressure` is naturally low-rate and uncapped.
- `rotvec` uniquely needs four numbers (a unit quaternion). The CSV
  stays 6 columns by riding `w` in the `event` column. The Python
  loader handles this — see `waytrace_analysis.detect_generation()`.
- Any v2 sensor is **optional**. `getDefaultSensor()` returns null on
  devices that lack it; the service simply doesn't emit those rows.
- v1 row formats and meanings are bit-identical to the previous build:
  same `accel`/`gyro` line layout, same `bump`/`heavy_bump` threshold
  detection, same `bumpCount` side effect.
- Early v2 builds also logged `linaccel`, `step`, and `light`. They
  were dropped: `linaccel` is derivable from `accel - gravity`, and
  `step` / `light` were not feeding any analysis. The Python loader
  silently accepts old files that contain those rows.
