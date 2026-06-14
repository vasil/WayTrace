#!/usr/bin/env python3
"""
Interactive folium-based road-quality map.

Same input as road_quality_map_combined.py but renders an HTML file with
OpenStreetMap tiles underneath so you can zoom in and check whether
heavy-bump hits sit on curb cuts, tram tracks, or potholes.

Usage:
    road_quality_map_folium.py \\
        --pair GPS-A.gpx ART-A.csv \\
        --pair GPS-B.gpx ART-B1.csv ART-B2.csv \\
        --out RQM-skopje.html
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import folium
from folium.features import DivIcon

GRAVITY = 9.81
WINDOW_S = 4.0
DEDUP_GAP_S = 5.0
DEFAULT_TZ = 2


def parse_gpx(path):
    ns = "http://www.topografix.com/GPX/1/1"
    pts = []
    for tp in ET.parse(path).getroot().iter(f"{{{ns}}}trkpt"):
        lat = float(tp.attrib["lat"]); lon = float(tp.attrib["lon"])
        t = datetime.fromisoformat(
            tp.find(f"{{{ns}}}time").text.replace("Z", "+00:00"))
        pts.append((t.timestamp(), lat, lon))
    return pts


def parse_start(path, tz):
    m = re.search(r'(\d{12})', path.name)
    dt = datetime.strptime(m.group(1), '%Y%m%d%H%M')
    return dt.replace(tzinfo=timezone(timedelta(hours=tz))).timestamp()


def load_accel(path, tz):
    df = pd.read_csv(path, low_memory=False)
    a = df[df["sensor"] == "accel"].sort_values("timestamp_ms").reset_index(drop=True)
    if a.empty:
        return np.array([]), np.array([]), np.array([])
    t_ms = a["timestamp_ms"].to_numpy()
    start = parse_start(path, tz)
    utc = start + (t_ms - t_ms[0]) / 1000.0
    mag = np.sqrt(a["x"].to_numpy(float)**2 + a["y"].to_numpy(float)**2
                + a["z"].to_numpy(float)**2)
    return utc, mag, np.abs(mag - GRAVITY)


def push_data(arts, gpx, tz):
    chunks = [load_accel(p, tz) for p in arts]
    chunks = [c for c in chunks if len(c[0])]
    if not chunks:
        return None
    utc = np.concatenate([c[0] for c in chunks])
    mag = np.concatenate([c[1] for c in chunks])
    vib = np.concatenate([c[2] for c in chunks])
    o = np.argsort(utc); utc, mag, vib = utc[o], mag[o], vib[o]

    pts = parse_gpx(gpx)
    gpx_utc = np.array([p[0] for p in pts])
    lats = np.array([p[1] for p in pts])
    lons = np.array([p[2] for p in pts])
    half = WINDOW_S / 2.0
    rms = np.zeros(len(gpx_utc))
    for i, gt in enumerate(gpx_utc):
        lo = np.searchsorted(utc, gt - half)
        hi = np.searchsorted(utc, gt + half)
        if hi > lo:
            ch = vib[lo:hi]
            rms[i] = np.sqrt(np.mean(ch * ch))
    return {"lats": lats, "lons": lons, "rms": rms,
            "utc": utc, "mag": mag, "vib": vib,
            "gpx_utc": gpx_utc, "gpx_name": gpx.name}


def rms_to_color(v, vmin, vmax):
    t = max(0.0, min(1.0, (v - vmin) / (vmax - vmin) if vmax > vmin else 0.5))
    # Green → Yellow → Red
    if t < 0.5:
        r = int(255 * (2 * t)); g = 200; b = 0
    else:
        r = 255; g = int(200 * (1 - 2 * (t - 0.5))); b = 0
    return f"#{r:02x}{g:02x}{b:02x}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", action="append", nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--tz-offset", type=int, default=DEFAULT_TZ)
    args = ap.parse_args()

    pushes = []
    for grp in args.pair:
        if len(grp) < 2:
            sys.exit(f"--pair needs GPX + ART (got {grp})")
        gpx = Path(grp[0]).expanduser()
        arts = [Path(p).expanduser() for p in grp[1:]]
        d = push_data(arts, gpx, args.tz_offset)
        if d is None:
            print(f"skip: no data for {gpx.name}", file=sys.stderr)
            continue
        d["arts"] = arts
        pushes.append(d)
        print(f"loaded {gpx.name}: {len(d['lats']):,} pts, "
              f"covered={int(np.sum(d['rms']>0)):,}")

    all_rms = np.concatenate([p["rms"][p["rms"] > 0] for p in pushes])
    vmin = float(np.percentile(all_rms, 5))
    vmax = float(np.percentile(all_rms, 95))
    all_lats = np.concatenate([p["lats"] for p in pushes])
    all_lons = np.concatenate([p["lons"] for p in pushes])
    center = [float(all_lats.mean()), float(all_lons.mean())]

    m = folium.Map(location=center, zoom_start=15, tiles="OpenStreetMap",
                   control_scale=True)
    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        attr="OSM HOT", name="OSM Humanitarian", overlay=False
    ).add_to(m)
    folium.TileLayer("CartoDB positron", name="CartoDB light").add_to(m)
    folium.TileLayer("CartoDB dark_matter", name="CartoDB dark").add_to(m)
    # ESRI World Imagery for aerial view
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
              "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="ESRI", name="ESRI satellite", overlay=False
    ).add_to(m)

    # Plot each push as a coloured polyline (one segment per GPS pair)
    push_layer = folium.FeatureGroup(name="push tracks", show=True)
    for p in pushes:
        lats, lons, rms = p["lats"], p["lons"], p["rms"]
        for i in range(len(lats) - 1):
            if rms[i] == 0 and rms[i+1] == 0:
                # uncovered segment — thin grey
                folium.PolyLine([[lats[i], lons[i]], [lats[i+1], lons[i+1]]],
                                color="#aaaaaa", weight=1.5, opacity=0.5,
                                dash_array="3,5").add_to(push_layer)
                continue
            v = 0.5 * (rms[i] + rms[i+1])
            color = rms_to_color(v, vmin, vmax)
            folium.PolyLine([[lats[i], lons[i]], [lats[i+1], lons[i+1]]],
                            color=color, weight=5, opacity=0.85).add_to(push_layer)
    push_layer.add_to(m)

    # Top hits per push, then global ranking
    hit_layer = folium.FeatureGroup(name="top hits", show=True)
    hits = []
    for p in pushes:
        order = np.argsort(p["vib"])[::-1]
        kept_t = []
        for idx in order:
            t_here = p["utc"][idx]
            if all(abs(t_here - kt) > DEDUP_GAP_S for kt in kept_t):
                kept_t.append(t_here)
                j = int(np.argmin(np.abs(p["gpx_utc"] - t_here)))
                hits.append({
                    "peak": float(p["mag"][idx]),
                    "vib":  float(p["vib"][idx]),
                    "lat":  float(p["lats"][j]),
                    "lon":  float(p["lons"][j]),
                    "push": p["gpx_name"].replace("GPS-", "").replace(".gpx", ""),
                    "t_offset": t_here - p["utc"][0],
                })
                if len(kept_t) >= 5:
                    break
    hits.sort(key=lambda h: -h["peak"])
    hits = hits[:args.top]

    for rank, h in enumerate(hits, 1):
        mm = int(h["t_offset"] // 60); ss = h["t_offset"] % 60
        popup = folium.Popup(
            f"<b>Hit #{rank}</b><br>"
            f"Peak |a|: <b>{h['peak']:.1f} m/s²</b> ({h['peak']/9.81:.1f} g)<br>"
            f"Vibration: {h['vib']:.1f} m/s²<br>"
            f"Push: {h['push']}<br>"
            f"Time into push: {mm:02d}:{ss:05.2f}<br>"
            f"Lat: {h['lat']:.6f}<br>Lon: {h['lon']:.6f}<br>"
            f'<a href="https://www.openstreetmap.org/?mlat={h["lat"]:.6f}'
            f'&mlon={h["lon"]:.6f}#map=19/{h["lat"]:.6f}/{h["lon"]:.6f}" '
            f'target="_blank">OSM</a> · '
            f'<a href="https://www.google.com/maps/@?api=1&map_action=pano'
            f'&viewpoint={h["lat"]:.6f},{h["lon"]:.6f}" '
            f'target="_blank">Street View</a>',
            max_width=320,
        )
        folium.CircleMarker(
            location=[h["lat"], h["lon"]],
            radius=12, color="black", weight=2,
            fill=True, fill_color="yellow", fill_opacity=0.95,
            popup=popup, tooltip=f"#{rank}  {h['peak']:.0f} m/s²",
        ).add_to(hit_layer)
        folium.map.Marker(
            [h["lat"], h["lon"]],
            icon=DivIcon(icon_size=(40, 18), icon_anchor=(-6, 6),
                         html=f'<div style="font-size:11px;font-weight:bold;'
                              f'color:black;background:yellow;'
                              f'border:1px solid black;padding:1px 3px;'
                              f'border-radius:3px;">#{rank}</div>'),
        ).add_to(hit_layer)
    hit_layer.add_to(m)

    # Legend
    legend = f"""
    <div style="position: fixed; bottom: 30px; left: 30px; width: 230px;
                background: white; border:2px solid #333; padding:10px;
                font-family:sans-serif; font-size:12px; z-index:9999;
                box-shadow:2px 2px 6px rgba(0,0,0,0.3);">
      <b>Road vibration RMS (m/s²)</b><br>
      <div style="height:14px;width:100%;
                  background:linear-gradient(to right,#00c800,#ffc800,#ff0000);
                  border:1px solid #333;margin:5px 0;"></div>
      <span style="float:left;">{vmin:.2f}</span>
      <span style="float:right;">{vmax:.2f}</span>
      <div style="clear:both;"></div>
      <hr style="margin:6px 0;">
      ★ yellow circles = top {len(hits)} hits<br>
      Click a circle for OSM + Street View links.<br>
      Dashed grey = no sensor coverage.
    </div>"""
    m.get_root().html.add_child(folium.Element(legend))

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(args.out))
    print(f"\nwrote {args.out}")
    print(f"colour scale: {vmin:.2f}–{vmax:.2f} m/s² RMS")
    print(f"top {len(hits)} hits:")
    for rank, h in enumerate(hits, 1):
        print(f"  #{rank}: {h['lat']:.6f}, {h['lon']:.6f}   "
              f"|a|={h['peak']:.1f} m/s²  ({h['push']})")


if __name__ == "__main__":
    main()
