#!/usr/bin/env python3
"""
WayTrace Sonify — turn a push CSV into music.

Part of Open Streets Initiative (OSI-018). Reads any v1/v2/v3 ART file
and emits a real-time-length General MIDI file mapping sensor signals
to musical parameters:

    forward accel (X)   ->  tempo  +  base melody pitch
    lateral accel (Z)   ->  stereo pan
    |mag| - g           ->  note velocity (loudness)
    yaw rate (Y_gyro)   ->  key shift (left = minor, right = major)
    bump                ->  kick drum (35)
    heavy_bump          ->  crash cymbal (49)
    wheelie             ->  high tom (50)
    tilt                ->  wood block (76)
    pinpoint            ->  triangle (81)

Usage:
    python3 waytrace_sonify.py ART-YYYYMMDDHHMM.csv
Output:
    MUS-YYYYMMDDHHMM.mid    (next to the input CSV)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from mido import Message, MidiFile, MidiTrack, MetaMessage, bpm2tempo

from waytrace_analysis import (
    GRAVITY, SAMPLE_RATE,
    load_csv, split_sensors, detect_events_offline,
)

# ── Musical constants ─────────────────────────────────────────────────────────

MIDI_TEMPO_BPM      = 120              # constant clock so real-time = wall-clock
TICKS_PER_BEAT      = 480
TICKS_PER_SECOND    = TICKS_PER_BEAT * (MIDI_TEMPO_BPM / 60)   # 960
SECONDS_PER_TICK    = 1.0 / TICKS_PER_SECOND                    # 1.04 ms

# Note rate (notes per second of wall clock) is what actually changes with
# "musical tempo" derived from forward acceleration. A fixed MIDI clock keeps
# the file's wall-clock length equal to the recording's wall-clock length.
NOTE_RATE_SLOW      = 1.0              # 1 melody note/sec at rest
NOTE_RATE_FAST      = 4.0              # 4 melody notes/sec at full push

# Melody pitch range, MIDI note numbers
PITCH_LOW           = 48               # C3
PITCH_HIGH          = 84               # C6

# Velocity (loudness) range from vibration RMS
VEL_QUIET           = 30
VEL_LOUD            = 120

# Pan range
PAN_LEFT            = 0
PAN_RIGHT           = 127

# Instruments (General MIDI program numbers)
PROG_MELODY         = 73               # Flute      (warm, sustains nicely)
PROG_BASS           = 33               # Acoustic Bass
PROG_PAD            = 89               # Pad 2 (warm)  — for slow stretches

# Drum kit notes (MIDI channel 10 / 0-indexed channel 9)
DRUM_BUMP           = 36               # Bass Drum 1
DRUM_HEAVY_BUMP     = 49               # Crash Cymbal 1
DRUM_WHEELIE        = 50               # High Tom
DRUM_TILT           = 76               # High Wood Block
DRUM_PINPOINT       = 81               # Open Triangle

# C major / C minor scales (MIDI notes within an octave above the tonic)
SCALE_MAJOR         = [0, 2, 4, 5, 7, 9, 11]
SCALE_MINOR         = [0, 2, 3, 5, 7, 8, 10]


# ── Helpers ───────────────────────────────────────────────────────────────────

def clip(value, lo, hi):
    return max(lo, min(hi, value))


def scale(value, in_lo, in_hi, out_lo, out_hi):
    """Linear map value from [in_lo,in_hi] to [out_lo,out_hi], clipped."""
    if in_hi == in_lo:
        return (out_lo + out_hi) / 2
    t = (value - in_lo) / (in_hi - in_lo)
    t = clip(t, 0.0, 1.0)
    return out_lo + t * (out_hi - out_lo)


def quantize_to_scale(midi_pitch: float, root: int, scale: list[int]) -> int:
    """Snap an arbitrary MIDI pitch to the nearest note in (root + scale)."""
    p = int(round(midi_pitch))
    octave_base = (p // 12) * 12
    candidates = [octave_base + s + (root % 12) for s in scale]
    candidates += [c + 12 for c in candidates] + [c - 12 for c in candidates]
    return min(candidates, key=lambda c: abs(c - p))


def seconds_to_ticks(t: float) -> int:
    return int(round(t * TICKS_PER_SECOND))


# ── Feature extraction ────────────────────────────────────────────────────────

def per_second_features(accel: pd.DataFrame, gyro: pd.DataFrame):
    """Aggregate sensor values into one row per second of wall clock."""
    t = accel['t_s'].values
    total = float(t[-1])
    seconds = np.arange(0, int(np.ceil(total)) + 1)

    feats = []
    for s in seconds:
        a_mask = (t >= s) & (t < s + 1.0)
        if not a_mask.any():
            feats.append(None)
            continue
        fwd  = float(np.mean(accel['x'].values[a_mask]))            # forward
        lat  = float(np.mean(accel['z'].values[a_mask]))            # lateral
        vib_chunk = np.abs(np.sqrt(
            accel['x'].values[a_mask]**2 +
            accel['y'].values[a_mask]**2 +
            accel['z'].values[a_mask]**2
        ) - GRAVITY)
        vib  = float(np.sqrt(np.mean(vib_chunk**2)))                # RMS vibration
        if not gyro.empty:
            g_mask = (gyro['t_s'].values >= s) & (gyro['t_s'].values < s + 1.0)
            yaw = float(np.mean(gyro['y'].values[g_mask])) if g_mask.any() else 0.0
        else:
            yaw = 0.0
        feats.append((s, fwd, lat, vib, yaw))
    return feats


# ── Main sonify ───────────────────────────────────────────────────────────────

def sonify(csv_path: Path, out_path: Path):
    print(f"Loading {csv_path.name} ...")
    df = load_csv(csv_path)
    accel, gyro = split_sensors(df)
    if len(accel) < 60:
        sys.exit("Not enough accel data to sonify (need at least 1 second).")

    duration = float(accel['t_s'].iloc[-1])
    print(f"  duration: {duration:.1f}s ({duration/60:.2f} min)")
    print(f"  accel rows: {len(accel)}")

    print("Detecting events ...")
    events = detect_events_offline(accel, gyro)
    counts = events['kind'].value_counts().to_dict() if not events.empty else {}
    print(f"  events: {counts}")

    # Pinpoint events also become percussion
    pinpoint = df[df['sensor'] == 'pinpoint'].copy()
    if not pinpoint.empty:
        pinpoint['t_s'] = (pinpoint['timestamp_ms'].astype(float)
                           - accel['timestamp_ms'].iloc[0]) / 1000.0
        print(f"  pinpoints: {len(pinpoint)}")

    print("Computing per-second features ...")
    feats = per_second_features(accel, gyro)

    # ── Build the MIDI file ──────────────────────────────────────────────────
    mid = MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    melody_tr = MidiTrack(); mid.tracks.append(melody_tr)
    bass_tr   = MidiTrack(); mid.tracks.append(bass_tr)
    drum_tr   = MidiTrack(); mid.tracks.append(drum_tr)

    melody_tr.append(MetaMessage('set_tempo', tempo=bpm2tempo(MIDI_TEMPO_BPM), time=0))
    melody_tr.append(MetaMessage('track_name', name='Melody (forward/vibration)', time=0))
    melody_tr.append(Message('program_change', program=PROG_MELODY, channel=0, time=0))

    bass_tr.append(MetaMessage('track_name', name='Bass (yaw/key)', time=0))
    bass_tr.append(Message('program_change', program=PROG_BASS, channel=1, time=0))

    drum_tr.append(MetaMessage('track_name', name='Drums (events)', time=0))
    # Channel 9 = drum channel in mido (= MIDI channel 10)

    # ── Schedule events as (abs_tick, message) tuples on each track ──────────
    melody_events: list[tuple[int, Message]] = []
    bass_events:   list[tuple[int, Message]] = []
    drum_events:   list[tuple[int, Message]] = []

    # Bass: one root note every 2 seconds, key shifts with yaw
    accumulated_yaw = 0.0
    bass_root = 36   # C2
    bass_period_s = 2.0

    for f in feats:
        if f is None:
            continue
        s, fwd, lat, vib, yaw = f
        # Tempo / note-rate from forward accel magnitude
        speed_proxy = abs(fwd)
        rate = scale(speed_proxy, 0.0, 5.0, NOTE_RATE_SLOW, NOTE_RATE_FAST)
        notes_this_second = max(1, int(round(rate)))
        # Pitch from forward acceleration: positive (forward) = higher,
        # negative (braking) = lower. Quantized to current scale below.
        pitch_base = scale(fwd, -3.0, 3.0, PITCH_LOW + 12, PITCH_HIGH - 6)
        # Velocity from vibration RMS
        velocity = int(round(scale(vib, 0.05, 3.0, VEL_QUIET, VEL_LOUD)))
        velocity = clip(velocity, 1, 127)
        # Pan from lateral accel
        pan = int(round(scale(lat, -2.0, 2.0, PAN_LEFT, PAN_RIGHT)))
        # Key: yaw rate integrated drifts major/minor
        accumulated_yaw += yaw
        scale_notes = SCALE_MINOR if accumulated_yaw < 0 else SCALE_MAJOR
        root = (bass_root + int(round(accumulated_yaw * 0.4))) % 12

        # Pan CC on melody channel (channel 0)
        tick = seconds_to_ticks(s)
        melody_events.append((tick, Message('control_change',
                                            channel=0, control=10, value=pan, time=0)))

        # Multiple melody notes spread across this second
        for i in range(notes_this_second):
            note_t = s + (i / notes_this_second)
            note_pitch = quantize_to_scale(pitch_base + (i * 3), root, scale_notes)
            note_pitch = clip(note_pitch, PITCH_LOW, PITCH_HIGH)
            note_dur_s = 0.9 / notes_this_second
            on_tick = seconds_to_ticks(note_t)
            off_tick = seconds_to_ticks(note_t + note_dur_s)
            melody_events.append((on_tick, Message('note_on',
                                                   channel=0, note=int(note_pitch),
                                                   velocity=velocity, time=0)))
            melody_events.append((off_tick, Message('note_off',
                                                    channel=0, note=int(note_pitch),
                                                    velocity=0, time=0)))

        # Bass note every bass_period_s seconds
        if s % bass_period_s == 0:
            bass_pitch = clip(36 + root, 24, 60)
            on_tick  = seconds_to_ticks(s)
            off_tick = seconds_to_ticks(s + bass_period_s * 0.95)
            bass_vel = int(scale(vib, 0.0, 3.0, 50, 100))
            bass_events.append((on_tick, Message('note_on',
                                                 channel=1, note=int(bass_pitch),
                                                 velocity=bass_vel, time=0)))
            bass_events.append((off_tick, Message('note_off',
                                                  channel=1, note=int(bass_pitch),
                                                  velocity=0, time=0)))

    # Drum hits from events
    DRUM_MAP = {
        'bump':       (DRUM_BUMP,        100),
        'heavy_bump': (DRUM_HEAVY_BUMP,  127),
        'wheelie':    (DRUM_WHEELIE,     110),
        'tilt':       (DRUM_TILT,         95),
    }
    for _, row in events.iterrows():
        note, vel = DRUM_MAP[row['kind']]
        on_tick  = seconds_to_ticks(float(row['t_s']))
        off_tick = on_tick + int(TICKS_PER_SECOND * 0.2)
        drum_events.append((on_tick, Message('note_on',
                                             channel=9, note=note,
                                             velocity=vel, time=0)))
        drum_events.append((off_tick, Message('note_off',
                                              channel=9, note=note,
                                              velocity=0, time=0)))
    if not pinpoint.empty:
        for t in pinpoint['t_s'].values:
            on_tick  = seconds_to_ticks(float(t))
            off_tick = on_tick + int(TICKS_PER_SECOND * 0.4)
            drum_events.append((on_tick, Message('note_on',
                                                 channel=9, note=DRUM_PINPOINT,
                                                 velocity=120, time=0)))
            drum_events.append((off_tick, Message('note_off',
                                                  channel=9, note=DRUM_PINPOINT,
                                                  velocity=0, time=0)))

    # ── Sort each track's events by absolute tick + convert to deltas ────────
    def commit_events(track: MidiTrack, evs: list[tuple[int, Message]]):
        evs.sort(key=lambda x: x[0])
        last_tick = 0
        for abs_tick, msg in evs:
            delta = max(0, abs_tick - last_tick)
            msg.time = delta
            track.append(msg)
            last_tick = abs_tick

    commit_events(melody_tr, melody_events)
    commit_events(bass_tr,   bass_events)
    commit_events(drum_tr,   drum_events)

    mid.save(out_path)
    print(f"\nSaved: {out_path}")
    print(f"  tracks: melody, bass, drums   length: ~{duration:.1f}s")
    print(f"  open with any DAW / VLC / TiMidity / fluidsynth")


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: waytrace_sonify.py <ART-*.csv>")
    csv = Path(sys.argv[1])
    if not csv.exists():
        sys.exit(f"File not found: {csv}")
    stem = csv.stem.replace('ART-', 'MUS-')
    out  = csv.parent / f"{stem}.mid"
    sonify(csv, out)


if __name__ == '__main__':
    main()
