"""Litter-watch server: REST API + SQLite + serves the React dashboard.

    python -m server.app                 # http://127.0.0.1:5000
    HOST=0.0.0.0 python -m server.app    # reachable from the Raspberry Pi

Edge device endpoints (need header X-API-Key):
  POST /api/v1/incidents                    multipart: metadata (JSON) + evidence files
  POST /api/v1/cameras/<id>/heartbeat       JSON status
Dashboard endpoints:
  GET  /api/v1/incidents                    list + filters + pagination
  GET  /api/v1/incidents/<id>               detail incl. evidence and audit trail
  POST /api/v1/incidents/<id>/actions       verify / reject / issue_notice / ...
  GET  /api/v1/stats                        numbers for the overview page
  GET  /api/v1/cameras                      edge devices and whether they are online
  GET  /api/v1/settings                     municipality name, fine schedule
  GET  /media/<uid>/<file>                  evidence files
"""
from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, abort, g, jsonify, request, send_from_directory

from .db import connect, init_db, now_iso, row_to_dict
from .settings import Settings
from .workflow import STATUSES, WorkflowError, apply_action, fine_for, pretty_plate

log = logging.getLogger("litterwatch.server")

EVIDENCE_KINDS = {"scene", "litter", "person", "vehicle", "plate", "clip"}
EVIDENCE_EXT = {".jpg", ".jpeg", ".png", ".mp4", ".webm"}
UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
CAMERA_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    tz = ZoneInfo(settings.timezone)

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_upload_mb * 1024 * 1024
    app.config["SETTINGS"] = settings

    # ------------------------------------------------------------------ helpers
    def db():
        if "db" not in g:
            g.db = connect(settings.db_path)
        return g.db

    @app.teardown_appcontext
    def _close(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def require_key():
        given = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(given.encode(), settings.api_key.encode()):
            abort(401, description="Missing or wrong X-API-Key")

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    def _json_error(err):
        if request.path.startswith("/api/"):
            return jsonify(error=getattr(err, "description", str(err))), err.code
        return err

    def evidence_map(incident_ids: list[int]) -> dict[int, dict[str, str]]:
        if not incident_ids:
            return {}
        marks = ",".join("?" * len(incident_ids))
        out: dict[int, dict[str, str]] = defaultdict(dict)
        for row in db().execute(f"SELECT incident_id, kind, path FROM evidence WHERE incident_id IN ({marks})",
                                incident_ids):
            out[row["incident_id"]][row["kind"]] = f"/media/{row['path']}"
        return out

    def serialize(row, evidence: dict[str, str]) -> dict:
        d = row_to_dict(row)
        d["evidence"] = evidence
        d["thumbnail"] = evidence.get("scene") or evidence.get("person") or evidence.get("vehicle")
        due = parse_time(d.get("due_date"))
        d["overdue"] = bool(d["status"] == "notice_issued" and due and due < datetime.now(timezone.utc))
        d["suggested_fine"] = fine_for(settings, d)
        return d

    def get_incident(incident_id: int):
        row = db().execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            abort(404, description="Incident not found")
        return row

    # ------------------------------------------------------------- edge → server
    @app.post("/api/v1/incidents")
    def create_incident():
        require_key()
        try:
            meta = json.loads(request.form.get("metadata", ""))
        except ValueError:
            abort(400, description="'metadata' must be a JSON string")
        if not isinstance(meta, dict):
            abort(400, description="'metadata' must be a JSON object")
        uid = str(meta.get("uid", ""))
        if not UID_RE.match(uid):
            abort(400, description="invalid uid")
        occurred = parse_time(meta.get("occurred_at"))
        if occurred is None:
            abort(400, description="occurred_at must be an ISO-8601 timestamp")
        offender_type = meta.get("offender_type", "unknown")
        if offender_type not in ("person", "vehicle", "unknown"):
            abort(400, description="offender_type must be person, vehicle or unknown")
        cam = meta.get("camera") or {}
        conn = db()
        existing = conn.execute("SELECT id FROM incidents WHERE uid = ?", (uid,)).fetchone()
        if existing:  # the edge retried after a timeout: already stored
            return jsonify(id=existing["id"], uid=uid, duplicate=True), 200

        plate = meta.get("plate") or {}
        plate_text = plate.get("text") or None   # kept even if not a valid format: officers can correct it
        now = now_iso()
        cur = conn.execute(
            """INSERT INTO incidents (uid, camera_id, zone, ward, location, occurred_at, detected_at, received_at,
                   offender_type, offender_track, attribution_score, vehicle_class, vehicle_category, linked_track,
                   plate_number, plate_display, plate_confidence, plate_valid, plate_raw, litter_bbox, suspects,
                   updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, cam.get("id"), meta.get("zone"), cam.get("ward"), cam.get("location"),
             occurred.isoformat(), (parse_time(meta.get("detected_at")) or occurred).isoformat(), now,
             offender_type, meta.get("offender_track"), meta.get("attribution_score"),
             meta.get("vehicle_class"), meta.get("vehicle_category"), meta.get("linked_track"),
             plate_text, plate.get("pretty") or pretty_plate(plate_text), plate.get("confidence"),
             int(bool(plate.get("valid"))) if plate else None, json.dumps(plate.get("raw") or []),
             json.dumps(meta.get("litter_bbox")), json.dumps(meta.get("suspects") or []), now))
        incident_id = cur.lastrowid

        folder = settings.media_dir / uid
        folder.mkdir(parents=True, exist_ok=True)
        for kind, storage in request.files.items():
            ext = Path(storage.filename or "").suffix.lower()
            if kind not in EVIDENCE_KINDS or ext not in EVIDENCE_EXT:
                continue
            name = f"{kind}{ext}"
            storage.save(folder / name)
            conn.execute("INSERT INTO evidence (incident_id, kind, path, mime, bytes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                         (incident_id, kind, f"{uid}/{name}", mimetypes.guess_type(name)[0],
                          (folder / name).stat().st_size, now))
        conn.execute("INSERT INTO audit_log (incident_id, at, actor, action, to_value, note) VALUES (?, ?, ?, ?, ?, ?)",
                     (incident_id, now, f"Camera {cam.get('id') or '?'}", "detected", "pending_review",
                      f"Auto-detected {offender_type} littering in {meta.get('zone') or 'zone'}"))
        if cam.get("id") and CAMERA_RE.match(str(cam["id"])):
            _upsert_camera(conn, str(cam["id"]), {"name": cam.get("name"), "location": cam.get("location"),
                                                  "ward": cam.get("ward"), "zone": meta.get("zone")}, touch=False)
        conn.commit()
        log.info("incident #%s %s (%s, plate=%s)", incident_id, uid, offender_type, plate_text)
        return jsonify(id=incident_id, uid=uid), 201

    def _upsert_camera(conn, camera_id: str, body: dict, touch: bool = True) -> None:
        now = now_iso()
        conn.execute("INSERT INTO cameras (id, created_at) VALUES (?, ?) ON CONFLICT(id) DO NOTHING", (camera_id, now))
        fields = {k: body.get(k) for k in ("name", "location", "ward", "zone", "preview_url") if body.get(k)}
        if "stats" in body:
            fields["stats"] = json.dumps(body["stats"])
        if touch:
            fields["last_seen"] = now
        if fields:
            conn.execute(f"UPDATE cameras SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), camera_id))

    @app.post("/api/v1/cameras/<camera_id>/heartbeat")
    def heartbeat(camera_id: str):
        require_key()
        if not CAMERA_RE.match(camera_id):
            abort(400, description="invalid camera id")
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, description="JSON object expected")
        conn = db()
        _upsert_camera(conn, camera_id, body)
        conn.commit()
        return jsonify(ok=True)

    # ----------------------------------------------------------- dashboard API
    @app.get("/api/v1/health")
    def health():
        return jsonify(ok=True, time=now_iso())

    @app.get("/api/v1/settings")
    def get_settings():
        return jsonify(municipality=settings.municipality, department=settings.department,
                       timezone=settings.timezone, currency="INR", fines=settings.fines,
                       notice_due_days=settings.notice_due_days, statuses=STATUSES)

    @app.get("/api/v1/incidents")
    def list_incidents():
        args = request.args
        where, params = [], []
        if args.get("status"):
            statuses = [s for s in args["status"].split(",") if s in STATUSES]
            if statuses:
                where.append(f"status IN ({','.join('?' * len(statuses))})")
                params += statuses
        if args.get("type") in ("person", "vehicle", "unknown"):
            where.append("offender_type = ?"); params.append(args["type"])
        if args.get("cleanup") in ("pending", "assigned", "cleaned"):
            where.append("cleanup_status = ?"); params.append(args["cleanup"])
        if args.get("camera"):
            where.append("camera_id = ?"); params.append(args["camera"])
        if args.get("q"):
            q = re.sub(r"\s+", "", args["q"].upper())
            where.append("(REPLACE(UPPER(COALESCE(plate_number,'')),' ','') LIKE ? OR UPPER(uid) LIKE ? "
                         "OR UPPER(COALESCE(notice_number,'')) LIKE ? OR CAST(id AS TEXT) = ?)")
            params += [f"%{q}%", f"%{q}%", f"%{q}%", args["q"].strip().lstrip("#")]
        for key, op, delta in (("from", ">=", 0), ("to", "<", 1)):
            if args.get(key):
                try:
                    day = datetime.strptime(args[key], "%Y-%m-%d").replace(tzinfo=tz) + timedelta(days=delta)
                except ValueError:
                    abort(400, description=f"'{key}' must be YYYY-MM-DD")
                where.append(f"occurred_at {op} ?"); params.append(day.astimezone(timezone.utc).isoformat())
        if args.get("overdue") == "1":
            where.append("status = 'notice_issued' AND due_date < ?"); params.append(now_iso())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        page = max(1, args.get("page", 1, type=int))
        size = min(100, max(1, args.get("page_size", 20, type=int)))
        conn = db()
        total = conn.execute(f"SELECT COUNT(*) FROM incidents {clause}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM incidents {clause} ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
                            (*params, size, (page - 1) * size)).fetchall()
        ev = evidence_map([r["id"] for r in rows])
        return jsonify(items=[serialize(r, ev.get(r["id"], {})) for r in rows],
                       total=total, page=page, page_size=size)

    @app.get("/api/v1/incidents/<int:incident_id>")
    def incident_detail(incident_id: int):
        row = get_incident(incident_id)
        conn = db()
        d = serialize(row, evidence_map([incident_id]).get(incident_id, {}))
        d["timeline"] = [dict(r) for r in conn.execute(
            "SELECT at, actor, action, from_value, to_value, note FROM audit_log WHERE incident_id = ? ORDER BY id",
            (incident_id,))]
        if d.get("plate_number"):
            d["plate_history"] = [dict(r) for r in conn.execute(
                "SELECT id, occurred_at, status, fine_amount FROM incidents "
                "WHERE plate_number = ? AND id != ? AND status != 'rejected' ORDER BY occurred_at DESC LIMIT 10",
                (d["plate_number"], incident_id))]
        cam = conn.execute("SELECT * FROM cameras WHERE id = ?", (d.get("camera_id"),)).fetchone()
        d["camera"] = row_to_dict(cam)
        return jsonify(d)

    @app.post("/api/v1/incidents/<int:incident_id>/actions")
    def incident_action(incident_id: int):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, description="JSON object expected")
        conn = db()
        incident = row_to_dict(get_incident(incident_id))
        try:
            apply_action(conn, settings, incident, body)
        except (WorkflowError, ValueError, TypeError) as e:
            conn.rollback()
            return jsonify(error=str(e)), 409
        conn.commit()
        return incident_detail(incident_id)

    @app.get("/api/v1/stats")
    def stats():
        conn = db()
        rows = [dict(r) for r in conn.execute(
            "SELECT id, occurred_at, offender_type, vehicle_category, status, cleanup_status, fine_amount, "
            "plate_number, plate_display, due_date FROM incidents")]
        now_utc = datetime.now(timezone.utc)
        today = now_utc.astimezone(tz).date()
        days = int(request.args.get("days", 14))
        days = min(90, max(7, days))
        by_status = Counter(r["status"] for r in rows)
        by_type = Counter(r["offender_type"] for r in rows)
        daily = {(today - timedelta(days=i)).isoformat(): {"person": 0, "vehicle": 0, "unknown": 0}
                 for i in range(days - 1, -1, -1)}
        hourly = [0] * 24
        plates: dict[str, dict] = {}
        today_count = 0
        for r in rows:
            local = parse_time(r["occurred_at"]).astimezone(tz)
            key = local.date().isoformat()
            if key in daily:
                daily[key][r["offender_type"]] += 1
            if local.date() == today:
                today_count += 1
            hourly[local.hour] += 1
            if r["plate_number"] and r["status"] != "rejected":
                p = plates.setdefault(r["plate_number"], {"plate": r["plate_number"],
                                                          "display": r["plate_display"] or r["plate_number"],
                                                          "count": 0, "fines": 0, "last_seen": r["occurred_at"]})
                p["count"] += 1
                p["fines"] += r["fine_amount"] or 0
                p["last_seen"] = max(p["last_seen"], r["occurred_at"])
        issued = [r for r in rows if r["status"] in ("notice_issued", "paid", "escalated")]
        reviewed = sum(by_status[s] for s in ("verified", "notice_issued", "paid", "escalated"))
        overdue = sum(1 for r in rows if r["status"] == "notice_issued" and r["due_date"]
                      and parse_time(r["due_date"]) < now_utc)
        cams = conn.execute("SELECT last_seen FROM cameras").fetchall()
        online = sum(1 for c in cams if c["last_seen"] and
                     (now_utc - parse_time(c["last_seen"])).total_seconds() <= settings.camera_online_seconds)
        return jsonify(
            totals={"all": len(rows), "today": today_count, "pending_review": by_status["pending_review"],
                    "overdue": overdue, "cleanup_pending": sum(1 for r in rows if r["cleanup_status"] != "cleaned"),
                    "cameras": len(cams), "cameras_online": online},
            by_status={s: by_status[s] for s in STATUSES},
            by_type={t: by_type[t] for t in ("person", "vehicle", "unknown")},
            by_vehicle=dict(Counter(r["vehicle_category"] or "other" for r in rows if r["offender_type"] == "vehicle")),
            fines={"issued": sum(r["fine_amount"] or 0 for r in issued),
                   "collected": sum(r["fine_amount"] or 0 for r in rows if r["status"] == "paid"),
                   "notices": len(issued)},
            review_accuracy=round(reviewed / (reviewed + by_status["rejected"]), 3)
            if reviewed + by_status["rejected"] else None,
            daily=[{"date": k, **v} for k, v in daily.items()],
            hourly=[{"hour": h, "count": c} for h, c in enumerate(hourly)],
            repeat_offenders=sorted([p for p in plates.values() if p["count"] > 1],
                                    key=lambda p: (-p["count"], p["plate"]))[:8],
        )

    @app.get("/api/v1/cameras")
    def cameras():
        now_utc = datetime.now(timezone.utc)
        out = []
        for row in db().execute("SELECT * FROM cameras ORDER BY id"):
            d = row_to_dict(row)
            seen = parse_time(d.get("last_seen"))
            d["online"] = bool(seen and (now_utc - seen).total_seconds() <= settings.camera_online_seconds)
            d["incidents"] = db().execute("SELECT COUNT(*) FROM incidents WHERE camera_id = ?", (d["id"],)).fetchone()[0]
            out.append(d)
        return jsonify(out)

    @app.get("/media/<path:path>")
    def media(path: str):
        return send_from_directory(settings.media_dir, path, conditional=True)

    # ---------------------------------------------------------- React dashboard
    @app.get("/")
    @app.get("/<path:path>")
    def dashboard(path: str = ""):
        if path.startswith(("api/", "media/")):
            abort(404)
        dist = settings.dashboard_dir
        if path and (dist / path).is_file():
            return send_from_directory(dist, path)
        if (dist / "index.html").is_file():
            return send_from_directory(dist, "index.html")
        return ("<h1>Litter-watch API is running</h1><p>Build the dashboard with "
                "<code>cd dashboard && npm install && npm run build</code>.</p>"), 200

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    s: Settings = app.config["SETTINGS"]
    if s.api_key == "demo-edge-key":
        log.warning("Using the demo API key. Set LW_API_KEY before exposing this server on a network.")
    log.info("database: %s", s.db_path)
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
