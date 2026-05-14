#!/usr/bin/env python3
"""
WayTrace Strava client — OAuth + GPX export.

Pulls the GPS track for a Strava activity, writes a standard GPX file
to ~/Downloads/GPS-YYYYMMDDHHMM.gpx so it can be aligned with an ART
sensor CSV by waytrace_locate.py.

Modes:
    --auth                       one-time browser OAuth flow
    --latest                     download the most recent activity (default)
    --activity-id N              download a specific activity

Credentials live at ~/.config/waytrace/strava.json (outside the repo).
See STRAVA.md for first-time setup.
"""

import http.server
import json
import sys
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

CRED_PATH      = Path.home() / ".config" / "waytrace" / "strava.json"
DOWNLOADS_DIR  = Path.home() / "Downloads"
REDIRECT_PORT  = 8080
REDIRECT_URI   = f"http://localhost:{REDIRECT_PORT}/"
AUTH_URL       = "https://www.strava.com/oauth/authorize"
TOKEN_URL      = "https://www.strava.com/oauth/token"
API_BASE       = "https://www.strava.com/api/v3"
SCOPES         = "read,activity:read_all"


# ── credential storage ───────────────────────────────────────────────────

def load_creds() -> dict:
    if not CRED_PATH.exists():
        sys.exit(f"Credentials file not found at {CRED_PATH}\n"
                 "Read STRAVA.md and complete Step 1+2 first.")
    return json.loads(CRED_PATH.read_text())


def save_creds(c: dict) -> None:
    CRED_PATH.write_text(json.dumps(c, indent=2) + "\n")


def need_app_creds(c: dict) -> None:
    if c.get("client_id", "").startswith("PUT_") or \
       c.get("client_secret", "").startswith("PUT_"):
        sys.exit("client_id / client_secret are still placeholders.\n"
                 f"Edit {CRED_PATH} with values from "
                 "https://www.strava.com/settings/api\n"
                 "(See STRAVA.md for screenshots.)")


# ── OAuth flow ───────────────────────────────────────────────────────────

class _CodeCatcher(http.server.BaseHTTPRequestHandler):
    """Catches the ?code=... redirect from Strava and stashes it on the server."""
    def do_GET(self):
        q = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(q)
        if "code" in params:
            self.server.received_code = params["code"][0]
            body = b"<html><body><h2>OK \xe2\x80\x94 you can close this tab.</h2></body></html>"
        elif "error" in params:
            self.server.received_error = params["error"][0]
            body = f"<html><body><h2>Authorization error: {params['error'][0]}</h2></body></html>".encode()
        else:
            body = b"<html><body>Waiting for Strava redirect...</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass  # silence default access log


def do_auth() -> None:
    creds = load_creds()
    need_app_creds(creds)

    url = (
        f"{AUTH_URL}?client_id={creds['client_id']}"
        f"&response_type=code"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
        f"&approval_prompt=force"
        f"&scope={SCOPES}"
    )
    print("Opening browser to authorize WayTrace on Strava...")
    print(f"   if it doesn't open automatically, paste this URL:\n   {url}\n")
    webbrowser.open(url)

    httpd = http.server.HTTPServer(("localhost", REDIRECT_PORT), _CodeCatcher)
    httpd.received_code  = None
    httpd.received_error = None
    print(f"Listening on {REDIRECT_URI} for the redirect ...")
    while httpd.received_code is None and httpd.received_error is None:
        httpd.handle_request()
    httpd.server_close()

    if httpd.received_error:
        sys.exit(f"Authorization denied: {httpd.received_error}")

    print("Got authorization code, exchanging for tokens ...")
    r = requests.post(TOKEN_URL, data={
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
        "code":          httpd.received_code,
        "grant_type":    "authorization_code",
    }, timeout=30)
    r.raise_for_status()
    tok = r.json()

    creds["access_token"]  = tok["access_token"]
    creds["refresh_token"] = tok["refresh_token"]
    creds["expires_at"]    = tok["expires_at"]
    save_creds(creds)
    expiry = datetime.fromtimestamp(tok["expires_at"]).strftime("%Y-%m-%d %H:%M:%S")
    athlete = tok.get("athlete", {})
    name = f"{athlete.get('firstname','')} {athlete.get('lastname','')}".strip() or "(unknown)"
    print(f"Saved tokens for athlete: {name}")
    print(f"   access token valid until {expiry}")
    print(f"   credentials saved to {CRED_PATH}")


# ── token refresh ────────────────────────────────────────────────────────

def get_access_token() -> str:
    creds = load_creds()
    need_app_creds(creds)
    if not creds.get("refresh_token"):
        sys.exit("No refresh_token yet. Run: python3 waytrace_strava.py --auth")
    if time.time() < creds.get("expires_at", 0) - 60:
        return creds["access_token"]
    print("Refreshing Strava access token ...")
    r = requests.post(TOKEN_URL, data={
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type":    "refresh_token",
    }, timeout=30)
    r.raise_for_status()
    tok = r.json()
    creds["access_token"]  = tok["access_token"]
    creds["refresh_token"] = tok["refresh_token"]
    creds["expires_at"]    = tok["expires_at"]
    save_creds(creds)
    return creds["access_token"]


# ── activity + streams → GPX ─────────────────────────────────────────────

def fetch_latest_activity_id(token: str) -> int:
    r = requests.get(
        f"{API_BASE}/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"per_page": 1},
        timeout=30,
    )
    r.raise_for_status()
    arr = r.json()
    if not arr:
        sys.exit("No activities found on this Strava account.")
    return arr[0]["id"]


def fetch_activity(token: str, activity_id: int) -> dict:
    r = requests.get(
        f"{API_BASE}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def fetch_streams(token: str, activity_id: int) -> dict:
    r = requests.get(
        f"{API_BASE}/activities/{activity_id}/streams",
        headers={"Authorization": f"Bearer {token}"},
        params={"keys": "time,latlng,altitude", "key_by_type": "true"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def streams_to_gpx(activity: dict, streams: dict, out_path: Path) -> int:
    if "latlng" not in streams or "time" not in streams:
        sys.exit(f"Activity {activity['id']} has no GPS streams "
                 "(was it recorded without GPS, e.g. on an indoor trainer?).")
    latlng = streams["latlng"]["data"]
    times  = streams["time"]["data"]
    ele    = streams.get("altitude", {}).get("data") or [None] * len(latlng)

    start_iso = activity["start_date"]                 # UTC ISO
    start_dt  = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    name = activity.get("name", f"Activity {activity['id']}").replace("&", "&amp;").replace("<", "&lt;")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="WayTrace" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        '  <metadata>',
        f'    <name>{name}</name>',
        f'    <time>{start_iso}</time>',
        '  </metadata>',
        '  <trk>',
        f'    <name>{name}</name>',
        '    <trkseg>',
    ]
    for (lat, lon), t_off, alt in zip(latlng, times, ele):
        ts = (start_dt + timedelta(seconds=int(t_off))).strftime("%Y-%m-%dT%H:%M:%SZ")
        if alt is None:
            lines.append(f'      <trkpt lat="{lat:.7f}" lon="{lon:.7f}"><time>{ts}</time></trkpt>')
        else:
            lines.append(
                f'      <trkpt lat="{lat:.7f}" lon="{lon:.7f}">'
                f'<ele>{alt:.2f}</ele><time>{ts}</time></trkpt>'
            )
    lines += ['    </trkseg>', '  </trk>', '</gpx>', '']
    out_path.write_text("\n".join(lines))
    return len(latlng)


def do_fetch(activity_id: int | None) -> None:
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    token = get_access_token()
    if activity_id is None:
        activity_id = fetch_latest_activity_id(token)
        print(f"Latest activity id: {activity_id}")
    print(f"Fetching activity {activity_id} metadata + streams ...")
    activity = fetch_activity(token, activity_id)
    streams  = fetch_streams(token, activity_id)

    # File name uses the local start time of the activity, to match
    # the ART file naming convention (which is also phone-local time).
    start_local_iso = activity["start_date_local"]    # 'YYYY-MM-DDTHH:MM:SSZ' (no offset, but it's local)
    local_dt = datetime.strptime(start_local_iso, "%Y-%m-%dT%H:%M:%SZ")
    fname = f"GPS-{local_dt.strftime('%Y%m%d%H%M')}.gpx"
    out_path = DOWNLOADS_DIR / fname

    n = streams_to_gpx(activity, streams, out_path)
    dist_km   = activity.get("distance", 0) / 1000.0
    moving_s  = activity.get("moving_time", 0)
    print(f"Wrote {n} GPS points  |  {dist_km:.2f} km  |  {moving_s//60}m{moving_s%60:02d}s moving")
    print(f"Output: {out_path}")
    print(f"\nNext step:")
    print(f"  python3 waytrace_locate.py <ART-*.csv> {out_path}")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args or args == ["--latest"]:
        do_fetch(None)
        return
    if args == ["--auth"]:
        do_auth()
        return
    if len(args) == 2 and args[0] == "--activity-id":
        do_fetch(int(args[1]))
        return
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    main()
