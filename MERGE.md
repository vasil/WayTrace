# Merging multi-part WayTrace sessions

When a single ride was recorded as **multiple ART-*.csv files** (because
the app was force-stopped, the phone restarted, or you tapped STOP and
started a new recording in the middle of a ride), you can merge them
into one continuous activity for analysis.

There are two scripts that work together:

| Script | What it does | Output |
|---|---|---|
| `waytrace_merge.py`    | Combines 2+ ART CSVs into one continuous file | `ART-MERGED-YYYYMMDDHHMM.csv` |
| `waytrace_analysis.py` | Runs the 7-technique signal analysis        | `ANL-*.png` + `ANL-*.txt`    |

---

## Quick recipe — merge then analyze

```bash
cd ~/Projects/WayTrace

# Step 1 — merge the parts
python3 waytrace_merge.py \
    ~/Downloads/ART-202605091751.csv \
    ~/Downloads/ART-202605091852.csv

# Step 2 — analyze the merged file (path shown in the merge output)
python3 waytrace_analysis.py ~/Downloads/ART-MERGED-202605091949.csv
```

Or as a one-liner that pipes the second command into the first:

```bash
python3 waytrace_merge.py ~/Downloads/ART-202605091751.csv \
                          ~/Downloads/ART-202605091852.csv \
  | tee /dev/tty | grep -oP 'ART-MERGED-[0-9]+\.csv' | head -1 \
  | xargs -I{} python3 waytrace_analysis.py ~/Downloads/{}
```

---

## How `waytrace_merge.py` works

- **Sorts** the input files by their starting timestamp, so order on the
  command line does not matter.
- **Re-bases timestamps** so the merged file has no idle gap between
  sessions. The 31-minute coffee break between two recordings is removed
  — only active riding time is kept.
- **Inserts a 1-second seam** between sessions, marked with a
  `merge_seam_N` event row, so you can still see in the spectrogram
  where one recording ended and the next began.
- **Output goes to the same folder as the first input file**, named
  `ART-MERGED-YYYYMMDDHHMM.csv` (timestamp = when the merge ran).

The merged CSV uses the exact same 6-column format as a normal ART file
(`timestamp_ms,sensor,x,y,z,event`), so any tool that reads ART files
reads it without changes.

---

## How to call it yourself

### Two files

```bash
python3 waytrace_merge.py FILE_1.csv FILE_2.csv
```

### Three or more files

```bash
python3 waytrace_merge.py PART_1.csv PART_2.csv PART_3.csv
```

### All ART files in a folder (shell glob)

```bash
python3 waytrace_merge.py ~/Downloads/ART-202605*.csv
```

The `*` lets the shell expand to every matching file. They get sorted
by timestamp inside the script, so the wildcard order does not matter.

### Output

The merge script prints something like this:

```
Merging 2 session(s):
  ART-202605091751.csv            27097 rows    1740.9s
  ART-202605091852.csv            18271 rows    2322.3s

Merged: 45369 rows   4064.2s total
Output: /home/vasil/Downloads/ART-MERGED-202605091949.csv
```

Copy the `Output:` path and pass it to `waytrace_analysis.py`.

---

## How to call `waytrace_analysis.py`

```bash
python3 waytrace_analysis.py PATH_TO_CSV.csv
```

It produces, in the same folder as the input CSV:

- `ANL-<name>.png` — 5-panel chart (FFT, RMS, STFT, Jerk, stats table)
- `ANL-<name>.txt` — one-line log entry + band power + statistics

It also prints a console summary with:

- **RMS** (m/s²) — overall vibration intensity, ISO 2631-1
- **VDV** (m/s^1.75) — cumulative shock dose, ISO 2631-1
- **IRI** (m/km) — road roughness estimate
- **Bumps / heavy bumps / jerk obstacles** — discrete impact counts
- **Dominant frequency band** — push rhythm / surface texture / sharp impacts

---

## What the merged metrics mean

The merged file's metrics describe the **whole ride as one activity**.
The 31-minute pause between recordings is excluded, so RMS, VDV, and IRI
reflect actual riding conditions, not a watered-down average that
includes idle time.

Bump counts are cumulative across all merged parts.
