# IMPORTANT — One-time manual setup on the Xiaomi phone

## Why this file exists

On Xiaomi / MIUI phones, the sensor framework throttles delivery from
**60 Hz down to ~2 Hz exactly 60 seconds after the screen is locked**,
even with a foreground service and a `PARTIAL_WAKE_LOCK` held.

The code in `WT-202605172217.apk` and later does everything it can to
defeat this — high-importance notification channel, immediate
foreground-service behavior, battery-optimisation exemption prompt — but
**three settings can only be changed by you, on the phone**. Until all
three are toggled, the v3 APK will still record 60 seconds of useful
data followed by 100+ minutes of garbage 2 Hz output.

Do this once per install. If you reinstall the APK, redo all three.

---

## 1. Battery → "No restrictions"

Path on MIUI 14 (and later):

> **Settings → Apps → Manage apps → WayTrace → Battery saver → No restrictions**

What it does: stops MIUI from suspending the app or killing its sensor
delivery when the screen is off.

The default ("Battery saver") is what causes the 60-second cutoff.

---

## 2. Autostart ON

Path:

> **Security app → Permissions → Autostart → WayTrace = ON**

(On some MIUI versions: **Settings → Apps → Permissions → Autostart**.)

What it does: lets the foreground service keep running after the phone
is locked or after you swipe the app away from Recents.

Without this, MIUI kills the service silently after a few minutes
regardless of any wake lock.

---

## 3. Lock the app in the Recents screen

1. Open WayTrace and start recording.
2. Press the home button (or gesture).
3. Open Recents (swipe-up-and-hold, or the three-button square key).
4. Long-press the WayTrace card.
5. Tap the **lock icon** that appears.

What it does: tells MIUI "I never want you to swipe-kill this app". The
lock icon stays on the card until you explicitly remove it.

---

## How to verify it worked

After the three steps, run a quick test push:

1. Tap START.
2. Lock the phone screen.
3. Wait 3 minutes.
4. Stop the recording.
5. Open the CSV in Downloads and check the duration:
   - if you have **~10,800 accel rows** for 3 minutes → fix worked (60 Hz × 180s = 10,800)
   - if you have **~3,800 accel rows** → still throttled. Re-check steps 1–3.

Or run on the computer:

```
python3 -c "
import pandas as pd, sys
df = pd.read_csv(sys.argv[1])
a = df[df['sensor']=='accel']
dur = (a['timestamp_ms'].max() - a['timestamp_ms'].min())/1000
print(f'duration: {dur:.0f}s, rate: {len(a)/dur:.1f} Hz')
" ~/Downloads/ART-202605xxxxxx.csv
```

Pass = 50+ Hz for the entire duration.

---

## Bonus — also useful

- **Lock screen → keep WayTrace visible.** MIUI sometimes hides
  foreground-service notifications on the lock screen. Settings →
  Notifications → Lock screen → Show all notifications.

- **Do not put the phone in "Ultra battery saver"** mode while recording.
  That mode overrides everything above.

- **First launch will show a system dialog** asking you to allow
  WayTrace to run in the background without battery optimisation. Tap
  **Allow**. The app shows this dialog on every launch until you allow
  it, because MIUI sometimes reverts the setting after an update.
