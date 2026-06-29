#!/usr/bin/env python3
"""
osi024_migrate_to_hash.py — one-shot migration of the OLD plates.db schema
(`plate_text` column, readable plates on disk) to the new salted-hash
schema (`plate_hash` column, no readable plates on disk).

Strategy:
  1. Backup the DB file to plates.db.bak.<ts>
  2. UPDATE every row to overwrite plate_text with HMAC-SHA256(salt, text).
     Same uniqueness, same streak math.
  3. ALTER TABLE RENAME COLUMN plate_text → plate_hash on both tables.

Idempotent: detects already-migrated DB (column is already `plate_hash`)
and exits cleanly.
"""
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import osi024_plates_db as pdb  # noqa: E402


def column_names(con, table):
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def main(db_path=pdb.DEFAULT_DB):
    if not Path(db_path).exists():
        sys.exit(f"no DB at {db_path}")

    con = sqlite3.connect(db_path)
    pl_cols = column_names(con, "plates")
    si_cols = column_names(con, "sightings")

    if "plate_hash" in pl_cols and "plate_text" not in pl_cols:
        print(f"already migrated: {db_path}")
        return

    if "plate_text" not in pl_cols:
        sys.exit(f"unexpected schema on plates: {pl_cols}")

    # 1) backup
    bak = f"{db_path}.bak.{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, bak)
    print(f"backup: {bak}")

    # 2) overwrite text with hash in place
    pl_rows = con.execute(
        "SELECT rowid, plate_text FROM plates").fetchall()
    print(f"hashing {len(pl_rows)} plate rows ...")
    for rowid, txt in pl_rows:
        h = pdb.plate_hash(txt)
        con.execute("UPDATE plates SET plate_text = ? WHERE rowid = ?",
                    (h, rowid))

    si_rows = con.execute(
        "SELECT rowid, plate_text FROM sightings").fetchall()
    print(f"hashing {len(si_rows)} sighting rows ...")
    for rowid, txt in si_rows:
        h = pdb.plate_hash(txt)
        con.execute("UPDATE sightings SET plate_text = ? WHERE rowid = ?",
                    (h, rowid))
    con.commit()

    # 3) rename column. Requires SQLite ≥ 3.25 (we have it).
    print("renaming columns ...")
    con.execute("ALTER TABLE plates    RENAME COLUMN plate_text TO plate_hash")
    con.execute("ALTER TABLE sightings RENAME COLUMN plate_text TO plate_hash")

    # Rebuild the index under its new name (the old one may still point at
    # plate_text; SQLite tracks the renamed column, but be explicit).
    con.execute("DROP INDEX IF EXISTS idx_sight_plate_cluster")
    con.execute("""CREATE INDEX idx_sight_plate_cluster
        ON sightings(plate_hash, chair_lat_bin, chair_lon_bin,
                     chair_heading_bin, push_date)""")
    con.commit()

    n_pl = con.execute("SELECT COUNT(*) FROM plates").fetchone()[0]
    n_si = con.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
    print(f"done. plates={n_pl}  sightings={n_si}  no readable plates remain.")


if __name__ == "__main__":
    main()
