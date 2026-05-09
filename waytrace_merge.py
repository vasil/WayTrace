#!/usr/bin/env python3
"""
WayTrace Merge — combine multiple ART CSV files into one continuous session.

Use this when one push/ride was recorded as multiple ART-*.csv files
(because the app was paused, stopped and restarted, or split mid-ride).

The script re-bases timestamps so the merged output has no idle gap
between sessions — only a 1-second placeholder seam marked with a
'merge_seam_N' event row, so the analysis treats it as one activity.

Usage:
    python waytrace_merge.py <csv1> <csv2> [<csv3> ...]

Output:
    ART-MERGED-YYYYMMDDHHMM.csv  (in the same folder as the first input)

The merged file follows the standard ART-*.csv format and is accepted
by waytrace_analysis.py with no changes.
"""

import sys
import pandas as pd
from pathlib import Path
from datetime import datetime

SEAM_GAP_MS = 1000   # 1 second placeholder between merged sessions


def main():
    if len(sys.argv) < 3:
        print("Usage: python waytrace_merge.py <csv1> <csv2> [<csv3> ...]")
        sys.exit(1)

    paths = [Path(p) for p in sys.argv[1:]]
    for p in paths:
        if not p.exists():
            print(f"File not found: {p}")
            sys.exit(1)

    # Load each file and sort by its start timestamp
    sessions = []
    for p in paths:
        df = pd.read_csv(p)
        df.columns = df.columns.str.strip()
        sessions.append((df['timestamp_ms'].iloc[0], df, p.name))
    sessions.sort(key=lambda s: s[0])

    print(f"Merging {len(sessions)} session(s):")
    for start_ts, df, name in sessions:
        dur_s = (df['timestamp_ms'].iloc[-1] - df['timestamp_ms'].iloc[0]) / 1000.0
        print(f"  {name:<30} {len(df):>6} rows   {dur_s:7.1f}s")

    # Re-base timestamps so they are continuous (no idle gap)
    parts = []
    last_end = None
    for i, (start_ts, df, name) in enumerate(sessions):
        df = df.copy()
        if last_end is None:
            offset = df['timestamp_ms'].iloc[0]                   # zero-base first session
        else:
            offset = df['timestamp_ms'].iloc[0] - (last_end + SEAM_GAP_MS)
        df['timestamp_ms'] = df['timestamp_ms'] - offset
        last_end = int(df['timestamp_ms'].iloc[-1])

        # Insert a seam marker between sessions (not before the first one)
        if i > 0:
            seam_ts = int(df['timestamp_ms'].iloc[0]) - 1
            seam = pd.DataFrame([{
                'timestamp_ms': seam_ts,
                'sensor': 'pinpoint',
                'x': 0.0, 'y': 0.0, 'z': 0.0,
                'event': f'merge_seam_{i}',
            }])
            parts.append(seam)
        parts.append(df)

    merged = pd.concat(parts, ignore_index=True)

    out_dir  = paths[0].parent
    ts       = datetime.now().strftime("%Y%m%d%H%M")
    out_path = out_dir / f"ART-MERGED-{ts}.csv"
    merged.to_csv(out_path, index=False)

    total_dur = (merged['timestamp_ms'].iloc[-1] - merged['timestamp_ms'].iloc[0]) / 1000.0
    print(f"\nMerged: {len(merged)} rows   {total_dur:.1f}s total")
    print(f"Output: {out_path}")
    print(f"\nNext step:")
    print(f"  python waytrace_analysis.py {out_path}")


if __name__ == '__main__':
    main()
