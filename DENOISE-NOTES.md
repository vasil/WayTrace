# Denoise notes — per-file analysis log

This file accumulates audio-spectrum analyses of helmet-camera MOV/MP4
recordings so we have a record of what frequencies dominate each file
and what cleanup mode worked. Useful when we revisit a file or want to
batch-process similar recordings.

---

## `20240101_205232.MOV`

- **Source path:** `/media/vasil/disk/VIDEO/20240101_205232.MOV`
- **Analysed:** 2026-05-14
- **Duration:** 837.0 s (~14 minutes)
- **Audio (source):** 48 kHz stereo AAC (analysis downmixed to 22 kHz mono)
- **Spectrum plot:** `~/Downloads/20240101_205232-spectrum.png`

### Band energy distribution

| band            | label                       | share of total |
|---              |---                          | ---:           |
| 20-80 Hz        | sub-bass / wind rumble      | **49.6 %**     |
| 80-250 Hz       | bass / frame vibration      | **31.4 %**     |
| 250-500 Hz      | low-mid / caster hum        | 9.4 %          |
| 500-2000 Hz     | mid / speech / songs        | 7.0 %          |
| 2000-5000 Hz    | high-mid / rim noise        | 0.2 %          |
| 5000-10000 Hz   | high / ambience             | 0.0 %          |

### Narrow peaks

None ≥ 5 dB above local baseline. The noise is **broadband, not tonal**
— no caster shimmy or motor whine to notch out. Aggressive low-cut is
the right tool here.

### Quiet window

The script identified the quietest 2-s window at **06:01-06:03**
(RMS 0.0203). Use that for `--profile-from` if you want a profile-based
pass on top of the preset.

### Recommended mode

`--target-caster` will cut 81 % of total energy (everything below 250 Hz),
all of which appears to be noise. The 7 % of energy in 500-2000 Hz —
where speech and music live — survives. Speech and song content should
become noticeably more audible.

If `--target-caster` still leaves audible rumble:
1. Add `--profile-from 6:01-6:03` for a second sweep.
2. Or step up the high-pass — manually pass `--notch` is the wrong
   tool here (no narrow peaks to notch).

### Comments

- Almost nothing above 2 kHz — this recording is heavily low-frequency.
  Either the mic was wind-shielded badly or the camera's audio is
  poor. Don't expect to recover crisp speech.
- The 7 % of useful mid-band energy is **all** we have to work with.
  Aggressive denoising risks taking that with the noise. Listen
  critically to the cleaned version and step back to default mode if
  music sounds gutted.

---
