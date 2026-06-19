# OSI-007 Phase-2 Video Dashboard — full spec
# Open Streets Initiative
# Written: 2026-06-18, Vasil Taneski + Claude Code, after the v6 sketch.
# Sketch artefact: ~/Downloads/OSI007-layout-sketch.png (v6, dated 2026-06-18).

The output is a single 1920×1080 MP4 per source MOV. It is built as four
layers on top of each other; in the finished frame only the bottom
(video) is always on. The other three are HUD overlays that the viewer
reads at a glance.

---

## LAYER 1 — Video (full screen, always on)

The entire 1920×1080 canvas is the Rear Window footage from the Akaso
V50 X, downscaled from 4K to 1080p, already passed through the OSI-007
single-pass pipeline (osi007_final.py): face blur + license-plate blur
+ YOLO colored boxes per the locked color language:

 RED — any vehicle (car/truck/motorcycle/bus/van)
 ORANGE — large fixed obstacle (container/dumpster/scaffold/skip)
 YELLOW — road-surface failure (linked to ART heavy_bump GPS coords)
 BLUE — small fixed obstacle (bollard/pole/illegal bike)
 GREEN — vulnerable road user (cyclist, stroller, person w/ child)
 PURPLE — person with mobility aid (cane/crutch/walker/rollator)
 WHITE — elderly person (gait + posture)

The HUD overlays sit *on top of* this video so the road behind is
always visible — none of the overlays fills the whole frame.

---

## LAYER 2 — Upper-left readout (the dashboard "primary number")

Top-left corner. NO background panel, NO box — text only, with a thin
black outline (~4-5 px stroke) so it reads on any underlying frame.

Two lines, stacked top → bottom:

### 2a. Push title (fixed for the whole video)
- Source: the Strava activity name fetched via waytrace_strava.py
 (e.g. "Rear Window Push").
- Style: yellow #ffcc00, bold, ~24 pt.
- Does NOT change over time.

### 2b. Primary readout (switches on events — the heart of the dashboard)
- Style: white, bold, ~46 pt.
- DEFAULT: current GPS speed in km/h (e.g. `6.4 km/h`).
- When something noteworthy happens, the readout briefly swaps to show
 that event, then returns to speed after a fixed dwell time.
- Only ONE variant is on screen at a time. If two triggers fire close
 together, the second waits for the first's hold time to expire, then
 runs (queue, not overwrite). The speed readout is the "rest state".

Switching table:

 | Trigger | Flash content | Hold | Color |
 |------------------------------------------|------------------------|------|--------|
 | (none) | `6.4 km/h` | — | white |
 | New heavy_bump in ART | `HEAVY BUMPS 47` | 2 s | orange |
 | ISO 8608 class boundary crossed (E→F etc) | `ISO 8608 E → F` | 3 s | red |
 | YOLO vehicles-passed cumulative +5 | `VEHICLES PASSED 12` | 2 s | green |

DROPPED on Vasil's direction (2026-06-18): people-count flash. Person
count is not the headline; the dashboard stays tight.

---

## LAYER 3 — Upper-right map ("ROUTE" block)

A 460×300 px block, top-right corner. THIN GREEN BORDERLINE outline
(2.5 px, #33aa33). NO fill — the video shows through the inside of the
frame. Header text "ROUTE" in the same green sits at the top-center of
the frame.

Inside the frame:
- The full GPX polyline of this push, drawn as a thick yellow (#ffcc00)
 line on a thin black halo (so it reads regardless of what the video
 shows underneath).
- A single bright red dot (#ff3333), white-outlined, that moves along
 the route in time with the video — i.e. the dot position at video
 time T = the GPX point whose timestamp matches T (after the SYNC
 offset, see implementation notes).
- A small "YOU" label next to the dot, white with black halo.

The map auto-fits the bounding box of the GPX, with a small margin,
so the whole route is always visible — the dot moves, the route does
not pan.

---

## LAYER 4 — Footer trace ("the heart-rate monitor")

A thin band along the bottom of the frame, ~100 px tall, full 1920 px
wide, with a thin red border (#cc0033, 1 px).

### 4a. Background color reflects CURRENT roughness
The whole footer band is ONE uniform color at any moment, set by the
current windowed Wk-weighted RMS:

 green (#003300cc semi-transparent) — RMS < 1.0 m/s² (comfortable)
 amber (#665500cc) — RMS 1.0–5.0 (moderate)
 red (#5a0000cc) — RMS > 5.0 (severe)

It changes smoothly over time as conditions change.

### 4b. Time window: 30-MINUTE rolling
- Left edge = the moment 30 minutes ago.
- Right edge = NOW (the moment of the current video frame).
- Older data scrolls off the left as the push progresses.
- Tick labels along the bottom: `-30m -25m -20m -15m -10m -5m now`.

### 4c. The trace itself
- Thin white line (~1.7 px).
- DEFAULT metric: VDV (or IRI proxy — final call TBD on first build).
- Auto-switches based on ART events:
 - During a `wheelie` or `tilt` event → switch to angular-rate trace
 for the event duration + 5 s, then crossfade back to default.
 - During a `heavy_bump` event → switch to the jerk channel briefly,
 same return rule.
- A small italic label in the bottom-right shows which metric is being
 plotted right now ("metric: VDV (default)" or "metric: ω (gyro)" etc.).

### 4d. Threshold line
A horizontal dashed white line at the legal/health threshold (ISO 2631-1
VDV HIGH-risk = 17.0 m/s¹·⁷⁵). Trace above the line is dangerous to a
human body sustained over time. Labeled "ISO 2631-1 HIGH-risk threshold"
at the left edge.

---

## WHAT IT ADDS UP TO

The whole frame becomes a dashboard of the push: the video is the truth
of what was there; the HUD over it is the truth of what the push
*cost* — speed, route, surface quality over time, threshold crossings,
surface-class changes, heavy-impact moments. Nothing is on screen all
at once; the dashboard reveals each piece of information when it
matters, then returns to the default reading. The viewer's attention
follows the road, not the chrome.

---

## IMPLEMENTATION ROADMAP (separate from the spec above)

This is HOW the dashboard will be built. Not part of the visual spec;
left here so future Claude Code knows where to start.

1. **Video ↔ ART synchronisation (OSI-016 SYNC clapper).**
 For each source MOV, scan the audio track for the OSI-016 "Push Off"
 5-note chime (C5–E5–G5–A5–C6, ~1.8 s, R.raw.waytrace_theme). Each
 detected chime corresponds to one `sync_pulse` row in the ART CSV
 (matched by ordinal: first chime in audio = first sync_pulse row,
 etc.). The offset between video-time-of-chime and ART-timestamp-of-
 sync_pulse gives the per-MOV `video_to_art_offset_ms`. Bookend
 sanity check: first and last chime offsets agree to within a few ms.

2. **Per-frame ART lookup.**
 At video frame T (after offset), look up the nearest ART row(s) to
 compute:
 - Speed (m/s → km/h) — from GPX interpolation at this time.
 - Current windowed Wk-weighted RMS — for footer bg color.
 - Current VDV (default trace value) and any switched metric.
 - Recent events in a sliding window — drives the upper-left flash
 and the footer auto-switch.

3. **Map dot position.**
 GPX-time → (lat, lon) by linear interpolation. Project to map pixel
 space using the GPX bbox transform built once at start.

4. **HUD compositor.**
 New tool: `osi007_dashboard.py`. Inputs: the consolidated MP4
 produced by osi007_final.py (the layer-1 video), the ART CSV, the
 GPX, the Strava title. Output: RW-YYYYMMDDHHMM-final.mp4 per the
 SRS naming convention. cv2 + ffmpeg pipeline; per-frame: read frame
 → composite layers 2/3/4 → write frame. Audio is muxed through from
 the source.

5. **Acceptance for the dashboard build.**
 - On a known test MOV: the dot's position on the map at video time
 T matches Vasil's eye-test of where he was at that moment.
 - The footer trace bg flips colour on an ART-quiet → ART-rough
 transition seen in the source.
 - The upper-left readout flashes the correct event values at the
 correct moments (verify against the source ART events list).
 - The push title is exactly the Strava activity name.

---

(End of original OSI-007 Phase-2 dashboard spec.)


====================================================================
## APPENDIX A — ISO 8608 ROUGHNESS COEFFICIENT (method behind the
## footer class line and the upper-left "ISO 8608 X → Y" flash)
## Appended 2026-06-19, from Vasil's objective spec. Append-only.
====================================================================

The dashboard already references ISO 8608 class in two places: the
upper-left flash ("ISO 8608 E → F") and, implicitly, the footer roughness.
This appendix specifies HOW the class is computed, so the class shown is
defensible and reproducible — not eyeballed. It is the rigorous method
behind those displays. It runs in waytrace_analysis.py (offline), and the
dashboard reads its per-window output; the dashboard does NOT recompute
physics per frame.

OBJECTIVE: a real-time-capable road-quality classification based on
ISO 8608 from the IMU.

1. DATA INPUTS
 - Primary: vertical acceleration a_v in m/s².
 ON THIS RIG vertical = Y_accel − g (gravity removed). Generic ISO 8608
 text says "Z-axis"; that means WORLD-vertical, which on this mount is
 Y_accel, NOT the phone's Z (Z is lateral here — see PHYSICAL SETUP in
 the SRS). Using the wrong axis invalidates the whole result.
 - Supporting: GPS-derived velocity v (m/s) and timestamp t (from GPX).

2. CORE PROCESSING PIPELINE
 - Windowing: process in spatial windows of 50 meters.
 - Time→space transform: convert a_v(t) to a profile over distance x
 using the current velocity v (x = ∫v dt). Resample to uniform spatial
 spacing within the window.
 - Displacement: obtain road displacement z(x). To go from acceleration
 to displacement, double-integrate with high-pass / detrend to remove
 integration drift. REUSE the existing integration/detrend path in
 waytrace_analysis.py — do not invent a second one.
 - PSD estimation: FFT the displacement z(x) to estimate the Power
 Spectral Density S_z(Ω) over spatial angular frequency Ω (rad/m).
 - Coefficient fit: least-squares curve-fit to the ISO 8608 model
 S_z(Ω) = C · (Ω / Ω₀)^(−2), with Ω₀ = 1 rad/m (project ref REF-011
 uses n₀ = 0.1 cyc/m; keep the conversion explicit and documented in
 code so the constant is unambiguous). Extract the roughness
 coefficient C.

3. CATEGORIZATION (lookup on C, in units of 10⁻⁶ m³/rad)
 - C < 16 → Class A (Excellent)
 - 16 ≤ C < 64 → Class B (Good)
 - 64 ≤ C < 256 → Class C (Fair)
 - 256 ≤ C < 1024 → Class D (Poor)
 - 1024 ≤ C < 4096 → Class E (Very Poor / Rough)
 - C ≥ 4096 → Class F (Hazardous)
 (REF-011 lists A–H; this objective collapses the worst grades into F
 for display. Keep A–H available internally; show A–F.)

4. WHAT THE DASHBOARD DOES WITH IT
 - The per-50 m class (A–F) is the value behind the upper-left
 "ISO 8608 X → Y" flash: when consecutive windows cross a class
 boundary upward (A–D → E/F), fire the red 3 s flash already specified
 in LAYER 2's switching table.
 - OPTIONAL second footer mode: a running line of C vs route DISTANCE
 with A–F colour bands (green→red) behind it, as an alternative to the
 time-based VDV footer. This is the "C-vs-distance dual-layer view".
 Default footer stays the 30-min VDV "heart-rate monitor" (LAYER 4);
 the C-vs-distance band is a toggle for analysis exports, decided at
 build time.
 - BASELINE + THRESHOLD on the footer: the "normal" baseline is the
 A/B band; the threshold whose crossing matters is D→E (good-enough
 town surface → rough). Crossing upward is what turns the footer /
 border toward red and (per LAYER 2) can flash the class change.

5. HONESTY GUARDS (project rule — claim only what is computed)
 - C and ISO 8608 class are ESTIMATES from a single caster-mounted IMU,
 not a calibrated profilometer. Label the panel sensor-derived.
 - VDV is Wk-weighted (OSI-006b). IRI stays a PROXY and is never the
 headline. ISO 8608 class + VDV are the defensible displays.
 - Deterministic: the same ART + GPX must always yield the same class
 sequence and the same footer.

6. ACCEPTANCE (for the ISO 8608 layer specifically)
 [ ] On a push with an obviously smooth stretch and an obviously rough
 stretch, the class sequence reflects it (smooth → A/B, rough → E/F).
 [ ] Class is computed per 50 m window from C, not hard-coded from RMS.
 [ ] Same input → same class sequence (reproducible).
 [ ] The upper-left flash fires on a real A–D → E/F crossing in the
 source, at the correct synced moment.

(End of Appendix A.)


====================================================================
## APPENDIX B — CLARIFICATIONS (appended 2026-06-19, from Vasil)
====================================================================

B-1. "HEART-RATE MONITOR" IS A METAPHOR — NOT A REAL HEART RATE.
 The nickname for LAYER 4 ("the heart-rate monitor") refers ONLY to the
 VISUAL SHAPE of the footer trace — a thin line that rises and falls,
 resembling the line on a hospital cardiac monitor. It does NOT mean
 the system measures, records, or displays the rider's heart rate or
 pulse. WayTrace has NO heart-rate sensor and NO heart-rate data.
 Vasil deliberately did not buy a heart-rate watch.
 What LAYER 4 actually plots is VIBRATION: the Wk-weighted RMS / VDV
 of the road, i.e. road roughness and the physical cost of the surface
 — never anything about the body's cardiovascular system.
 Any future reader (human or AI) must not introduce, infer, or display
 heart rate anywhere in the project. If the metaphor risks confusion in
 a public-facing context (e.g. the eSociety pitch or a YouTube caption),
 prefer calling LAYER 4 the "road-vibration trace" or "surface trace".

B-2. THE ISO 8608 VALUE-OVER-TIME GRAPH IS CONFIRMED FOR IMPLEMENTATION.
 Vasil has confirmed (2026-06-19) that the footer graph showing a value
 changing over time, classified by ISO 8608 per Appendix A, is to be
 BUILT — it is not optional or hypothetical. Concretely:
 - The footer (LAYER 4) plots a road metric AS A FUNCTION OF TIME
 (the 30-minute rolling trace), with the threshold/baseline line, and
 the background colour + class flashes driven by the ISO 8608 class
 computed per Appendix A.
 - The default time-based trace (VDV / Wk-RMS over the rolling window)
 is the PRIMARY footer and must be implemented first.
 - The C-vs-DISTANCE banded view from Appendix A §4 remains the optional
 alternate/export mode; the time-based graph is the one that ships in
 the video dashboard.
 - This is the "graphic that has value over time" Vasil asked for:
 a line that moves up and down with the road, a normal/baseline line,
 a threshold line above which the surface is punishing, and colour that
 turns toward red (and can flash the ISO 8608 class change) when the
 value crosses the threshold.
 Build order: implement LAYER 4 time-graph + threshold + colour + the
 Appendix A ISO 8608 class computation feeding it, as part of the
 OSI-007 dashboard (osi007_dashboard.py). Acceptance combines LAYER 4's
 acceptance (footer colour flips on rough transitions) with Appendix A
 §6 (class sequence reflects real smooth/rough stretches).

(End of Appendix B.)
