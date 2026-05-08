#!/usr/bin/env python3
"""
waytrace_fetch.py
Open Streets Initiative — Akaso V50X video fetch via WiFi
Connects to camera WiFi, downloads latest video, reconnects to home WiFi.

Usage: python waytrace_fetch.py

Camera WiFi: AKASO_V50X_B-A5D6
Password: 1234567890
Camera IP: 192.168.42.1
"""

import os
import sys
import time
import subprocess
import requests
from datetime import datetime
from pathlib import Path
import platform
import re

# --- CONFIGURATION ---
AKASO_SSID = "AKASO_V50X_B-A5D6"
AKASO_PASSWORD = "1234567890"
AKASO_IP = "192.168.42.1"
AKASO_BASE_URL = f"http://{AKASO_IP}"
OUTPUT_DIR = Path.home() / "OpenStreets" / "sessions"

OS = platform.system()

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_current_wifi():
    if OS == "Linux":
        result = subprocess.run(
            ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if line.startswith("yes:"):
                return line.split(":", 1)[1].strip()
    elif OS == "Windows":
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if "SSID" in line and "BSSID" not in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    return parts[1].strip()
    return None

def connect_wifi(ssid, password=None):
    log(f"Connecting to WiFi: {ssid}")
    if OS == "Linux":
        if password:
            cmd = ["nmcli", "dev", "wifi", "connect", ssid, "password", password]
        else:
            cmd = ["nmcli", "con", "up", ssid]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
    elif OS == "Windows":
        result = subprocess.run(
            ["netsh", "wlan", "connect", f"name={ssid}"],
            capture_output=True, text=True
        )
        return result.returncode == 0
    return False

def wait_for_camera(timeout=20):
    log(f"Waiting for camera at {AKASO_IP}...")
    for i in range(timeout):
        try:
            r = requests.get(AKASO_BASE_URL, timeout=2)
            if r.status_code == 200:
                log("Camera is reachable.")
                return True
        except:
            pass
        time.sleep(1)
        print(f"\r  Trying... {i+1}/{timeout}", end="")
    print()
    return False

def get_video_files():
    """Try multiple endpoints to find video files on camera."""
    endpoints = [
        "/DCIM/100AKASO/",
        "/DCIM/",
        "/?action=getfilelist",
    ]
    for endpoint in endpoints:
        try:
            r = requests.get(f"{AKASO_BASE_URL}{endpoint}", timeout=5)
            if r.status_code == 200 and r.text:
                # Find all MP4 files in response
                files = re.findall(r'[\w]+\.(?:MP4|MOV)', r.text, re.IGNORECASE)
                if files:
                    log(f"Found {len(files)} video file(s) at {endpoint}")
                    return files, endpoint
        except:
            pass
    return [], ""

def download_video(filename, endpoint, output_dir):
    today = datetime.now().strftime("%Y%m%d")
    save_dir = output_dir / today
    save_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    output_file = save_dir / f"RW-{timestamp}.mp4"

    url = f"{AKASO_BASE_URL}{endpoint}{filename}"
    log(f"Downloading: {filename}")
    log(f"From: {url}")

    try:
        r = requests.get(url, stream=True, timeout=120)
        total = int(r.headers.get('content-length', 0))
        downloaded = 0
        with open(output_file, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    mb = downloaded / 1024 / 1024
                    print(f"\r  {pct:.1f}% ({mb:.1f} MB)", end="")
        print()
        size_mb = output_file.stat().st_size / 1024 / 1024
        log(f"Saved: {output_file} ({size_mb:.1f} MB)")
        return output_file
    except Exception as e:
        log(f"Download error: {e}")
        return None

def main():
    log("=== WayTrace Video Fetch ===")
    log(f"Camera SSID: {AKASO_SSID}")
    log(f"OS: {OS}")

    # Save current WiFi to reconnect later
    home_wifi = get_current_wifi()
    if home_wifi:
        log(f"Home WiFi: {home_wifi}")

    # Connect to camera
    connected = connect_wifi(AKASO_SSID, AKASO_PASSWORD)
    if not connected:
        log("ERROR: Could not connect to camera WiFi.")
        log(f"Make sure camera is on and WiFi is enabled (press UP button).")
        log(f"Network name should be: {AKASO_SSID}")
        sys.exit(1)

    time.sleep(4)

    # Wait for camera to respond
    if not wait_for_camera():
        log("ERROR: Camera not responding at 192.168.42.1")
        if home_wifi:
            connect_wifi(home_wifi)
        sys.exit(1)

    # Find video files
    files, endpoint = get_video_files()

    if not files:
        log("Could not find video files automatically.")
        log("Try opening http://192.168.42.1 in your browser while connected")
        log("to see the camera file structure.")
        if home_wifi:
            connect_wifi(home_wifi)
        sys.exit(1)

    # Download the latest file
    latest = sorted(files)[-1]
    output_file = download_video(latest, endpoint, OUTPUT_DIR)

    # Reconnect home WiFi
    if home_wifi:
        log(f"Reconnecting to: {home_wifi}")
        connect_wifi(home_wifi)
        time.sleep(3)
        current = get_current_wifi()
        if current == home_wifi:
            log("Home WiFi restored.")
        else:
            log(f"Please reconnect to {home_wifi} manually.")

    # Summary
    if output_file:
        log("=== DONE ===")
        log(f"Video: {output_file}")
        log("Next: run waytrace_video.py to blur faces and process video.")
    else:
        log("=== FAILED ===")
        log("Could not download video. Check camera connection and try again.")

if __name__ == "__main__":
    main()
