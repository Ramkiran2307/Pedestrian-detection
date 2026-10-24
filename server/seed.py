"""Fill the database with clearly-marked SAMPLE incidents so the dashboard charts
have something to show during a presentation. Sample rows use district code 00
(never issued by any RTO) so they can't match a real vehicle, and they can be
removed again at any time.

    python -m server.seed --count 60      # add sample data
    python -m server.seed --purge         # remove all sample data
"""
from __future__ import annotations

import argparse
import json
import random
import string
from datetime import datetime, timedelta, timezone

from .db import connect, init_db, now_iso
from .settings import Settings
from .workflow import fine_for, pretty_plate

STATES = ["KA", "MH", "TN", "DL", "TS", "KL", "GJ", "UP"]


def sample_plate(rng: random.Random) -> str:
    return f"{rng.choice(STATES)}00{''.join(rng.choices(string.ascii_uppercase, k=2))}{rng.randint(1000, 9999)}"


def seed_if_empty(s: Settings, count: int) -> int:
    """Add sample incidents only when there are no incidents yet (LW_SEED_SAMPLE on a fresh server)."""
    init_db(s.db_path)
    conn = connect(s.db_path)
    try:
        if conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]:
            return 0
        add_samples(conn, s, count, 42)
        return count
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    s = Settings()
    init_db(s.db_path)
    conn = connect(s.db_path)
    if args.purge:
        n = conn.execute("DELETE FROM incidents WHERE is_sample = 1").rowcount
        conn.execute("DELETE FROM cameras WHERE id LIKE 'SAMPLE-%'")
        conn.commit()
        print(f"removed {n} sample incidents")
        return
    add_samples(conn, s, args.count, args.seed)
    print(f"added {args.count} sample incidents (remove with --purge)")


def add_samples(conn, s: Settings, count: int, seed: int) -> None:
    rng = random.Random(seed)
    cams = [("SAMPLE-CAM-1", "Sample: Market road", "Ward 12"), ("SAMPLE-CAM-2", "Sample: Bus stand", "Ward 7")]
    for cid, name, ward in cams:
        conn.execute("INSERT OR IGNORE INTO cameras (id, name, location, ward, zone, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (cid, name, name.split(": ")[1], ward, "Clean Zone A", now_iso()))
    repeaters = [sample_plate(rng) for _ in range(3)]
    now = datetime.now(timezone.utc)
    for i in range(count):
        occurred = now - timedelta(days=rng.random() ** 1.3 * 14, hours=0)
        hour = rng.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 4, 6, 8, 7, 6, 6, 7, 6, 5, 6, 7, 9, 10, 9, 6, 4, 2, 1])[0]
        occurred = occurred.replace(hour=(hour - 5) % 24, minute=rng.randint(0, 59))  # IST hour -> UTC
        if occurred > now:
            occurred -= timedelta(days=1)
        kind = rng.choices(["person", "vehicle", "unknown"], weights=[5, 4, 1])[0]
        cls, cat, plate = None, None, None
        if kind == "vehicle":
            cls = rng.choices(["motorcycle", "car", "truck", "bus"], weights=[5, 4, 1, 1])[0]
            cat = {"motorcycle": "two_wheeler", "car": "car"}.get(cls, "heavy")
            plate = rng.choice(repeaters) if rng.random() < 0.3 else sample_plate(rng)
        age_days = (now - occurred).days
        status = "pending_review"
        if age_days >= 1:
            status = rng.choices(["verified", "notice_issued", "paid", "rejected", "escalated"],
                                 weights=[2, 4, 5, 2, 1 if age_days > 7 else 0])[0]
        if kind == "unknown" and status in ("notice_issued", "paid", "escalated"):
            status = "verified"
        cam = rng.choice(cams)
        inc = {"offender_type": kind, "vehicle_category": cat}
        fine = fine_for(s, inc) if status in ("notice_issued", "paid", "escalated") else None
        issued = occurred + timedelta(hours=rng.randint(2, 30)) if fine is not None else None
        cur = conn.execute(
            """INSERT INTO incidents (uid, camera_id, zone, ward, location, occurred_at, detected_at, received_at,
                 offender_type, offender_track, attribution_score, vehicle_class, vehicle_category, plate_number,
                 plate_display, plate_confidence, plate_valid, plate_raw, litter_bbox, suspects, status, cleanup_status,
                 fine_amount, notice_number, notice_issued_at, due_date, paid_at, rejection_reason, is_sample, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (f"SAMPLE-{i:04d}-{rng.randint(0, 99999):05d}", cam[0], "Clean Zone A", cam[2], cam[1].split(": ")[1],
             occurred.isoformat(), occurred.isoformat(), occurred.isoformat(), kind,
             f"{'V' if kind == 'vehicle' else 'P'}{rng.randint(1, 90)}" if kind != "unknown" else None,
             round(rng.uniform(0.55, 0.98), 2), cls, cat, plate, pretty_plate(plate),
             round(rng.uniform(0.6, 0.99), 2) if plate else None, 1 if plate else None, json.dumps([]),
             json.dumps([600, 650, 630, 672]), json.dumps([]), status,
             rng.choices(["cleaned", "assigned", "pending"], weights=[6, 2, 2])[0] if age_days >= 1 else "pending",
             fine, f"SWM/SAMPLE/{occurred.year}/{i + 1:05d}" if fine is not None else None,
             issued.isoformat() if issued else None,
             (issued + timedelta(days=s.notice_due_days)).isoformat() if issued else None,
             (issued + timedelta(days=rng.randint(1, 10))).isoformat() if status == "paid" else None,
             "Sample: shadow / false trigger" if status == "rejected" else None, now_iso()))
        conn.execute("INSERT INTO audit_log (incident_id, at, actor, action, to_value, note) VALUES (?, ?, ?, ?, ?, ?)",
                     (cur.lastrowid, occurred.isoformat(), f"Camera {cam[0]}", "detected", "pending_review",
                      "Sample record generated for demonstration"))
    # sample plates: those that already got a notice were verified by an officer
    conn.execute("UPDATE incidents SET plate_level = 'high', plate_verified = 1, plate_verified_by = 'Sample officer', "
                 "plate_verified_at = notice_issued_at WHERE is_sample = 1 AND offender_type = 'vehicle' "
                 "AND status IN ('notice_issued', 'paid', 'escalated')")
    conn.execute("UPDATE incidents SET plate_level = 'medium' WHERE is_sample = 1 AND offender_type = 'vehicle' "
                 "AND plate_level IS NULL")
    conn.commit()


if __name__ == "__main__":
    main()
