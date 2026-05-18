#!/usr/bin/env python3
"""
WayTrace Sonify Live (OSI-019) — listen for live sensor packets from the
phone over UDP and play music in real time. Companion to waytrace_sonify.py
but consumes a live stream instead of a finished CSV.

Protocol (matches RecorderService.kt liveQueue):
    UDP packet, US-ASCII text:

        WTLIVE 1
        ts_ms,sensor,x,y,z,rotvec_w
        ts_ms,sensor,x,y,z,rotvec_w
        ...

Usage:
    python3 waytrace_sonify_live.py                   # 0.0.0.0:54321
    python3 waytrace_sonify_live.py --port 12345
    python3 waytrace_sonify_live.py --silent          # log only, no audio

Audio backend: pyfluidsynth (pip install pyfluidsynth). Requires the system
fluidsynth library (already installed for the offline render) and the
General MIDI soundfont at /usr/share/sounds/sf2/FluidR3_GM.sf2.
"""

import argparse
import collections
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

# Reuse mapping + thresholds from the offline sonifier and analysis module
from waytrace_analysis import (
    GRAVITY, BUMP_MAG, HEAVY_BUMP_MAG, ANGULAR_RATE, EVENT_COOLDOWN_S,
)
from waytrace_sonify import (
    PROG_MELODY, PROG_BASS,
    DRUM_BUMP, DRUM_HEAVY_BUMP, DRUM_WHEELIE, DRUM_TILT, DRUM_PINPOINT,
    PITCH_LOW, PITCH_HIGH, VEL_QUIET, VEL_LOUD,
    PAN_LEFT, PAN_RIGHT,
    NOTE_RATE_SLOW, NOTE_RATE_FAST,
    SCALE_MAJOR, SCALE_MINOR,
    scale, clip, quantize_to_scale,
)

# ── Audio backend ───────────────────────────────────────────────────────────

def local_ip() -> str:
    """Best-effort: find the IP of the interface that would reach the LAN.
    No packet is actually sent — UDP connect() just selects a route."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "?"
    finally:
        s.close()


class NullSynth:
    """Silent fallback when fluidsynth isn't available."""
    def program_select(self, *a, **k): pass
    def noteon(self, *a, **k): pass
    def noteoff(self, *a, **k): pass
    def cc(self, *a, **k): pass

def make_synth(silent: bool):
    if silent:
        return NullSynth()
    try:
        import fluidsynth
    except ImportError:
        print("[warn] pyfluidsynth not installed. Running silent.",
              "  pip install --user --break-system-packages pyfluidsynth",
              sep="\n", file=sys.stderr)
        return NullSynth()
    sf2_path = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
    if not Path(sf2_path).exists():
        print(f"[warn] soundfont not found at {sf2_path}. Running silent.",
              file=sys.stderr)
        return NullSynth()
    fs = fluidsynth.Synth()
    fs.start()                                  # platform-default audio driver
    sfid = fs.sfload(sf2_path)
    # Circus instrumentation:
    #   ch 0 = Calliope (GM 82) — the steam-organ sound everyone hears as "circus"
    #   ch 1 = Trombone (GM 57) — brassy oompah bass
    #   ch 9 = drum kit
    fs.program_select(0, sfid, 0, 82)
    fs.program_select(1, sfid, 0, 57)
    fs.program_select(9, sfid, 128, 0)
    print(f"[ok] fluidsynth started with FluidR3_GM.sf2", file=sys.stderr)
    return fs


# ── UDP receiver ────────────────────────────────────────────────────────────

class Receiver(threading.Thread):
    """Reads UDP packets and pushes parsed rows into a per-sensor deque.
    Each deque holds the last RING_SECONDS seconds of (t_s, x, y, z) tuples.
    Event-row detection is done by the consumer."""
    RING_SECONDS = 4.0

    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', port))
        self.sock.settimeout(0.5)
        self.streams = {
            'accel':    collections.deque(),
            'gyro':     collections.deque(),
            'gravity':  collections.deque(),
            'mag':      collections.deque(),
            'rotvec':   collections.deque(),
            'pressure': collections.deque(),
        }
        self.pinpoints = collections.deque()
        self.lock = threading.Lock()
        self.packets_received = 0
        self.first_ts_ms = None
        self.last_packet_at = 0.0
        self.running = True

    def run(self):
        ip = local_ip()
        print(f"[ok] listening on UDP 0.0.0.0:{self.port}", file=sys.stderr)
        print(f"[ok] set the phone's Live Sonify target to: {ip}:{self.port}",
              file=sys.stderr)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            self.packets_received += 1
            self.last_packet_at = time.time()
            text = data.decode('ascii', errors='ignore')
            lines = text.splitlines()
            if not lines or not lines[0].startswith("WTLIVE"):
                continue  # not our protocol
            for line in lines[1:]:
                parts = line.split(',')
                if len(parts) < 5:
                    continue
                try:
                    ts_ms = int(parts[0])
                    sensor = parts[1]
                    x = float(parts[2]) if parts[2] else 0.0
                    y = float(parts[3]) if parts[3] else 0.0
                    z = float(parts[4]) if parts[4] else 0.0
                except ValueError:
                    continue
                if self.first_ts_ms is None:
                    self.first_ts_ms = ts_ms
                t_s = (ts_ms - self.first_ts_ms) / 1000.0
                with self.lock:
                    if sensor == 'pinpoint':
                        self.pinpoints.append(t_s)
                    elif sensor in self.streams:
                        self.streams[sensor].append((t_s, x, y, z))
                        # Trim ring buffer
                        cutoff = t_s - self.RING_SECONDS
                        while self.streams[sensor] and self.streams[sensor][0][0] < cutoff:
                            self.streams[sensor].popleft()

    def stop(self):
        self.running = False
        try: self.sock.close()
        except Exception: pass


# ── Sonifier (live tick loop) ───────────────────────────────────────────────

def run_sonifier(rx: Receiver, synth, tick_hz: float = 4.0):
    """Every 1/tick_hz seconds, drain the last 1 second from the streams,
    derive musical features, and emit MIDI events on the synth."""
    tick_dt = 1.0 / tick_hz
    last_event_t = {'bump': -10.0, 'heavy_bump': -10.0,
                    'wheelie': -10.0, 'tilt': -10.0}
    yaw_accumulator = 0.0
    last_print_ts = 0.0
    # Track currently-held melody/bass notes so we can release them before
    # the next noteon — otherwise voices pile up and fluidsynth's
    # ringbuffer overflows after ~30 s.
    prev_melody_notes: list[int] = []
    prev_bass_note   = None
    # State for accel-vs-brake distinction (delta of intensity per tick)
    prev_intensity   = 0.0
    last_event_t.update({'accel': -10.0, 'brake': -10.0, 'crash': -10.0})
    # Reference gravity vector captured the first time we have a stable reading.
    # Tilt deviations from this neutral pose modulate pitch bend + vibrato.
    neutral_gravity: tuple | None = None

    while True:
        time.sleep(tick_dt)
        with rx.lock:
            accel   = list(rx.streams['accel'])
            gyro    = list(rx.streams['gyro'])
            gravity = list(rx.streams['gravity'])
            pins    = list(rx.pinpoints)
            rx.pinpoints.clear()
        if not accel:
            continue
        # Take the most recent ~1 second of accel
        latest_t = accel[-1][0]
        window_start = latest_t - 1.0
        acc_win = [(t,x,y,z) for (t,x,y,z) in accel if t >= window_start]
        if not acc_win:
            continue
        # Features — all orientation-independent.
        xs = np.array([a[1] for a in acc_win])
        ys = np.array([a[2] for a in acc_win])
        zs = np.array([a[3] for a in acc_win])
        # Subtract gravity vector (from the gravity sensor stream) to get the
        # true motion of the phone, independent of how it's mounted. Without
        # this, "forward acceleration" is buried under the constant 9.8 m/s²
        # of gravity sitting on whichever axis the phone happens to use.
        g_samples = [g for g in gravity if g[0] >= window_start]
        if g_samples:
            gx0 = float(np.mean([g[1] for g in g_samples]))
            gy0 = float(np.mean([g[2] for g in g_samples]))
            gz0 = float(np.mean([g[3] for g in g_samples]))
        else:
            gx0, gy0, gz0 = 0.0, 0.0, GRAVITY
        lin_mags = np.sqrt((xs - gx0) ** 2 + (ys - gy0) ** 2 + (zs - gz0) ** 2)
        intensity      = float(np.mean(lin_mags))   # smooth motion intensity
        intensity_peak = float(np.max(lin_mags))    # current spike
        # Keep vib (RMS of magnitude minus g) for backwards-compatible threshold logic
        mag = np.sqrt(xs**2 + ys**2 + zs**2)
        vib = float(np.sqrt(np.mean((np.abs(mag - GRAVITY))**2)))
        # ── Tilt angle vs. neutral pose ──────────────────────────────────
        # Capture the first stable gravity reading as "level/neutral". After
        # that, the angle between current and neutral gravity tells us how
        # much the chair is tipped back (wheelie) or banked (cornering).
        ng_mag = (gx0*gx0 + gy0*gy0 + gz0*gz0) ** 0.5
        if neutral_gravity is None and ng_mag > 5.0:
            neutral_gravity = (gx0, gy0, gz0, ng_mag)
            print(f"[ok] captured neutral gravity: "
                  f"({gx0:+.2f},{gy0:+.2f},{gz0:+.2f})", file=sys.stderr)
        tilt_rad = 0.0
        if neutral_gravity is not None and ng_mag > 0.1:
            nx, ny, nz, nm = neutral_gravity
            cos_t = (gx0*nx + gy0*ny + gz0*nz) / (ng_mag * nm)
            cos_t = max(-1.0, min(1.0, cos_t))
            tilt_rad = float(np.arccos(cos_t))
        # Pitch bend: 0 rad = no shift; π/2 rad = +2 semitones (max bend)
        bend_amount = int(clip(tilt_rad / (np.pi / 2) * 8191, 0, 8191))
        synth.pitch_bend(0, bend_amount)
        # Modulation wheel: same scale → vibrato amount, the "wobble"
        mod_amount = int(clip(tilt_rad / (np.pi / 2) * 127, 0, 127))
        synth.cc(0, 1, mod_amount)

        gyro_win = [g for g in gyro if g[0] >= window_start]
        # Signed yaw = projection of gyro vector onto gravity unit vector.
        # When spinning in place around the vertical axis, this is the true
        # clockwise/counter-clockwise rate, regardless of phone orientation.
        g_mag = (gx0*gx0 + gy0*gy0 + gz0*gz0) ** 0.5
        if g_mag > 0.1 and gyro_win:
            gux, guy, guz = gx0/g_mag, gy0/g_mag, gz0/g_mag
            yaw_signed = float(np.mean(
                [gx*gux + gy*guy + gz*guz for (_, gx, gy, gz) in gyro_win]
            ))
        else:
            yaw_signed = 0.0
        yaw_accumulator += yaw_signed * tick_dt   # heading in radians, signed

        # ── Event detection (mirrors detect_events_offline) ──────────────
        for (t,x,y,z) in acc_win:
            m = (x*x + y*y + z*z) ** 0.5
            if m > HEAVY_BUMP_MAG and (t - last_event_t['heavy_bump']) > EVENT_COOLDOWN_S:
                synth.noteon(9, DRUM_HEAVY_BUMP, 127)
                last_event_t['heavy_bump'] = t
            elif m > BUMP_MAG and (t - last_event_t['bump']) > EVENT_COOLDOWN_S:
                synth.noteon(9, DRUM_BUMP, 100)
                last_event_t['bump'] = t
        # Orientation-independent rotation detection. Threshold lowered so
        # gentle banking (caster wheels lift) is audible. Use cowbell + low
        # tom at max velocity so the rotation drums cut through the flute
        # melody instead of being drowned by the heavy-bump cymbal.
        ROT_LIVE = 1.0  # rad/s (~57°/s) — sensitive enough for banking
        DRUM_ROT_A = 56   # cowbell — distinctive, cuts through
        DRUM_ROT_B = 41   # low floor tom — heavy, also cutting
        gyro_max_mag = 0.0
        for (t,x,y,z) in gyro_win:
            m = (x*x + y*y + z*z) ** 0.5
            if m > gyro_max_mag:
                gyro_max_mag = m
            if m > ROT_LIVE:
                ax, ay, az = abs(x), abs(y), abs(z)
                if ax > ay and ax > az:
                    if (t - last_event_t['tilt']) > EVENT_COOLDOWN_S:
                        synth.noteon(9, DRUM_ROT_B, 127)
                        last_event_t['tilt'] = t
                else:
                    if (t - last_event_t['wheelie']) > EVENT_COOLDOWN_S:
                        synth.noteon(9, DRUM_ROT_A, 127)
                        last_event_t['wheelie'] = t
        for _ in pins:
            synth.noteon(9, DRUM_PINPOINT, 120)

        # ── Melody ────────────────────────────────────────────────────────
        # Pitch follows HEADING directly (sawtooth wrap): spinning right
        # → melody climbs the keyboard; spinning left → melody descends.
        # One full turn (2π rad) sweeps the entire flute range. The wrap
        # at the ends is audible but musical — like a calliope glissando.
        pitch_range = PITCH_HIGH - PITCH_LOW
        sweep_per_rad = pitch_range / (2.0 * np.pi)
        offset = (yaw_accumulator * sweep_per_rad) % pitch_range
        pitch_from_heading = PITCH_LOW + offset
        pitch_nudge        = scale(intensity, 0.0, 3.0, -3, 6)
        pitch_base = pitch_from_heading + pitch_nudge
        rate = scale(intensity, 0.0, 3.0, NOTE_RATE_SLOW, NOTE_RATE_FAST)
        velocity = int(clip(round(scale(intensity_peak, 0.1, 5.0,
                                        VEL_QUIET, VEL_LOUD)), 1, 127))
        # Pan follows heading too — quarter turn pans full L→R
        pan = int(clip(round(scale(np.sin(yaw_accumulator),
                                   -1.0, 1.0, PAN_LEFT, PAN_RIGHT)), 0, 127))
        synth.cc(0, 10, pan)
        # Direction of spin colours the harmony: right = major, left = minor
        scale_notes = SCALE_MAJOR if yaw_signed >= 0 else SCALE_MINOR
        root = (36 + int(round(yaw_accumulator * 0.4))) % 12

        # ── Accel vs brake vs crash punctuation ──────────────────────────
        # Δ-intensity = sped-up vs slowed-down (orientation-independent).
        # intensity_peak = the largest instantaneous spike in the window;
        # door crashes / hard impacts blow past everything else and earn a
        # crash cymbal regardless of direction.
        delta_i = intensity - prev_intensity
        if intensity_peak > 10.0 and (latest_t - last_event_t.get('crash', -10)) > EVENT_COOLDOWN_S:
            synth.noteon(9, 49, 127)   # crash cymbal — door slam / hard impact
            last_event_t['crash'] = latest_t
        elif delta_i > 0.4 and (latest_t - last_event_t['accel']) > EVENT_COOLDOWN_S:
            synth.noteon(9, 38, 127)   # acoustic snare — push forward!
            last_event_t['accel'] = latest_t
        elif delta_i < -0.4 and (latest_t - last_event_t['brake']) > EVENT_COOLDOWN_S:
            synth.noteon(9, 47, 127)   # low-mid tom — braking thud
            last_event_t['brake'] = latest_t
        prev_intensity = intensity
        note = int(clip(quantize_to_scale(pitch_base, root, scale_notes),
                        PITCH_LOW, PITCH_HIGH))
        # Release everything we held last tick
        for n in prev_melody_notes:
            synth.noteoff(0, n)
        prev_melody_notes = []
        # Spinning hard → triad stab. Right-spin = major (bright), left-spin
        # = minor (tense). Both give the circus-organ "stab" feel.
        if abs(yaw_signed) > 1.0:
            third = 4 if yaw_signed >= 0 else 3   # major 3rd vs minor 3rd
            chord = [note, note + third, note + 7]
        else:
            chord = [note]
        for n in chord:
            n = int(clip(n, 0, 127))
            synth.noteon(0, n, velocity)
            prev_melody_notes.append(n)
        # bass every other tick
        if int(latest_t / tick_dt) % 8 == 0:
            bass_note = int(clip(36 + root, 24, 60))
            if prev_bass_note is not None:
                synth.noteoff(1, prev_bass_note)
            synth.noteon(1, bass_note, 80)
            prev_bass_note = bass_note

        # ── periodic log line ────────────────────────────────────────────
        if time.time() - last_print_ts > 1.0:
            since_pkt = time.time() - rx.last_packet_at
            print(f"  pkts={rx.packets_received:>5}  "
                  f"i={intensity:4.2f}  pk={intensity_peak:5.2f}  "
                  f"yaw={yaw_signed:+5.2f}  hdg={yaw_accumulator:+6.2f}  "
                  f"tilt={np.degrees(tilt_rad):5.1f}°  "
                  f"last_pkt={since_pkt:.1f}s", file=sys.stderr)
            last_print_ts = time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=54321)
    ap.add_argument("--silent", action="store_true",
                    help="log packets but don't play audio (debug)")
    args = ap.parse_args()

    rx = Receiver(args.port)
    rx.start()
    synth = make_synth(args.silent)

    try:
        run_sonifier(rx, synth)
    except KeyboardInterrupt:
        print("\n[exit]", file=sys.stderr)
    finally:
        rx.stop()


if __name__ == "__main__":
    main()
