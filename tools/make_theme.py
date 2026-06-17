#!/usr/bin/env python3
"""Regenerate the WayTrace "Push Off" SYNC-clapper chime WAV.

Output: app/src/main/res/raw/waytrace_theme.wav (44.1 kHz, mono, 16-bit).
Notes : C5 E5 G5 A5 C6 — one octave up from the original sketch so the
        high frequencies cut through low-frequency street noise (traffic
        rumble, wind) and the Akaso V50 X mic captures them cleanly.
        Three short pickups and two held arrivals (~1.8 s).
This is the OSI-016 sync-clapper signature. See Drive doc
WAYTRACE-THEME-PUSH-OFF.md for the full spec.
"""
from pathlib import Path
import numpy as np
import wave

FS = 44_100

def note(f: float, d: float, gap: float = 0.04) -> np.ndarray:
    """Single sine + harmonic-partials note with attack/release envelope."""
    n = int(FS * d)
    x = np.linspace(0, d, n, endpoint=False)
    env = np.minimum(1, np.minimum(x / 0.02, (d - x) / 0.15))
    audio = (np.sin(2 * np.pi * f * x)
             + 0.35 * np.sin(2 * np.pi * 2 * f * x)
             + 0.15 * np.sin(2 * np.pi * 3 * f * x)) * env * 0.35 * 32767
    return np.concatenate([audio.astype(np.int16),
                           np.zeros(int(FS * gap), dtype=np.int16)])

def main() -> None:
    out = np.concatenate([
        note(523.25, 0.22),   # do  (C5)
        note(659.26, 0.22),   # mi  (E5)
        note(783.99, 0.22),   # sol (G5)
        note(880.00, 0.50),   # la  (A5)  — held
        note(1046.50, 0.50, gap=0),  # do' (C6) — resolve up
    ])
    path = Path(__file__).resolve().parents[1] / "app/src/main/res/raw/waytrace_theme.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(out.tobytes())
    print(f"wrote {path}  ({len(out)/FS:.2f} s, {len(out)*2} bytes)")

if __name__ == "__main__":
    main()
