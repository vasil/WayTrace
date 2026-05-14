# `waytrace_denoise.py` — Helmet-camera audio cleanup

The Akaso V50 X (and any action camera mounted on a moving wheelchair)
captures useful video but the audio is usually a mess: wind rumble,
caster-wheel rolling noise, frame vibration, and rim handling all
combine into a dense low-frequency wash that drowns out anything
useful. This script cleans that up.

**TL;DR**

```bash
# 1.  See what's in the audio first
python3 waytrace_denoise.py PUSH.mov --analyze

# 2.  Default clean-up (safe, no extra dependencies beyond ffmpeg)
python3 waytrace_denoise.py PUSH.mov

# 3.  Wheelchair-tuned preset
python3 waytrace_denoise.py PUSH.mov --target-caster

# 4.  Aggressive profile-based clean-up (needs sox)
python3 waytrace_denoise.py PUSH.mov --profile-from 0:05-0:08
```

The video stream is **copied bit-for-bit** in every mode — no
re-encoding, no quality loss on the picture. Only the audio is
replaced. By default the cleaned file lands next to the original with
`-clean` appended to the stem.

---

## What this script does NOT do

- It does not (yet) target the **specific** caster-shimmy frequency of
  your specific chair, because we have not yet captured a continuous
  in-range moving sample long enough for FFT to resolve that
  frequency (TODO 1 in the v2 SRS — pin-free calibration). The
  `--target-caster` preset uses literature-derived placeholders for
  caster/bearing rumble (200 and 400 Hz). Tune them with `--analyze`.
- It does not transcribe speech, normalise music, or "enhance" detail
  beyond what's in the original recording.
- It does not change the video. If you want stabilisation, that's a
  different tool.

---

## Prerequisites

Always required:

```bash
sudo apt install ffmpeg
```

Required only for `--profile-from`:

```bash
sudo apt install sox
```

Python: `numpy`, `scipy`, `matplotlib` — all already in the WayTrace
project requirements.

---

## Mode 1 — `--analyze` (always start here)

Before filtering anything, see what's actually in your audio.

```bash
python3 waytrace_denoise.py /path/to/PUSH.mov --analyze
```

Produces:

- **`PUSH-spectrum.png`** next to the input — log-scale PSD plot of the
  full audio with the eight strongest narrow peaks marked.
- **Console output:**
  ```
  Audio: 312.4 s @ 22050 Hz, mono
  Quietest 2-s window starts at 00:43 (RMS 0.0021)
    → consider: --profile-from 0:43-0:45

  Band energy:
     20-80   Hz  sub-bass / wind rumble       38.2% of total energy
     80-250  Hz  bass / frame vibration       27.4% of total energy
    250-500  Hz  low-mid / caster hum         12.1% of total energy
    500-2000 Hz  mid / speech                  9.8% of total energy
   2000-5000 Hz  high-mid / rim noise          7.5% of total energy
   5000-10000 Hz high / ambience               5.0% of total energy

  Narrow peaks (≥5 dB above local baseline, 60–5000 Hz):
    198.4 Hz   +7.2 dB
    412.9 Hz   +6.8 dB
    827.1 Hz   +5.1 dB
  ```

Interpret it like this:

| what you see in band energy        | likely cause                          |
|---                                  |---                                    |
| > 30 % in 20-80 Hz                 | wind rumble dominates                 |
| > 25 % in 80-250 Hz                | frame vibration / chassis rumble      |
| > 15 % in 250-500 Hz               | caster bearings / mechanical hum       |
| Narrow peaks listed                | mechanical resonance — feed to `--notch` |

The narrow-peaks list is the most actionable output: any peak with
≥5 dB rise above local baseline is a candidate for targeted filtering.

---

## Mode 2 — default clean-up (safe baseline)

```bash
python3 waytrace_denoise.py /path/to/PUSH.mov
```

Filter chain (ffmpeg only, no profile needed):

| filter                     | what it does                                  |
|---                          |---                                            |
| `highpass=f=80`            | removes wind rumble below 80 Hz               |
| `afftdn=nr=18:nf=-25`      | FFT-based spectral noise reduction            |
| `lowpass=f=11000`          | rolls off ultrasonic hiss above 11 kHz        |
| `loudnorm=I=-16:LRA=11`    | broadcast-standard loudness normalisation     |

Output: `PUSH-clean.mov` next to the original.

Use this when:
- You haven't run `--analyze` yet, or
- The audio is mostly fine and you just want it tidied up.

---

## Mode 3 — `--target-caster` preset

```bash
python3 waytrace_denoise.py /path/to/PUSH.mov --target-caster
```

Same family as Mode 2 but tuned for chair-on-pavement:

| filter                            | what it does                                  |
|---                                 |---                                            |
| `highpass=f=150`                  | aggressive low-cut, kills wobble-induced rumble |
| `bandreject=f=200, w=20`          | notch at 200 Hz — typical caster bearing tone  |
| `bandreject=f=400, w=20`          | second-harmonic notch                          |
| `lowpass=f=10000`                 | tighter top-end                                |
| `afftdn=nr=24:nf=-28`             | stronger spectral noise reduction              |
| `loudnorm=I=-16:LRA=11`           | loudness                                       |

Use this when:
- The chair was rolling for most of the recording, and
- `--analyze` showed strong narrow peaks in the 100-500 Hz band.

Combinable with `--notch`:

```bash
python3 waytrace_denoise.py PUSH.mov --target-caster --notch 820,1640
```

This adds two more notches (at 820 and 1640 Hz) on top of the 200/400
defaults.

---

## Mode 4 — `--notch F1,F2,...` for custom frequencies

```bash
python3 waytrace_denoise.py /path/to/PUSH.mov --notch 220,440,880
```

Same chain as the default clean-up but with extra notch filters at the
listed centre frequencies (Hz). Each notch is ~20 Hz wide.

Use this when:
- `--analyze` showed specific narrow peaks not covered by `--target-caster`
- You want to keep the gentler default high-pass / afftdn settings.

---

## Mode 5 — `--profile-from MM:SS-MM:SS` (cleanest, needs sox)

```bash
sudo apt install sox
python3 waytrace_denoise.py /path/to/PUSH.mov --profile-from 0:05-0:08
```

The time range you pick should be a stretch where **the chair was not
moving** — ideally before you start pushing, or during a stop. sox
samples that range as a noise profile (its frequency spectrum is taken
as "this is noise"), then subtracts the same spectrum from the entire
file.

This produces the cleanest result when the noise is roughly stationary
across the whole recording (i.e. the chair sounds the same when
parked as when moving — true for fan noise, electrical hum, etc., less
true for rolling noise). For rolling noise specifically, `--target-caster`
often does better.

Optional `--strength` controls how aggressively sox subtracts:

```bash
python3 waytrace_denoise.py PUSH.mov --profile-from 0:05-0:08 --strength 0.30
```

- `0.10` — very gentle, leaves room ambience
- `0.21` (default) — balanced
- `0.30` — strong, may sound "watery" if too aggressive
- `0.50+` — diagnostic only, will sound very artificial

`--analyze` suggests a quiet 2-second window automatically:

```
Quietest 2-s window starts at 00:43 (RMS 0.0021)
  → consider: --profile-from 0:43-0:45
```

---

## Recommended workflow for a new MOV

1. **Inspect first:**
   ```bash
   python3 waytrace_denoise.py PUSH.mov --analyze
   ```
   Look at `PUSH-spectrum.png`. Note the band energies and any narrow peaks.

2. **Pick a mode based on what you saw:**
   - Energy concentrated < 250 Hz, no narrow peaks → **default mode**.
   - Significant energy in 250-500 Hz, narrow peaks listed → **--target-caster**.
   - Specific peaks you want to kill → **--notch F1,F2** (or combine with --target-caster).
   - You have a long quiet stretch → **--profile-from MM:SS-MM:SS**.

3. **Process:**
   ```bash
   python3 waytrace_denoise.py PUSH.mov --target-caster
   ```

4. **Listen to `PUSH-clean.mov`.** If it sounds underdone, try
   `--profile-from`. If it sounds over-processed ("watery", "swirly"),
   step down to the default mode or reduce `--strength`.

---

## Future improvements

- Once we have a continuous in-range push recording (>= 2 min at
  0.8-1.5 m/s), we will run accelerometer FFT to identify the
  actual caster shimmy frequency of *this specific chair* and bake
  the right notches into `--target-caster` by default.
- A `--target-caster-rigid` preset will follow when the rigid chair
  arrives (different frame, different resonant frequencies).
- Speech-aware mode: preserve 250-3000 Hz when a person is speaking
  on camera, more aggressive elsewhere.

---

## Troubleshooting

**"Missing tool: sox"**
You're using `--profile-from`. Install sox: `sudo apt install sox`.
Or use a different mode that doesn't need it.

**Output sounds "watery" or "swirly"**
afftdn / sox-noisered are too aggressive. Try:
- Default mode instead of `--target-caster`
- Lower `--strength` (e.g. 0.10) for profile mode
- Drop the `--notch` list — you may be cutting useful content

**Output is silent or too quiet**
The `loudnorm` filter requires audio with measurable signal. If your
input was already near-silent, normalisation will amplify the noise
floor. Check the original first.

**Notch frequencies don't help**
Re-run `--analyze`. Narrow peaks below 5 dB above baseline are usually
not worth notching (you'll hear holes in the audio instead of an
improvement). Focus on peaks ≥ 7 dB.
