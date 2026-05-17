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
    fs.program_select(0, sfid, 0, PROG_MELODY)  # channel 0 = melody (flute)
    fs.program_select(1, sfid, 0, PROG_BASS)    # channel 1 = bass
    fs.program_select(9, sfid, 128, 0)          # channel 9 = drum kit (bank 128)
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
        print(f"[ok] listening on UDP 0.0.0.0:{self.port}", file=sys.stderr)
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

    while True:
        time.sleep(tick_dt)
        with rx.lock:
            accel = list(rx.streams['accel'])
            gyro  = list(rx.streams['gyro'])
            pins  = list(rx.pinpoints)
            rx.pinpoints.clear()
        if not accel:
            continue
        # Take the most recent ~1 second of accel
        latest_t = accel[-1][0]
        window_start = latest_t - 1.0
        acc_win = [(t,x,y,z) for (t,x,y,z) in accel if t >= window_start]
        if not acc_win:
            continue
        # Features
        xs = np.array([a[1] for a in acc_win])
        ys = np.array([a[2] for a in acc_win])
        zs = np.array([a[3] for a in acc_win])
        fwd = float(np.mean(xs))
        lat = float(np.mean(zs))
        mag = np.sqrt(xs**2 + ys**2 + zs**2)
        vib = float(np.sqrt(np.mean((np.abs(mag - GRAVITY))**2)))
        gyro_win = [g for g in gyro if g[0] >= window_start]
        yaw = float(np.mean([g[2] for g in gyro_win])) if gyro_win else 0.0
        yaw_accumulator += yaw * tick_dt

        # ── Event detection (mirrors detect_events_offline) ──────────────
        for (t,x,y,z) in acc_win:
            m = (x*x + y*y + z*z) ** 0.5
            if m > HEAVY_BUMP_MAG and (t - last_event_t['heavy_bump']) > EVENT_COOLDOWN_S:
                synth.noteon(9, DRUM_HEAVY_BUMP, 127)
                last_event_t['heavy_bump'] = t
            elif m > BUMP_MAG and (t - last_event_t['bump']) > EVENT_COOLDOWN_S:
                synth.noteon(9, DRUM_BUMP, 100)
                last_event_t['bump'] = t
        for (t,x,y,z) in gyro_win:
            if abs(z) > ANGULAR_RATE and (t - last_event_t['wheelie']) > EVENT_COOLDOWN_S:
                synth.noteon(9, DRUM_WHEELIE, 110)
                last_event_t['wheelie'] = t
            if abs(x) > ANGULAR_RATE and (t - last_event_t['tilt']) > EVENT_COOLDOWN_S:
                synth.noteon(9, DRUM_TILT, 95)
                last_event_t['tilt'] = t
        for _ in pins:
            synth.noteon(9, DRUM_PINPOINT, 120)

        # ── Melody ────────────────────────────────────────────────────────
        speed_proxy = abs(fwd)
        rate = scale(speed_proxy, 0.0, 5.0, NOTE_RATE_SLOW, NOTE_RATE_FAST)
        pitch_base = scale(fwd, -3.0, 3.0, PITCH_LOW + 12, PITCH_HIGH - 6)
        velocity = int(clip(round(scale(vib, 0.05, 3.0, VEL_QUIET, VEL_LOUD)), 1, 127))
        pan = int(clip(round(scale(lat, -2.0, 2.0, PAN_LEFT, PAN_RIGHT)), 0, 127))
        synth.cc(0, 10, pan)
        scale_notes = SCALE_MINOR if yaw_accumulator < 0 else SCALE_MAJOR
        root = (36 + int(round(yaw_accumulator * 0.4))) % 12
        note = int(clip(quantize_to_scale(pitch_base, root, scale_notes),
                        PITCH_LOW, PITCH_HIGH))
        synth.noteon(0, note, velocity)
        # bass every other tick
        if int(latest_t / tick_dt) % 8 == 0:
            bass_note = int(clip(36 + root, 24, 60))
            synth.noteon(1, bass_note, 80)

        # ── periodic log line ────────────────────────────────────────────
        if time.time() - last_print_ts > 1.0:
            since_pkt = time.time() - rx.last_packet_at
            print(f"  pkts={rx.packets_received:>5}  "
                  f"vib={vib:5.2f}  fwd={fwd:+5.2f}  lat={lat:+5.2f}  "
                  f"last_pkt={since_pkt:.1f}s ago", file=sys.stderr)
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
