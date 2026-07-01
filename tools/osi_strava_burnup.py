#!/usr/bin/env python3
"""
osi_strava_burnup.py — generate a single self-contained
osi_wheelchair_burnup.html animated burn-up chart of all Vasil's
Wheelchair activities from Strava.

Spec: OSI-STRAVA-BURNUP-SPEC.md (Drive id 12ZJCh-YYzQMT_p7ml82Pt7hcmlWs7KyUBPEP4sA5lQo).

Pipeline:
  1. /athlete/activities paginated 200/page until empty
  2. Filter type == "Wheelchair"
  3. Group by (year, month). Fill every calendar month between first
     and last activity (flat-line months allowed).
  4. For each month: build cumulative-km array of length days_in_month.
  5. Render to a one-file HTML (SVG + inline JS).

Output: ./osi_wheelchair_burnup.html (override with --out)
"""
import argparse
import calendar
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from waytrace_strava import get_access_token  # noqa: E402


def fetch_all_wheelchair(token, debug=False):
    """Paginate /athlete/activities, return Wheelchair-only list."""
    out = []
    page = 1
    while True:
        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 200, "page": page})
        r.raise_for_status()
        chunk = r.json()
        if not chunk:
            break
        keep = [a for a in chunk if a.get("type") == "Wheelchair"]
        out.extend(keep)
        if debug:
            print(f"  page {page}: {len(chunk)} activities, {len(keep)} wheelchair (total {len(out)})", flush=True)
        page += 1
        if len(chunk) < 200:
            break
    return out


def build_months(activities):
    """Group activities into a complete (year, month) sequence from first
    to last. Returns list of month dicts in chronological order."""
    if not activities:
        return []

    parsed = []
    for a in activities:
        sd = a.get("start_date_local")
        if not sd:
            continue
        dt = datetime.fromisoformat(sd.replace("Z", "+00:00")
                                    if sd.endswith("Z") else sd)
        parsed.append({
            "date":  dt.date(),
            "km":    float(a.get("distance", 0.0)) / 1000.0,
            "mvg_s": int(a.get("moving_time", 0)),
        })
    parsed.sort(key=lambda x: x["date"])

    first_y, first_m = parsed[0]["date"].year,  parsed[0]["date"].month
    last_y,  last_m  = parsed[-1]["date"].year, parsed[-1]["date"].month

    # Bucket by (y, m)
    buckets = {}
    for p in parsed:
        key = (p["date"].year, p["date"].month)
        buckets.setdefault(key, []).append(p)

    month_names = ["January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November",
                   "December"]
    months = []
    y, m = first_y, first_m
    while (y, m) <= (last_y, last_m):
        days = calendar.monthrange(y, m)[1]
        cum = [0.0] * days
        running = 0.0
        # daily totals
        daily = [0.0] * days
        moving_total = 0
        for act in buckets.get((y, m), []):
            d = act["date"].day - 1
            daily[d] += act["km"]
            moving_total += act["mvg_s"]
        for i in range(days):
            running += daily[i]
            cum[i] = round(running, 3)
        months.append({
            "label":              f"{month_names[m-1]} {y}",
            "days":               days,
            "daily_cumulative_km": cum,
            "total_km":           round(cum[-1], 3),
            "moving_time_seconds": moving_total,
        })
        # advance one month
        if m == 12:
            y += 1; m = 1
        else:
            m += 1
    return months


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OSI WayTrace — Wheelchair Burn-Up</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f7f6f2;
    --ink: #1a1a1a;
    --muted: #8a8a8a;
    --orange: #FF6B00;
    --orange-deep: #cc4f00;
    --panel: #ffffff;
  }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--ink);
               font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                            Helvetica, Arial, sans-serif;
               height: 100vh; overflow: hidden; }
  body { display: flex; flex-direction: column; }
  .wrap { flex: 1 1 auto; display: flex; flex-direction: column;
          padding: 18px 28px 10px; min-height: 0; }
  .brand { font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
           color: var(--muted); margin-bottom: 8px; }
  .header { display: grid; grid-template-columns: 1fr 1.6fr 1fr;
            align-items: end; gap: 24px; padding: 10px 4px;
            border-bottom: 1px solid #e7e3da; margin-bottom: 10px; }
  .left, .center, .right { line-height: 1.05; }
  .label { font-size: 11px; letter-spacing: 0.18em; text-transform: uppercase;
           color: var(--muted); margin-bottom: 4px; }
  .left .value { font-size: 30px; font-weight: 600; }
  .center .value { font-size: 46px; font-weight: 700; text-align: center; }
  .right { text-align: right; }
  .right .this { font-size: 34px; font-weight: 700; color: var(--orange-deep); }
  .right .all  { font-size: 17px; color: var(--muted); margin-top: 4px; }
  .panel { flex: 1 1 auto; min-height: 0;
           background: var(--panel); border: 1px solid #e7e3da; padding: 8px;
           border-radius: 6px;
           display: flex; }
  svg { display: block; width: 100%; height: 100%; }
  .foot { margin-top: 10px; font-size: 12px; color: var(--muted);
          text-align: center; }
  .hud { display: flex; justify-content: space-between; align-items: center;
         margin-top: 6px; padding: 0 4px;
         font-size: 11px; letter-spacing: 0.10em; color: var(--muted); }
  .hud .keys b { color: var(--ink); font-weight: 600; }
  .hud .speed { font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="brand">OSI WayTrace — Open Streets Initiative</div>
    <div class="header">
      <div class="left">
        <div class="label">time</div>
        <div class="value" id="time-val">0h 0m</div>
      </div>
      <div class="center">
        <div class="label">month</div>
        <div class="value" id="month-label">—</div>
      </div>
      <div class="right">
        <div class="label">this month / total</div>
        <div class="this" id="this-month">0.0 km</div>
        <div class="all"  id="all-time">0.0 km</div>
      </div>
    </div>
    <div class="panel">
      <svg id="chart" viewBox="0 0 1600 800" preserveAspectRatio="none"></svg>
    </div>
    <div class="foot">Strava Wheelchair activities, animated month-by-month at 3 s per month.</div>
    <div class="hud">
      <div class="keys"><b>Space</b> pause  ·  <b>←/→</b> slower/faster  ·  <b>↑/↓</b> next/prev month</div>
      <div class="speed">speed <span id="speed-val">1.0×</span> <span id="pause-tag"></span></div>
    </div>
  </div>

<script>
const MONTHS = __MONTHS_JSON__;

const W = 1600, H = 800, PAD = 60;
const usableW = W - 2 * PAD;
const usableH = H - 2 * PAD;

const GHOST_OPACITY = [
  "rgba(80,  80,  80, 0.80)",
  "rgba(100, 100, 100, 0.60)",
  "rgba(130, 130, 130, 0.45)",
  "rgba(160, 160, 160, 0.30)",
  "rgba(190, 190, 190, 0.18)",
  "rgba(210, 210, 210, 0.10)",
];
const ORANGE = "#FF6B00";
// Default pace: 1 week per second. Each month's duration scales with its
// number of days: days / 7 seconds. February is 4.00 s; 31-day months
// are 4.43 s. Arrow keys multiply / divide this base rate.
const MS_PER_WEEK = 1000;
function monthDurationMs(m) { return (m.days / 7) * MS_PER_WEEK; }

// Playback controls
let speedMul = 1.0;                 // 1.0 = spec default; arrows adjust ×1.25 / ÷1.25
const SPEED_STEP = 1.25;
const SPEED_MIN = 0.1, SPEED_MAX = 8.0;
let paused = false;
function updateHud() {
  document.getElementById("speed-val").textContent = speedMul.toFixed(2) + "×";
  document.getElementById("pause-tag").textContent = paused ? "  (paused)" : "";
}

const svgNS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("chart");

function el(name, attrs) {
  const e = document.createElementNS(svgNS, name);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

function fmtTime(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}
function fmtKm(km) {
  return km.toFixed(1) + " km";
}

// Build polyline points for a given month, scaled to fill the panel.
// daysDrawn is a fractional value: number of full + partial days to render.
function buildPoints(month, daysDrawn) {
  const cum = month.daily_cumulative_km;
  const finalY = Math.max(...cum, 0.0001);
  const N = month.days;
  // y maps [0, finalY] -> [H-PAD, PAD] (top of panel = final cumulative)
  // x maps [0, N-1] -> [PAD, W-PAD]
  function xy(idx, val) {
    const x = PAD + (idx / Math.max(1, N - 1)) * usableW;
    const y = (H - PAD) - (val / finalY) * usableH;
    return [x, y];
  }
  const pts = [];
  const wholeDays = Math.floor(daysDrawn);
  const frac = daysDrawn - wholeDays;
  // Always start with day 0 anchored at bottom-left
  pts.push(xy(0, 0));
  for (let i = 0; i < Math.min(wholeDays, N); i++) {
    pts.push(xy(i, cum[i]));
  }
  // Interpolated partial day
  if (frac > 0 && wholeDays < N) {
    const prev = wholeDays === 0 ? 0 : cum[wholeDays - 1];
    const next = cum[wholeDays];
    const val  = prev + (next - prev) * frac;
    const [x, y] = xy(wholeDays - 1 + frac, val);
    pts.push([x, y]);
  }
  return pts.map(p => p[0].toFixed(2) + "," + p[1].toFixed(2)).join(" ");
}

// Final-state polyline + dashed-top-marker for a completed month
function completedFinal(month) {
  return { pts: buildPoints(month, month.days),
           topY: (H - PAD) - 1 * usableH };  // value/finalY == 1
}

let currentMonthIdx = 0;
let allTime = 0;
let ghosts = [];   // array of { polyline node, dashedLine node, age }

const currentPoly = el("polyline", {
  fill: "none", stroke: ORANGE, "stroke-width": "2.5",
  "stroke-linejoin": "round", "stroke-linecap": "round", points: "",
});
svg.appendChild(currentPoly);

// Panel baseline
svg.appendChild(el("line", {
  x1: PAD, y1: H - PAD, x2: W - PAD, y2: H - PAD,
  stroke: "#dcd8cf", "stroke-width": 1,
}));

function applyGhostStyles() {
  // Re-apply opacity ladder based on current order; ghosts[0] is most recent.
  // Dashed line + km label render ONLY on the most-recent ghost (index 0)
  // — per spec: a single top marker tracking the previous month.
  for (let i = 0; i < ghosts.length; i++) {
    const opacity = GHOST_OPACITY[Math.min(i, GHOST_OPACITY.length - 1)];
    ghosts[i].poly.setAttribute("stroke", opacity);
    const dashStroke = (i === 0) ? GHOST_OPACITY[0] : "rgba(0,0,0,0)";
    ghosts[i].dash.setAttribute("stroke", dashStroke);
    if (ghosts[i].label) ghosts[i].label.setAttribute("fill", dashStroke);
  }
}

function finishMonth() {
  const m = MONTHS[currentMonthIdx];
  // Convert orange to most-recent ghost
  const final = completedFinal(m);
  const ghostPoly = el("polyline", {
    fill: "none", stroke: GHOST_OPACITY[0], "stroke-width": "2.0",
    "stroke-linejoin": "round", "stroke-linecap": "round",
    points: final.pts,
  });
  const dash = el("line", {
    x1: PAD, y1: final.topY, x2: W - PAD, y2: final.topY,
    stroke: GHOST_OPACITY[0], "stroke-width": "1",
    "stroke-dasharray": "6,4",
  });
  // Km label sitting just above the dashed line, right-aligned to the chart.
  const kmLabel = el("text", {
    x: W - PAD - 4, y: final.topY - 4,
    "text-anchor": "end",
    "font-family": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif",
    "font-size": "12",
    "font-weight": "600",
    "font-variant-numeric": "tabular-nums",
    fill: GHOST_OPACITY[0],
  });
  kmLabel.textContent = m.total_km.toFixed(1) + " km";
  svg.insertBefore(ghostPoly, currentPoly);
  svg.insertBefore(dash, currentPoly);
  svg.insertBefore(kmLabel, currentPoly);
  ghosts.unshift({ poly: ghostPoly, dash, label: kmLabel });
  while (ghosts.length > GHOST_OPACITY.length) {
    const old = ghosts.pop();
    old.poly.remove();
    old.dash.remove();
    if (old.label) old.label.remove();
  }
  applyGhostStyles();
  // Lock in this month's km
  allTime += m.total_km;
}

function drawCurrent(daysDrawn) {
  const m = MONTHS[currentMonthIdx];
  currentPoly.setAttribute("points", buildPoints(m, daysDrawn));
  // Live header updates
  document.getElementById("month-label").textContent = m.label;
  // Interpolated this-month km
  let kmNow;
  const wholeDays = Math.floor(daysDrawn);
  const frac = daysDrawn - wholeDays;
  if (wholeDays >= m.days) {
    kmNow = m.total_km;
  } else if (wholeDays <= 0) {
    kmNow = m.daily_cumulative_km[0] * frac;
  } else {
    const prev = m.daily_cumulative_km[wholeDays - 1];
    const next = m.daily_cumulative_km[wholeDays];
    kmNow = prev + (next - prev) * frac;
  }
  document.getElementById("time-val").textContent =
      fmtTime(m.moving_time_seconds * Math.min(1, daysDrawn / m.days));
  document.getElementById("this-month").textContent = fmtKm(kmNow);
  document.getElementById("all-time").textContent = fmtKm(allTime + kmNow);
}

// Virtual elapsed within the current month: advanced by (real_dt × speedMul)
// each frame, frozen while paused. monthStart goes away; replaced with this.
let monthElapsed = 0;
let lastFrameTs = null;

function tick(now) {
  if (lastFrameTs === null) lastFrameTs = now;
  const real_dt = now - lastFrameTs;
  lastFrameTs = now;
  const m = MONTHS[currentMonthIdx];
  const monthMs = monthDurationMs(m);
  if (!paused) {
    monthElapsed += real_dt * speedMul;
  }
  const t = Math.min(1, monthElapsed / monthMs);
  const daysDrawn = t * m.days;
  drawCurrent(daysDrawn);
  if (t >= 1) {
    if (currentMonthIdx + 1 < MONTHS.length) {
      finishMonth();
      currentMonthIdx += 1;
      monthElapsed = 0;
      document.getElementById("month-label").textContent =
          MONTHS[currentMonthIdx].label;
      currentPoly.setAttribute("points", "");
    } else {
      // Last month: cap monthElapsed so the chart sits at the finish.
      // Keep ticking so arrow keys still respond.
      monthElapsed = monthMs;
    }
  }
  requestAnimationFrame(tick);
}

function jumpForward() {
  // Skip to the start of the next month (works while paused too).
  if (currentMonthIdx + 1 >= MONTHS.length) {
    monthElapsed = monthDurationMs(MONTHS[currentMonthIdx]);
    drawCurrent(MONTHS[currentMonthIdx].days);
    return;
  }
  finishMonth();
  currentMonthIdx += 1;
  monthElapsed = 0;
  document.getElementById("month-label").textContent =
      MONTHS[currentMonthIdx].label;
  currentPoly.setAttribute("points", "");
}

function jumpBackward() {
  // Step back one month: undo the most-recent ghost, rewind currentMonthIdx.
  if (ghosts.length === 0) {
    monthElapsed = 0;
    currentPoly.setAttribute("points", "");
    return;
  }
  const g = ghosts.shift();
  g.poly.remove();
  g.dash.remove();
  if (g.label) g.label.remove();
  applyGhostStyles();
  if (currentMonthIdx > 0) {
    allTime = Math.max(0, allTime - MONTHS[currentMonthIdx - 1].total_km);
    currentMonthIdx -= 1;
  }
  monthElapsed = 0;
  document.getElementById("month-label").textContent =
      MONTHS[currentMonthIdx].label;
  currentPoly.setAttribute("points", "");
}

window.addEventListener("keydown", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    paused = !paused;
    updateHud();
  } else if (e.code === "ArrowRight" || e.key === "ArrowRight") {
    e.preventDefault();
    speedMul = Math.min(SPEED_MAX, speedMul * SPEED_STEP);
    updateHud();
  } else if (e.code === "ArrowLeft" || e.key === "ArrowLeft") {
    e.preventDefault();
    speedMul = Math.max(SPEED_MIN, speedMul / SPEED_STEP);
    updateHud();
  } else if (e.code === "ArrowUp" || e.key === "ArrowUp") {
    e.preventDefault();
    jumpForward();
  } else if (e.code === "ArrowDown" || e.key === "ArrowDown") {
    e.preventDefault();
    jumpBackward();
  }
});

if (MONTHS.length === 0) {
  document.getElementById("month-label").textContent = "no wheelchair activities";
} else {
  document.getElementById("month-label").textContent = MONTHS[0].label;
  updateHud();
  requestAnimationFrame(tick);
}
</script>
</body>
</html>
"""


def render_html(months, out_path):
    payload = json.dumps(months, separators=(",", ":"))
    html = HTML_TEMPLATE.replace("__MONTHS_JSON__", payload)
    Path(out_path).write_text(html, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="osi_wheelchair_burnup.html")
    ap.add_argument("--cache", default=None,
                    help="cache Strava activity list to JSON for re-renders")
    ap.add_argument("--from-cache", default=None,
                    help="read cached activities instead of hitting Strava")
    args = ap.parse_args()

    if args.from_cache:
        activities = json.loads(Path(args.from_cache).read_text())
        print(f"loaded {len(activities)} cached activities", flush=True)
    else:
        print("authenticating with Strava…", flush=True)
        token = get_access_token()
        print("fetching activities (paginated 200/page)…", flush=True)
        activities = fetch_all_wheelchair(token, debug=True)
        print(f"total wheelchair activities: {len(activities)}", flush=True)
        if args.cache:
            Path(args.cache).write_text(json.dumps(activities))
            print(f"cached → {args.cache}", flush=True)

    months = build_months(activities)
    if not months:
        sys.exit("no wheelchair activities — nothing to render")
    print(f"months built: {len(months)}  ({months[0]['label']} → {months[-1]['label']})",
          flush=True)
    total_km = sum(m["total_km"] for m in months)
    print(f"all-time total: {total_km:.1f} km", flush=True)

    render_html(months, args.out)
    print(f"wrote {args.out}  ({Path(args.out).stat().st_size // 1024} KB)",
          flush=True)


if __name__ == "__main__":
    main()
