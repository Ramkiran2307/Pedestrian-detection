"""SQLite schema and small helpers.

Tables
  cameras    one row per edge device (updated by heartbeats)
  incidents  one row per littering event + its enforcement / cleanup state
  evidence   files (snapshots, plate crop, clip) belonging to an incident
  audit_log  every change an officer makes, for accountability
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cameras (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    location    TEXT,
    ward        TEXT,
    zone        TEXT,
    preview_url TEXT,
    stats       TEXT,
    last_seen   TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    uid               TEXT NOT NULL UNIQUE,
    camera_id         TEXT,
    zone              TEXT,
    ward              TEXT,
    location          TEXT,
    occurred_at       TEXT NOT NULL,
    detected_at       TEXT,
    received_at       TEXT NOT NULL,
    offender_type     TEXT NOT NULL CHECK (offender_type IN ('person', 'vehicle', 'unknown')),
    offender_track    TEXT,
    attribution_score REAL,
    vehicle_class     TEXT,
    vehicle_category  TEXT,
    linked_track      TEXT,
    plate_number      TEXT,
    plate_display     TEXT,
    plate_confidence  REAL,
    plate_valid       INTEGER,
    plate_raw         TEXT,
    plate_corrected   INTEGER NOT NULL DEFAULT 0,
    plate_level       TEXT,
    plate_candidates  TEXT,
    plate_engine      TEXT,
    plate_verified    INTEGER NOT NULL DEFAULT 0,
    plate_verified_by TEXT,
    plate_verified_at TEXT,
    litter_bbox       TEXT,
    suspects          TEXT,
    status            TEXT NOT NULL DEFAULT 'pending_review',
    cleanup_status    TEXT NOT NULL DEFAULT 'pending',
    cleanup_assignee  TEXT,
    fine_amount       INTEGER,
    notice_number     TEXT UNIQUE,
    notice_issued_at  TEXT,
    due_date          TEXT,
    paid_at           TEXT,
    payment_ref       TEXT,
    rejection_reason  TEXT,
    is_sample         INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_status   ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_occurred ON incidents(occurred_at);
CREATE INDEX IF NOT EXISTS idx_incidents_plate    ON incidents(plate_number);

CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    path        TEXT NOT NULL,
    mime        TEXT,
    bytes       INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_incident ON evidence(incident_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    from_value  TEXT,
    to_value    TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_incident ON audit_log(incident_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# Columns added after the first release: added to existing databases on start-up.
MIGRATIONS = {
    "incidents": [
        ("plate_level", "TEXT"), ("plate_candidates", "TEXT"), ("plate_engine", "TEXT"),
        ("plate_verified", "INTEGER NOT NULL DEFAULT 0"), ("plate_verified_by", "TEXT"), ("plate_verified_at", "TEXT"),
    ],
}


def init_db(path: Path) -> None:
    with connect(path) as conn:
        for table, columns in MIGRATIONS.items():
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if existing:  # table exists (older database): add what is missing
                for name, decl in columns:
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        conn.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("litter_bbox", "suspects", "plate_raw", "plate_candidates", "stats"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except ValueError:
                pass
    for key in ("plate_valid", "plate_corrected", "plate_verified", "is_sample"):
        if key in d and d[key] is not None:
            d[key] = bool(d[key])
    return d
