# Wheelchair Vibration Measurement — Literature Brief for WayTrace v2

## 1. Key prior-art studies

**(a) VanSickle, Cooper, Boninger, DiGiovine — "Analysis of vibrations induced during wheelchair propulsion." *Assistive Technology / Arch Phys Med Rehabil*, 2001.**
Measured 3-axis acceleration at seat, footrest, and a bite-bar (head) of subjects propelling over a Simulated Road Course (SRC) of eight rigid obstacles. Used purpose-built "SMARTACCELEROMETER" triaxial sensors; mounted on wheelchair frame, footrest, and seat. Sample rate ~200 Hz. Computed RMS acceleration following ISO 2631 (then 1985 edition). Key finding: frame acceleration exceeded the 8-h fatigue-decreased performance boundary on SRC; a vertical resonant peak averaged **8.1 Hz** across 8 subjects — squarely inside ISO 2631-1 Wk's most heavily weighted band.

**(b) Wolf, Pearlman, Cooper et al. — "Vibration exposure of individuals using wheelchairs over sidewalk surfaces." *Med Eng Phys*, 2005 (PubMed 16418059).**
Ten non-disabled subjects propelled manual and powered wheelchairs over nine sidewalk surfaces at 1 and 2 m/s. Triaxial accelerometer on wheelchair frame at seat level; RMS vertical acceleration compared per surface. Key finding: 8-mm bevel interlocking concrete produced significantly higher RMS than other surfaces; the team recommended a **6-mm maximum bevel** for accessible sidewalks — this fed directly into US Access Board guidance.

**(c) Wolf, Cooper, Pearlman, Fitzgerald, Kelleher — "Longitudinal assessment of vibrations during manual and power wheelchair driving over select sidewalk surfaces." *J Rehabil Res Dev*, 2007, 44(4).**
Follow-up tracking surface degradation and vibration over years. Same methodology (frame-mounted triaxial). Key finding: surface-induced vibration is reproducible enough across years to be a useful infrastructure metric.

**(d) Garcia-Mendez, Pearlman, Boninger, Cooper — "Health risks of vibration exposure to wheelchair users in the community." *J Spinal Cord Med*, 2013, 36(4):365–375.**
Real-world community exposure over full days. Triaxial accelerometers at **seat, backrest, and footrest**. Sample rate **60 Hz**, bandwidth 0.5–22 Hz. Full ISO 2631-1 weightings: Wk and Wd at the seat, Wd and Wc at the backrest. Reported **weighted RMS and VDV**. 37 users (24 rigid, 13 folding, some with suspension). Key finding: users were continuously exposed at **0.83 ± 0.17 m/s²** for ~13 h/day — *within or above the ISO health caution zone* — and suspension showed no statistically significant attenuation.

**(e) Chénier & Aissaoui — "Effect of Wheelchair Frame Material on Users' Mechanical Work and Transmitted Vibration." *BioMed Research International*, 2014.**
Six folding chairs (1 titanium, 1 carbon, 4 aluminum). Five piezoelectric triaxial accelerometers (240 Hz bandwidth) on aluminum plates at the four wheel hubs plus one in the cushion under the ischion. **Sampling 3200 Hz.** ISO 2631-1 weighting; computed vibration transmissibility (seat / hub). Key finding: carbon had lowest transmissibility; titanium was no better than aluminum. Important caveat: study deliberately **excluded rigid frames** to constrain scope, so it does not directly answer "folding vs rigid."

**(f) Misch & Sprigle — "Estimating whole-body vibration limits of manual wheelchair mobility over common surfaces." *J Rehabil Assist Technol Eng*, 2022 (PMC9036318).**
Robotic propulsion over linoleum, brick, pavers, aluminum grates. Single triaxial (X16-1D) on **top of seat cushion**. **200 Hz**, ISO 2631-1 z-axis Wk filter chain (high-pass, low-pass, acceleration-velocity transition, upward step). RMS frequency-weighted vertical (awz). Key finding: even the worst surface required >14 h/day to reach the Exposure Action Value — health risk under typical daily propulsion (~1 h) is low *in healthy populations*. Reframes the question from "is it harmful" to "is it comfortable / discriminative between surfaces."

**(g) Larivière, Chadefaux, Sauret, Thoreux — "Vibration Transmission during Manual Wheelchair Propulsion: A Systematic Review." *Vibration* (MDPI), 2021, 4(2):29.**
Synthesises 35 papers. Confirms that mounting location, surface, speed, tyre pressure, mass, and frame all confound results; that ISO 2631-1 weighting is the de-facto standard despite being developed for able-bodied seated workers; and that VDV / MTVV are reported inconsistently across the literature.

**(h) Misch, Sprigle et al. — "Modal Characterization of Manual Wheelchairs." *Vibration*, 2022, 5(3):25.**
Impact-hammer modal analysis of wheelchair frames. Useful because it documents frame natural frequencies in the 8–30 Hz range — overlapping the bumpy-pavement excitation band.

## 2. Standard methodology — what "doing it right" looks like

**Mounting location.** Three accepted points in descending order of relevance to ISO 2631-1: (1) **seat / cushion top under ischial tuberosities** — what the body actually feels (Garcia-Mendez 2013; Misch 2022); (2) **frame, seat tube near hip** — proxy for seat, easier to attach (Wolf 2005; VanSickle 2001); (3) **caster fork** — captures excitation but is upstream of every spring/damper element. Cooper-group findings note ~80% of frame vibration *originates* from front casters, so caster-fork readings overstate body-relevant magnitudes.

**Sample rate.** ISO 2631-1's Wk band is 0.5–80 Hz; Nyquist alone wants ≥160 Hz. Field studies are pragmatic: Garcia-Mendez used **60 Hz** (sufficient because Wk rolls off above ~16 Hz heavily and 22 Hz was the upper analysis band); Misch & Wolf used **200 Hz**; lab modal work uses 1 kHz+. **120 Hz on the Xiaomi is comfortably adequate for Wk-weighted RMS and VDV.** It is *not* adequate for unweighted shock peaks or modal analysis above 60 Hz.

**Weightings.** Wk (vertical seat z-axis) and Wd (horizontal seat x/y) are the wheelchair standard, with seat-back Wc on x and Wd on z when measuring backrest input. No wheelchair-specific weighting exists; the Larivière review explicitly notes this gap.

**Confounds controlled.** Tyre pressure (Sprigle, Vorrink), rider mass, push speed (typically standardised to 1 m/s), surface taxonomy (Wolf's 9-surface set is the canonical reference), cushion type, and wheelchair configuration. Real-world studies (Garcia-Mendez) accept these as variance.

**Metrics.** Weighted RMS is universal; **VDV** (∫a⁴ dt)^¼ is recommended whenever crest factor > 9 (i.e. discrete bumps dominate, which is true for sidewalks) — see ISO 2631-1 §6. **MTVV** (running 1-s RMS max) is reported less often but is required by ISO when shocks are present.

## 3. Foldable vs rigid — vibration implications

The systematic review (Larivière 2021) and Kwarciak's curb-descent paper note: **rigid and suspension frames showed lower vibration than folding frames** in some controlled tests, but the difference was **not statistically significant**. Counter-intuitively, in 15 cm curb descents, folding frames sometimes produced *lower* peak accelerations because the cross-brace flex acts as a compliant element. Frame natural frequencies (Misch 2022 modal study) overlap the 8–30 Hz excitation band, so changing frames shifts where resonances land more than it changes total energy.

Garcia-Mendez 2013 (community study, mixed cohort of 24 rigid + 13 folding) found **suspension provided no significant attenuation** in real-world use — meaning the dominant variables in field conditions are tyres, casters and surface, not frame topology.

**Practical answer for your migration:** absolute RMS/VDV values will shift when you move from foldable to rigid — likely upward, but the literature does not commit to a specific factor. **Relative rankings between streets, recorded on the *same* chair, are preserved.** Cross-chair comparisons require recalibration. The cleanest control is a known reference surface (a flat indoor corridor + a known curb) recorded on both chairs as anchor points.

## 4. Smartphone-as-sensor — possibilities and limits

Direct wheelchair-smartphone work exists but is sparse and mostly *classification* rather than ISO-compliant exposure measurement:

- **WheelShare** (Iwasawa et al. and follow-ons) — smartphone accelerometer + gyroscope on wheelchair, ML surface classifier, ~91% accuracy. arXiv 2101.03724 / Springer LNCS.
- **MyPath** (Saha et al., RESNA 2022) — smartphone IMU + GPS, surface accessibility classification, OSM contribution pipeline. Closest analogue to WayTrace.
- **Pavement-roughness validation literature** (Douangphachanh & Oneyama; Islam; multiple IEEE/Nature Sci Rep 2024–2025) — smartphones vs class-1 inertial profilers in vehicles. Typical results: R² 0.75–0.87 between smartphone RMS and IRI; relative error <10% after suspension calibration. These are **vehicle** studies, not wheelchair; the transfer function differs but the methodological lessons carry.

**Where the phone falls short:**

- Sample rate ceiling 100–500 Hz depending on Android driver (Xiaomi 120 Hz fine for Wk).
- No calibration certificate; consumer MEMS have 1–3% gain error and small temperature drift.
- Mount compliance: a phone clipped to a frame tube is not at the seat; there is an unknown transfer function between mount point and ischium.
- Axis convention varies by device; gravity vector must be used to rotate into the world frame on every sample.

**Mitigations supported by literature:**

- Stationary 1-g calibration (Douangphachanh): place phone flat, capture 60 s, verify z = 9.81.
- Drop test / step test for transfer-function characterisation (Misch modal approach in miniature).
- Wk weighting itself *de-emphasises* the band (>20 Hz) where smartphone MEMS noise floor is worst — fortuitous alignment.
- Co-recording with a reference IMU on a representative route, once per chair, to back out the mount transfer function.

## 5. Concrete recommendations for WayTrace v2

- **Mount the phone on the rigid seat tube near the hip, not on the caster fork or the backrest.** Seat-level data is what ISO 2631-1 was written for; caster-fork data overstates body-relevant vibration by ~3×. Backing: Wolf 2005, Garcia-Mendez 2013, Misch 2022. Cost: a 3D-printed clamp.
- **Keep sampling at 120 Hz; do not chase higher.** Sufficient for Wk-weighted RMS/VDV; phone-MEMS noise increases above 50 Hz where Wk is already attenuated. Backing: Garcia-Mendez 2013 (used 60 Hz successfully); ISO 2631-1 §5. Cost: zero.
- **Implement the full Wk filter chain** (high-pass 0.4 Hz, low-pass 100 Hz, a-v transition near 2 Hz, upward step near 8 Hz), per ISO 2631-1 Annex A. Report awz (weighted RMS), **VDV**, and **MTVV** — the second two matter for sidewalks because crest factor > 9. Backing: Garcia-Mendez 2013; ISO 2631-1 §6. Cost: ~half a day of DSP.
- **Rotate to world frame using gravity + rotation vector.** Phone orientation on frame is arbitrary; ISO weightings are axis-specific. Use Android's `TYPE_ROTATION_VECTOR` (sensor fusion of gyro/accel/mag). Backing: standard inertial-navigation practice; required by ISO 2631-1 axis definitions.
- **Run a calibration protocol once per chair.** (1) Stationary 60 s flat (gain check). (2) Known indoor smooth corridor at 1 m/s × 60 s (baseline floor). (3) Three drops off a measured 5-cm threshold (transient response). Save these as the per-chair reference. Repeat on the rigid when it arrives. Backing: smartphone pavement-validation literature (Douangphachanh; Sci Rep 2025); Misch modal approach. Cost: 15 min per chair.
- **Treat absolute thresholds with caution; lead with relative street rankings.** Misch 2022 shows healthy users rarely exceed ISO action values; advocacy value lies in *comparing streets within Skopje*, not in claiming health-risk breaches. Backing: Misch 2022 + Garcia-Mendez 2013 disagreement on exposure-vs-risk. Cost: framing only.
- **Record speed (from Strava GPS) per segment and normalise.** Wolf 2005 standardised at 1 m/s; uncontrolled speed is the single biggest confound. Reject or speed-bin segments outside 0.8–1.5 m/s. Backing: Larivière 2021 review. Cost: a filter on the existing pipeline.
- **Literature-search inconclusive** on a published foldable→rigid conversion factor; do not publish cross-chair absolute comparisons without your own measured anchor.

## Sources

- [Wolf et al. 2005 — Vibration exposure over sidewalk surfaces (PubMed)](https://pubmed.ncbi.nlm.nih.gov/16418059/)
- [Wolf et al. 2007 — Longitudinal sidewalk assessment (JRRD)](https://www.rehab.research.va.gov/jour/07/44/4/wolf.html)
- [VanSickle & Cooper — Vibrations during wheelchair propulsion (JRRD)](https://www.rehab.research.va.gov/jour/01/38/4/vansi384.htm)
- [Garcia-Mendez et al. 2013 — Health risks of vibration in the community (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC3758533/)
- [Chénier & Aissaoui 2014 — Frame material and transmitted vibration (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC4167955/)
- [Misch & Sprigle 2022 — WBV limits over common surfaces (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC9036318/)
- [Larivière et al. 2021 — Systematic review (MDPI Vibration)](https://www.mdpi.com/2571-631X/4/2/29)
- [Misch et al. 2022 — Modal characterization of wheelchairs (MDPI)](https://www.mdpi.com/2571-631X/5/3/25)
- [ISO 2631-1:1997 — Whole-body vibration evaluation](https://www.iso.org/standard/7612.html)
- [WheelShare / sidewalk accessibility via smartphone IMU (arXiv)](https://arxiv.org/abs/2101.03724)
- [MyPath — accessible routing for wheelchair users (RESNA)](https://www.resna.org/sites/default/files/conference/2022/PublicTransportation/80_Saha.html)
- [Smartphone pavement roughness validation (Nature Sci Rep)](https://www.nature.com/articles/s41598-025-34396-3)
- [Smartphone-measured acceleration vs IRI (IEEE Xplore)](https://ieeexplore.ieee.org/document/9345727/)
- [US Access Board — surface roughness references](https://www.access-board.gov/research/exterior-surfaces/surface-roughness-standards/references-surface-roughness/)
