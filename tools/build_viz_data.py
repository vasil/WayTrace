#!/usr/bin/env python3
"""
build_viz_data.py — preprocess Strava JSONs into a minimal blob for the
Lumen Atlas visualization.

Filters to outdoor wheelchair pushes per the OSI distance rule:
  - sport_type == "Wheelchair"
  - trainer == False
  - "indoor" not in name (case-insensitive)

For each activity, keeps only:
  - id
  - n (short name)
  - d (start_date_local, ISO8601)
  - m (distance, metres, int)
  - p (Google-encoded summary_polyline)

Writes results sorted by date ascending so the viz can illuminate paths
in chronological order during its wake-up sequence.

Embeds the resulting JSON directly into the visualization HTML so the
file is genuinely self-contained — no fetch() needed at runtime.
"""
import json
import re
from pathlib import Path

ACTIVITIES_DIR = Path.home() / "Projects/open-streets-initiative/data/strava/activities"
VIZ_HTML       = Path.home() / "Projects/WayTrace/tools/osi_visualization.html"
TEMPLATE_HTML  = Path.home() / "Projects/WayTrace/tools/osi_visualization.template.html"

PLACEHOLDER = "/*__OSI_DATA_GOES_HERE__*/"


def is_outdoor_push(a):
    return (
        a.get("sport_type") == "Wheelchair"
        and not a.get("trainer", False)
        and "indoor" not in (a.get("name") or "").lower()
    )


def main():
    files = sorted(ACTIVITIES_DIR.glob("*.json"))
    print(f"scanning {len(files)} activities …")

    pushes = []
    skipped_no_polyline = 0
    for fp in files:
        try:
            a = json.loads(fp.read_text())
        except Exception:
            continue
        if not is_outdoor_push(a):
            continue
        poly = (a.get("map") or {}).get("summary_polyline") or ""
        if not poly:
            skipped_no_polyline += 1
            continue
        sll = a.get("start_latlng") or [0, 0]
        if not (sll and len(sll) == 2 and sll[0]):
            # Fall back to first polyline point — we still want this push
            sll = [0, 0]
        pushes.append({
            "id": a.get("id"),
            "n":  a.get("name") or "",
            "d":  a.get("start_date_local") or "",
            "m":  int(a.get("distance") or 0),
            "p":  poly,
            "s":  [round(sll[0], 4), round(sll[1], 4)],
            "c":  a.get("location_city") or "",
        })

    # Chronological order — earliest first
    pushes.sort(key=lambda x: x["d"])

    total_km = sum(x["m"] for x in pushes) / 1000
    print(f"  outdoor wheelchair pushes with polyline: {len(pushes)}")
    print(f"  total: {total_km:.2f} km")
    print(f"  earliest: {pushes[0]['d'][:10]}  →  latest: {pushes[-1]['d'][:10]}")
    print(f"  skipped (no polyline): {skipped_no_polyline}")

    # Compact JSON — no spaces
    blob = json.dumps(pushes, separators=(",", ":"), ensure_ascii=False)
    print(f"  blob size: {len(blob)/1024:.1f} KiB "
          f"({len(blob)/1024/1024:.2f} MiB)")

    # Inline into the template
    if not TEMPLATE_HTML.exists():
        raise SystemExit(f"missing template: {TEMPLATE_HTML}\n"
                         "run after the HTML template exists")
    html = TEMPLATE_HTML.read_text()
    if PLACEHOLDER not in html:
        raise SystemExit(f"placeholder {PLACEHOLDER!r} not found in template")
    html = html.replace(PLACEHOLDER, "OSI_DATA = " + blob)
    VIZ_HTML.write_text(html)
    print(f"wrote {VIZ_HTML} "
          f"({VIZ_HTML.stat().st_size/1024:.1f} KiB)")


if __name__ == "__main__":
    main()
