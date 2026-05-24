#!/usr/bin/env python3
"""
FFT plot for an ART CSV — first cut of OSI-006.

Plots:
  Top    — time-domain accel magnitude (whole push)
  Middle — FFT of accel magnitude, 0–40 Hz (wheelchair-relevant band)
  Bottom — FFT split per axis (X forward/back, Y vertical, Z lateral)

Usage:
    python fft_plot.py ~/Downloads/ART-202605242125.csv [output.png]
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        sys.exit(2)
    csv_path = Path(argv[1]).expanduser()
    out_path = Path(argv[2]) if len(argv) > 2 else csv_path.with_name(
        csv_path.stem.replace("ART-", "FFT-") + ".png")

    df = pd.read_csv(csv_path)
    a = df[df["sensor"] == "accel"].sort_values("timestamp_ms").reset_index(drop=True)
    if a.empty:
        sys.exit("no accel rows")

    t_ms = a["timestamp_ms"].to_numpy()
    t = (t_ms - t_ms[0]) / 1000.0  # seconds from start
    x = a["x"].to_numpy(dtype=float)
    y = a["y"].to_numpy(dtype=float)
    z = a["z"].to_numpy(dtype=float)

    # Effective sample rate (median delta)
    dt = np.median(np.diff(t_ms)) / 1000.0
    fs = 1.0 / dt
    n = len(a)
    span = t[-1] - t[0]

    # Magnitude minus gravity (subtract mean over whole push as a quick DC remove).
    mag = np.sqrt(x * x + y * y + z * z)
    mag_ac = mag - mag.mean()
    x_ac = x - x.mean()
    y_ac = y - y.mean()
    z_ac = z - z.mean()

    # FFT — single-sided amplitude spectrum
    def fft_amp(sig):
        N = len(sig)
        spec = np.fft.rfft(sig)
        freq = np.fft.rfftfreq(N, d=dt)
        amp = (2.0 / N) * np.abs(spec)
        return freq, amp

    f, mag_amp = fft_amp(mag_ac)
    _, x_amp   = fft_amp(x_ac)
    _, y_amp   = fft_amp(y_ac)
    _, z_amp   = fft_amp(z_ac)

    # Limit plot to wheelchair-relevant band 0–40 Hz
    fmax = 40.0
    sel = f <= fmax

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(11, 9),
                                        gridspec_kw={"height_ratios": [1, 1.3, 1.3]})

    # 1. Time-domain magnitude
    ax1.plot(t, mag, lw=0.4, color="black")
    ax1.set_title(f"Accel magnitude over time  —  {csv_path.name}\n"
                  f"fs={fs:.1f} Hz, N={n:,}, span={span:.1f} s")
    ax1.set_xlabel("time (s)")
    ax1.set_ylabel("|a|  (m/s²)")
    ax1.grid(True, alpha=0.3)

    # 2. FFT of magnitude
    ax2.semilogy(f[sel], mag_amp[sel], lw=0.8, color="C3")
    ax2.set_title("FFT — accel magnitude (gravity removed)")
    ax2.set_xlabel("frequency (Hz)")
    ax2.set_ylabel("amplitude  (m/s²)")
    ax2.set_xlim(0, fmax)
    ax2.grid(True, which="both", alpha=0.3)

    # 3. Per-axis FFT (SRS axes: X fwd/back, Y vertical, Z lateral)
    ax3.semilogy(f[sel], y_amp[sel], lw=0.8, color="C0", label="Y  vertical")
    ax3.semilogy(f[sel], x_amp[sel], lw=0.8, color="C2", label="X  forward/back")
    ax3.semilogy(f[sel], z_amp[sel], lw=0.8, color="C1", label="Z  lateral")
    ax3.set_title("FFT — per axis (SRS chair-mounted orientation)")
    ax3.set_xlabel("frequency (Hz)")
    ax3.set_ylabel("amplitude  (m/s²)")
    ax3.set_xlim(0, fmax)
    ax3.legend(loc="upper right")
    ax3.grid(True, which="both", alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    print(f"wrote {out_path}")

    # Headline numbers — print dominant peaks
    peak_band = (f > 0.5) & (f < fmax)
    for name, amp in [("magnitude", mag_amp), ("Y vertical", y_amp),
                      ("X fwd/back", x_amp), ("Z lateral", z_amp)]:
        idx_in_band = np.where(peak_band)[0]
        sub = amp[idx_in_band]
        top3 = idx_in_band[np.argsort(sub)[-3:][::-1]]
        peaks = ", ".join(f"{f[i]:5.2f} Hz ({amp[i]:.3f})" for i in top3)
        print(f"  {name:11s} top-3 peaks: {peaks}")


if __name__ == "__main__":
    main(sys.argv)
