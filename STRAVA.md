# Strava integration — one-time setup

WayTrace pulls the GPS track for each ride from Strava (since the phone
records only the sensor data, not GPS). You only need to do this setup
**once** per machine.

The credentials live **outside** the repo at
`~/.config/waytrace/strava.json` so they never get committed.

---

## Step 1 — Create a Strava API application (~3 minutes)

1. Open https://www.strava.com/settings/api in your browser
   (log in to Strava first if you aren't already).
2. Fill in the form:
   - **Application Name:** `WayTrace`
   - **Category:** `Data Importer`
   - **Club:** leave empty
   - **Website:** `http://localhost`
   - **Application Description:** `Personal road-quality data analysis`
   - **Authorization Callback Domain:** `localhost`
     (just the word, no `http://`, no port number)
3. Upload any small icon (Strava requires one — a screenshot works).
4. Click **Create**.

Strava now shows you two values on the app page:

- **Client ID** — a 5-or-6-digit number
- **Client Secret** — a long string of letters and digits
  (you may need to click "Show" to reveal it)

---

## Step 2 — Paste your credentials into the local file

Open `~/.config/waytrace/strava.json` in your editor and replace the two
placeholder strings:

```json
{
  "client_id": "PUT_CLIENT_ID_HERE",
  "client_secret": "PUT_CLIENT_SECRET_HERE",
  ...
}
```

becomes (with your real values, quotes kept):

```json
{
  "client_id": "12345",
  "client_secret": "abcdef0123456789abcdef0123456789abcdef01",
  ...
}
```

Leave the `access_token`, `refresh_token`, and `expires_at` fields as-is.
The next step fills them in for you.

---

## Step 3 — Authorize (browser pops open, you click one button)

```bash
cd ~/Projects/WayTrace
python3 waytrace_strava.py --auth
```

What happens:

1. The script starts a tiny web server on `http://localhost:8080`
2. Your browser opens to a Strava authorization page
3. You click **Authorize**
4. Strava redirects your browser back to `localhost:8080/?code=...`
5. The script catches that code, exchanges it for an access token plus a
   refresh token, and saves them to `~/.config/waytrace/strava.json`
6. Browser shows "OK — you can close this tab"; terminal prints "saved tokens"

You only do this once. Strava's refresh-token mechanism keeps you logged
in indefinitely; the script auto-refreshes whenever the access token
expires (every six hours).

---

## Step 4 — Use it

Fetch the most recent activity as a GPX file:

```bash
python3 waytrace_strava.py --latest
# → ~/Downloads/GPS-YYYYMMDDHHMM.gpx
```

Fetch a specific activity by ID (the long number in a Strava URL):

```bash
python3 waytrace_strava.py --activity-id 12345678901
```

---

## Then: pin road problems to actual streets

Once you have both the sensor CSV and the GPS track for the same ride:

```bash
python3 waytrace_locate.py \
    ~/Downloads/ART-YYYYMMDDHHMM.csv \
    ~/Downloads/GPS-YYYYMMDDHHMM.gpx
```

This produces, in `~/Downloads/`:

- `LOC-YYYYMMDDHHMM.png` — your route on a map, colored by road
  roughness, with numbered pins on the worst spots
- `LOC-YYYYMMDDHHMM.txt` — a ranked list of bad spots with lat/lon
  coordinates and what made each one bad

---

## Troubleshooting

**"Connection refused on localhost:8080"** — another app is using port 8080.
Kill it, or edit the `REDIRECT_PORT` constant at the top of
`waytrace_strava.py` to something else (then update the Strava app
"Authorization Callback Domain" to match if needed — but the domain
stays `localhost`, only the port changes).

**"invalid client"** — Client ID or Client Secret is wrong in
`~/.config/waytrace/strava.json`. Re-copy them from the Strava API page.

**"Authorization Error: redirect_uri does not match"** — the Strava app's
Authorization Callback Domain must be exactly `localhost` (no http://,
no port). Edit it on https://www.strava.com/settings/api.

**Tokens stopped working after a long break** — refresh tokens don't
expire under normal use, but if you revoked access on
https://www.strava.com/settings/apps just rerun `--auth`.
