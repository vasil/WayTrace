# Per-chair calibration (5-minute protocol)

You record this **once per wheelchair** (and once per phone mount
change). Output is a JSON profile at
`~/.config/waytrace/chairs/<chair-id>.json` that downstream analysis
uses to subtract this chair's baseline noise and to anchor cross-chair
comparisons.

The protocol uses the existing WayTrace UI — no new buttons. You start
a normal recording and tap **PIN seven times** at the moments below.

---

## What you need

- The WayTrace Android app, running.
- A smooth indoor corridor where you can push at roughly 1 m/s for
  about a minute (a hallway, a flat lobby — anywhere without bumps).
- A known step or threshold ~5 cm high. A typical curb-cut works; so
  does a wooden block. Mark it once with a tape measure so the same
  height is used on every chair.
- About 5 minutes.

## The seven pin moments

| pin | when                                                          |
|---: |---                                                            |
| **1** | Phone mounted, chair stationary. Press START → press PIN. Sit still ~30 s. |
| **2** | Begin pushing. Tap PIN as soon as you start moving.            |
| **3** | After ~60 s of smooth corridor push, stop pushing. Tap PIN.    |
| **4** | Move to the 5 cm step. Roll off it. Tap PIN at the moment of landing. |
| **5** | Repeat — second drop off the same step. PIN on landing.        |
| **6** | Third drop. PIN on landing.                                    |
| **7** | Stop the chair. Tap PIN one last time, then STOP the recording.|

The pin numbers (`pinpoint_1` … `pinpoint_7`) are written automatically
by the app — you don't have to track them.

## Run the analyser

```bash
python3 waytrace_calibrate.py ~/Downloads/ART-YYYYMMDDHHMM.csv \
        --chair foldable-2026 \
        --mount caster-fork
```

Choices for `--mount`:

| value          | what it means                                                                                       |
|---             |---                                                                                                  |
| `caster-fork`  | phone on the fork above the front wheel (today's default). Overstates body-relevant vibration ~3×.  |
| `seat-tube`    | phone clamped to the seat tube near the hip. Literature-recommended for ISO work.                   |
| `backrest`     | phone on the backrest. Records mostly torso vibration.                                              |
| `footrest`     | phone on the footrest. Captures lower-limb input.                                                   |
| `unknown`      | use if you didn't note where the phone was.                                                         |

The output JSON includes the mount string so downstream reports can
flag caster-fork data as "not ISO-relative".

## What the profile contains

```json
{
  "chair_id":        "foldable-2026",
  "mount_location":  "caster-fork",
  "source_file":     "ART-202605181000.csv",
  "source_generation": "v1",
  "captured_utc":    "2026-05-18T08:30:00+00:00",
  "stationary": {
    "duration_s": 30.4,
    "accel_magnitude_mean": 9.812,
    "accel_magnitude_std":  0.018,
    "noise_floor_rms":       0.018
  },
  "corridor": {
    "duration_s": 58.3,
    "sample_count": 7012,
    "raw_rms_y_minus_g": 0.84,
    "raw_rms_magnitude": 0.93
  },
  "drops": {
    "count": 3,
    "peak_accel_mean": 18.4,
    "peak_accel_std":   1.1,
    "ringdown_freq_hz_mean": 11.3
  }
}
```

`noise_floor_rms` is what the sensor reports when nothing is happening
— anything in the actual ride at or below this number is just sensor
noise.

`corridor.raw_rms_y_minus_g` is this chair's vibration **floor** on a
known-smooth surface. Bad-spot reports later subtract this number
before ranking, so that "rough" means "rougher than your smoothest
indoor corridor", not "rougher than zero".

`drops.peak_accel_mean` is the impulse response to a known input.
When you move from the foldable to the rigid, repeat the calibration,
and the ratio of these two numbers becomes the cross-chair conversion
factor: a hit on the rigid chair worth `X` corresponds to a hit on
the foldable worth `X × (foldable_peak / rigid_peak)`.

## Using the profile in the locator

```bash
python3 waytrace_locate.py ~/Downloads/ART-XXX.csv \
        ~/Downloads/GPS-XXX.gpx \
        --chair foldable-2026
```

The locator silently falls back to uncalibrated analysis if the named
chair has no profile yet. The report header always names the chair
profile actually used.

## When to re-calibrate

- New wheelchair (obviously).
- New phone mount position on the same chair.
- New tyre / new tyre pressure (the literature is clear that tyres
  dominate).
- After any service / change that could affect frame stiffness.

You do **not** need to re-calibrate for: new phone OS update,
different time of day, weather changes, or new ride locations.

## Why this works

See `LITERATURE-wheelchair-vibration.md` for the references. The
short version: the literature is unanimous that **relative rankings
on the same chair are preserved across rides**, while **absolute
numbers are confounded by frame / tyres / mount**. Calibration turns
the relative ranking into a relative-with-known-anchors comparison,
which is enough for advocacy work like "this street is roughly twice
as bad as that street, normalised to your wheelchair".
