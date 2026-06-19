#!/usr/bin/env python3
"""Upload a finished OSI-007 RW-*-final.mp4 to YouTube.

Defaults to "unlisted" so a video is shareable by link but not publicly
discoverable. Auto-builds the description from the osi007_final.py
JSON sidecar (object counts) plus optional ART analysis line.

ONE-TIME SETUP (do this once before the first upload):
    1) https://console.cloud.google.com/ → create a project
    2) APIs & Services → enable "YouTube Data API v3"
    3) APIs & Services → Credentials → "Create credentials" → OAuth 2.0
       client ID → application type "Desktop app". Download the JSON.
    4) Save it to ~/.config/waytrace/youtube_client_secret.json
    5) Run this script with --interactive once on a machine with a
       browser. It opens a browser, you sign in with the channel
       account, and a refresh token is written to
       ~/.config/waytrace/youtube_token.json. After that, headless
       uploads just work — Step 4 of the Phase-2 batch can call this
       without any user interaction.

DEPENDENCIES (in the osi007 conda env):
    pip install google-auth google-auth-oauthlib google-api-python-client

USAGE FROM THE BATCH:
    python youtube_upload.py
        --video        RW-…-final.mp4
        --title        "Rear Window Push 2026-06-18"
        --privacy      unlisted
        --json-counts  20240101_201141_consolidated.json  # optional
        --extra-line   "ISO 8608: E, VDV 43.4 (HIGH)"     # optional
"""
import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

CONFIG_DIR     = Path.home() / ".config/waytrace"
CLIENT_SECRET  = CONFIG_DIR / "youtube_client_secret.json"
TOKEN_FILE     = CONFIG_DIR / "youtube_token.json"
SCOPES         = ["https://www.googleapis.com/auth/youtube.upload"]
CATEGORY_ID    = "29"          # Nonprofits & Activism (closest fit)
DEFAULT_PRIVACY = "unlisted"   # never "public" by default


def lazy_imports():
    """Import Google libraries only when actually uploading, so the
    script can be inspected on machines without them."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        return (Credentials, InstalledAppFlow, Request, build, MediaFileUpload)
    except ImportError as e:
        sys.exit("YouTube upload deps missing — in the osi007 conda env:\n"
                 "  pip install google-auth google-auth-oauthlib "
                 "google-api-python-client\n"
                 f"(import error: {e})")


def authorize(interactive: bool):
    Credentials, InstalledAppFlow, Request, _, _ = lazy_imports()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # Refresh if expired and we have a refresh token.
    if creds and not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json())

    if not creds or not creds.valid:
        if not interactive:
            sys.exit(f"no valid token at {TOKEN_FILE}; "
                     f"re-run with --interactive once on a desktop to grant access.")
        if not CLIENT_SECRET.exists():
            sys.exit(f"missing {CLIENT_SECRET}. See ONE-TIME SETUP "
                     "in the script docstring.")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
        print(f"saved refresh token to {TOKEN_FILE}")
    return creds


def build_description(args) -> str:
    lines = [
        "Open Streets Initiative — Rear Window project.",
        "Wheelchair-mounted dashcam recording with WayTrace sensor analysis.",
        "https://github.com/vasil/open-streets-initiative",
        "",
        f"Recorded: {args.recorded or datetime.now().date()}",
    ]
    if args.extra_line:
        lines.append(args.extra_line)

    if args.json_counts:
        try:
            j = json.loads(Path(args.json_counts).read_text())
            counts_lines = [""]
            counts_lines.append("Object counts (YOLO):")
            for k in ("vehicles", "cyclists", "persons",
                      "small_obstacles"):
                if k in j:
                    counts_lines.append(f"  {k:>16}: {j[k]}")
            counts_lines.append("Privacy blur:")
            for k in ("plates_blurred", "faces_blurred"):
                if k in j:
                    counts_lines.append(f"  {k:>16}: {j[k]}")
            lines.extend(counts_lines)
        except Exception as e:
            lines.append(f"(counts sidecar unreadable: {e})")

    lines.extend([
        "",
        "Faces and license plates auto-blurred via YOLOv8 detectors.",
        "All event detection is offline (Wk-weighted RMS, VDV, ISO 8608, "
        "RFC) in waytrace_analysis.py.",
    ])
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default=None,
                    help="Override the auto-built description.")
    ap.add_argument("--privacy", choices=("private", "unlisted", "public"),
                    default=DEFAULT_PRIVACY)
    ap.add_argument("--json-counts", type=Path, default=None,
                    help="osi007_final.py JSON sidecar (vehicles, faces, etc).")
    ap.add_argument("--extra-line", default=None,
                    help="Extra line for the description (e.g. ART summary).")
    ap.add_argument("--recorded", default=None,
                    help="Recording date YYYY-MM-DD (for the description).")
    ap.add_argument("--interactive", action="store_true",
                    help="Run the OAuth browser flow (first-time setup).")
    args = ap.parse_args()

    if not args.video.exists():
        sys.exit(f"video not found: {args.video}")

    creds = authorize(args.interactive)
    _, _, _, build, MediaFileUpload = lazy_imports()

    youtube = build("youtube", "v3", credentials=creds)

    description = args.description or build_description(args)
    body = {
        "snippet": {
            "title":       args.title[:100],   # YouTube cap
            "description": description[:5000],
            "tags":        ["wheelchair", "accessibility", "OpenStreetMap",
                            "OSI", "WayTrace", "Rear Window"],
            "categoryId":  CATEGORY_ID,
        },
        "status": {
            "privacyStatus":         args.privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(args.video), resumable=True,
                            chunksize=8 * 1024 * 1024)
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media)

    print(f"uploading {args.video.name}  ({args.video.stat().st_size/1e6:.1f} MB)"
          f"  privacy={args.privacy}")
    response = None
    last_pct = -1
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                if pct != last_pct:
                    print(f"  {pct:3d}%", flush=True)
                    last_pct = pct
        except Exception as e:
            sys.exit(f"upload failed: {e}")

    vid = response.get("id", "?")
    url = f"https://youtu.be/{vid}"
    print(f"  uploaded → {url}")
    print(url)   # last stdout line = URL for shell to capture


if __name__ == "__main__":
    main()
