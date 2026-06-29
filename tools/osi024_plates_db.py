#!/usr/bin/env python3
"""
osi024_plates_db.py — SQLite-backed pseudonymous license-plate streak
database for OSI-024 (revised 2026-06-29 for GDPR Article 89 alignment).

GDPR DESIGN PRINCIPLE (this file is the choke point):
  • The readable license-plate text NEVER reaches disk.
  • What we store is plate_hash = HMAC-SHA256(salt, normalized_plate),
    truncated to 16 bytes hex (32 chars). Same plate → same hash, so
    streak math still works; an attacker without the salt cannot recover
    plates by brute force or rainbow tables.
  • The salt lives in ~/.config/waytrace/plate_salt.bin (32 random bytes),
    gitignored. Lose the salt → lose the linkage to past sightings.
  • Retention: sightings older than RETENTION_WEEKS are purged on every
    init (data minimisation, Article 5(1)(e)).

Purpose declaration: this database is for AGGREGATE statistics
("how many cars on a stretch", "how long does the same car stay parked")
not for identifying individual vehicles or owners.

Cluster key — identity of a "same parked car" across days — is derived
from the WHEELCHAIR pose, not the car GPS:
  • chair_lat_bin = lat rounded to ~10 m
  • chair_lon_bin = lon rounded to ~10 m
  • chair_heading_bin = rotvec heading bucketed into 45° octants

Schema (created on first call to init_db):

  plates
    plate_hash         TEXT PRIMARY KEY    -- HMAC-SHA256(salt, plate)[:32]
    first_seen_date    TEXT                -- ISO date YYYY-MM-DD
    last_seen_date     TEXT
    chair_lat_bin      INTEGER             -- representative cluster
    chair_lon_bin      INTEGER
    chair_heading_bin  INTEGER             -- 0..7 octant
    ocr_conf_max       REAL
    total_pushes       INTEGER

  sightings
    plate_hash         TEXT
    push_ts            TEXT
    push_date          TEXT
    chair_lat          REAL
    chair_lon          REAL
    chair_heading_deg  REAL
    chair_lat_bin      INTEGER
    chair_lon_bin      INTEGER
    chair_heading_bin  INTEGER
    ocr_conf           REAL
    yolo_conf          REAL
    bbox_x1, y1, x2, y2 INTEGER
    UNIQUE(plate_hash, push_date, chair_lat_bin, chair_lon_bin,
           chair_heading_bin)
"""
import hashlib
import hmac
import math
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_DB = os.environ.get(
    "OSI024_DB", str(Path.home() / "waytrace-video" / "plates.db"))
DEFAULT_SALT_PATH = os.environ.get(
    "OSI024_SALT", str(Path.home() / ".config" / "waytrace" / "plate_salt.bin"))

RETENTION_WEEKS = int(os.environ.get("OSI024_RETENTION_WEEKS", "12"))

# Hash output is truncated to 16 bytes hex = 32 chars. That is 128 bits of
# pseudo-identifier — enough collision space for billions of plates,
# nothing recoverable.
HASH_HEX_LEN = 32

# ~10 m bin at Skopje latitude (≈ 42°N).
LAT_BIN_STEP_DEG = 10.0 / 111_320.0
LON_BIN_STEP_DEG = 10.0 / 82_750.0
HEADING_BIN_DEG  = 45.0


def _load_or_create_salt(salt_path=DEFAULT_SALT_PATH):
    p = Path(salt_path)
    if p.exists():
        return p.read_bytes()
    p.parent.mkdir(parents=True, exist_ok=True)
    s = secrets.token_bytes(32)
    p.write_bytes(s)
    # Make the salt file read/write owner only.
    os.chmod(p, 0o600)
    return s


def plate_hash(plate_text, salt_path=DEFAULT_SALT_PATH):
    """One-way pseudonym for a plate string. Same input → same output.
    Uppercase + strip; HMAC-SHA256 with the per-machine salt; hex; truncated."""
    salt = _load_or_create_salt(salt_path)
    norm = (plate_text or "").strip().upper().encode("utf-8")
    return hmac.new(salt, norm, hashlib.sha256).hexdigest()[:HASH_HEX_LEN]


def cluster_bins(lat, lon, heading_deg):
    lat_bin = int(math.floor(lat / LAT_BIN_STEP_DEG))
    lon_bin = int(math.floor(lon / LON_BIN_STEP_DEG))
    h = heading_deg % 360.0
    heading_bin = int(h // HEADING_BIN_DEG) % 8
    return lat_bin, lon_bin, heading_bin


def init_db(db_path=DEFAULT_DB):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS plates (
        plate_hash TEXT PRIMARY KEY,
        first_seen_date TEXT NOT NULL,
        last_seen_date  TEXT NOT NULL,
        chair_lat_bin INTEGER NOT NULL,
        chair_lon_bin INTEGER NOT NULL,
        chair_heading_bin INTEGER NOT NULL,
        ocr_conf_max REAL NOT NULL,
        total_pushes INTEGER NOT NULL DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS sightings (
        plate_hash TEXT NOT NULL,
        push_ts TEXT NOT NULL,
        push_date TEXT NOT NULL,
        chair_lat REAL NOT NULL,
        chair_lon REAL NOT NULL,
        chair_heading_deg REAL NOT NULL,
        chair_lat_bin INTEGER NOT NULL,
        chair_lon_bin INTEGER NOT NULL,
        chair_heading_bin INTEGER NOT NULL,
        ocr_conf REAL NOT NULL,
        yolo_conf REAL NOT NULL,
        bbox_x1 INTEGER, bbox_y1 INTEGER,
        bbox_x2 INTEGER, bbox_y2 INTEGER,
        UNIQUE(plate_hash, push_date, chair_lat_bin, chair_lon_bin,
               chair_heading_bin)
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_sight_plate_cluster
        ON sightings(plate_hash, chair_lat_bin, chair_lon_bin,
                     chair_heading_bin, push_date)""")
    con.commit()
    purge_old(con)
    return con


def purge_old(con, weeks=RETENTION_WEEKS):
    """Data minimisation: drop sightings older than `weeks`, and prune
    plates rows whose last_seen_date is older than the cutoff."""
    cutoff = (date.today() - timedelta(weeks=weeks)).isoformat()
    cur = con.execute("DELETE FROM sightings WHERE push_date < ?", (cutoff,))
    deleted_sightings = cur.rowcount
    cur = con.execute("""DELETE FROM plates WHERE plate_hash NOT IN
        (SELECT DISTINCT plate_hash FROM sightings)""")
    deleted_plates = cur.rowcount
    con.commit()
    return deleted_sightings, deleted_plates


def upsert_sighting(con, plate_text_or_hash, push_ts, push_date,
                    chair_lat, chair_lon, chair_heading_deg,
                    ocr_conf, yolo_conf, bbox, *, prehashed=False):
    """Insert one sighting (idempotent per UNIQUE key).

    Pass `prehashed=True` if you already have the hash (e.g. caller did
    plate_hash() once and is calling repeatedly). Otherwise we hash here.
    bbox is (x1,y1,x2,y2). Returns True if a NEW sighting was inserted."""
    ph = plate_text_or_hash if prehashed else plate_hash(plate_text_or_hash)
    lat_bin, lon_bin, head_bin = cluster_bins(chair_lat, chair_lon,
                                              chair_heading_deg)
    cur = con.execute("""INSERT OR IGNORE INTO sightings
        (plate_hash, push_ts, push_date, chair_lat, chair_lon,
         chair_heading_deg, chair_lat_bin, chair_lon_bin, chair_heading_bin,
         ocr_conf, yolo_conf, bbox_x1, bbox_y1, bbox_x2, bbox_y2)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ph, push_ts, push_date, chair_lat, chair_lon,
         chair_heading_deg, lat_bin, lon_bin, head_bin,
         ocr_conf, yolo_conf, bbox[0], bbox[1], bbox[2], bbox[3]))
    inserted = cur.rowcount == 1

    con.execute("""INSERT INTO plates
        (plate_hash, first_seen_date, last_seen_date,
         chair_lat_bin, chair_lon_bin, chair_heading_bin,
         ocr_conf_max, total_pushes)
        VALUES (?,?,?,?,?,?,?,1)
        ON CONFLICT(plate_hash) DO UPDATE SET
          last_seen_date    = MAX(last_seen_date, excluded.last_seen_date),
          chair_lat_bin     = excluded.chair_lat_bin,
          chair_lon_bin     = excluded.chair_lon_bin,
          chair_heading_bin = excluded.chair_heading_bin,
          ocr_conf_max      = MAX(ocr_conf_max, excluded.ocr_conf_max),
          total_pushes      = (
              SELECT COUNT(DISTINCT push_ts) FROM sightings
              WHERE plate_hash = excluded.plate_hash)
        """,
        (ph, push_date, push_date,
         lat_bin, lon_bin, head_bin, ocr_conf))
    con.commit()
    return inserted


def get_daily_streak(con, plate_hash_or_text, cluster, as_of_date,
                     prehashed=False):
    if isinstance(as_of_date, str):
        as_of_date = date.fromisoformat(as_of_date)
    ph = plate_hash_or_text if prehashed else plate_hash(plate_hash_or_text)
    lat_bin, lon_bin, head_bin = cluster
    rows = con.execute("""SELECT DISTINCT push_date FROM sightings
        WHERE plate_hash = ? AND chair_lat_bin = ? AND chair_lon_bin = ?
              AND chair_heading_bin = ? AND push_date <= ?
        ORDER BY push_date DESC""",
        (ph, lat_bin, lon_bin, head_bin,
         as_of_date.isoformat())).fetchall()
    if not rows:
        return 0
    streak = 0
    expect = as_of_date
    for (d,) in rows:
        d = date.fromisoformat(d)
        if d == expect:
            streak += 1
            expect = expect - timedelta(days=1)
        elif d < expect:
            break
    return streak


def get_weekly_streak(con, plate_hash_or_text, cluster, as_of_date,
                      prehashed=False):
    if isinstance(as_of_date, str):
        as_of_date = date.fromisoformat(as_of_date)
    ph = plate_hash_or_text if prehashed else plate_hash(plate_hash_or_text)
    lat_bin, lon_bin, head_bin = cluster
    rows = con.execute("""SELECT DISTINCT push_date FROM sightings
        WHERE plate_hash = ? AND chair_lat_bin = ? AND chair_lon_bin = ?
              AND chair_heading_bin = ? AND push_date <= ?""",
        (ph, lat_bin, lon_bin, head_bin,
         as_of_date.isoformat())).fetchall()
    if not rows:
        return 0
    weeks = set()
    for (d,) in rows:
        d = date.fromisoformat(d)
        iso = d.isocalendar()
        weeks.add((iso[0], iso[1]))
    target_iso = as_of_date.isocalendar()
    cur = (target_iso[0], target_iso[1])
    streak = 0
    while cur in weeks:
        streak += 1
        wk_year, wk_num = cur
        any_day = date.fromisocalendar(wk_year, wk_num, 1)
        prev = any_day - timedelta(days=7)
        prev_iso = prev.isocalendar()
        cur = (prev_iso[0], prev_iso[1])
    return streak


def format_streak_label(cls, daily_streak, weekly_streak, ocr_conf):
    """OSI-024 label rendering rule.
       Returns e.g. "car · 12d 2w · 92%".
    NOTE: no plate identifier of any kind in the rendered label (GDPR)."""
    pct = int(round(ocr_conf * 100))
    return f"{cls} · {daily_streak}d {weekly_streak}w · {pct}%"


if __name__ == "__main__":
    con = init_db()
    n_pl = con.execute("SELECT COUNT(*) FROM plates").fetchone()[0]
    n_si = con.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    print(f"db: {DEFAULT_DB}  plates={n_pl}  sightings={n_si}  "
          f"retention={RETENTION_WEEKS}wk  salt={DEFAULT_SALT_PATH}")
