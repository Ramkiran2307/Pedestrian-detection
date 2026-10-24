import io
import json

import pytest

from server.app import create_app
from server.settings import Settings

KEY = "test-key"


@pytest.fixture()
def client(tmp_path):
    s = Settings(api_key=KEY, data_dir=tmp_path, dashboard_dir=tmp_path / "nodist")
    return create_app(s).test_client()


def meta(uid="CAM-01-1", offender="vehicle", plate="KA01AB1234"):
    return {
        "uid": uid, "camera": {"id": "CAM-01", "name": "Gate", "ward": "Ward 12", "location": "Gate"},
        "zone": "Clean Zone A", "occurred_at": "2026-10-20T10:00:00+00:00",
        "offender_type": offender, "offender_track": "V3", "attribution_score": 0.9,
        "vehicle_class": "car" if offender == "vehicle" else None,
        "vehicle_category": "car" if offender == "vehicle" else None,
        "plate": {"text": plate, "pretty": "KA 01 AB 1234", "confidence": 0.9, "valid": bool(plate), "raw": [plate]}
        if offender == "vehicle" else None,
        "litter_bbox": [1, 2, 3, 4], "suspects": [], "files": {},
    }


def post(client, m, key=KEY, files=None):
    data = {"metadata": json.dumps(m)}
    for kind, (name, content) in (files or {}).items():
        data[kind] = (io.BytesIO(content), name)
    return client.post("/api/v1/incidents", data=data, headers={"X-API-Key": key},
                       content_type="multipart/form-data")


def act(client, iid, **body):
    return client.post(f"/api/v1/incidents/{iid}/actions", json={"actor": "SI Rao", **body})


def test_upload_requires_api_key(client):
    assert post(client, meta(), key="wrong").status_code == 401


def test_upload_stores_incident_and_evidence(client):
    r = post(client, meta(), files={"scene": ("scene.jpg", b"jpgdata"), "clip": ("clip.mp4", b"mp4"),
                                    "evil": ("x.exe", b"nope")})
    assert r.status_code == 201
    iid = r.get_json()["id"]
    d = client.get(f"/api/v1/incidents/{iid}").get_json()
    assert d["plate_number"] == "KA01AB1234" and d["status"] == "pending_review"
    assert set(d["evidence"]) == {"scene", "clip"}
    assert client.get(d["evidence"]["scene"]).data == b"jpgdata"
    assert d["timeline"][0]["action"] == "detected"


def test_duplicate_uid_is_idempotent(client):
    first = post(client, meta()).get_json()
    again = post(client, meta())
    assert again.status_code == 200 and again.get_json()["duplicate"] and again.get_json()["id"] == first["id"]
    assert client.get("/api/v1/incidents").get_json()["total"] == 1


def test_rejects_bad_metadata(client):
    assert post(client, {**meta(), "uid": "../../etc"}).status_code == 400
    assert post(client, {**meta(), "occurred_at": "yesterday"}).status_code == 400
    assert post(client, {**meta(), "offender_type": "alien"}).status_code == 400


def test_full_enforcement_workflow(client):
    iid = post(client, meta()).get_json()["id"]
    assert act(client, iid, action="issue_notice").status_code == 409        # must verify first
    assert act(client, iid, action="verify").status_code == 200
    r = act(client, iid, action="issue_notice")                              # plate not verified yet
    assert r.status_code == 409 and "Plate verified" in r.get_json()["error"]
    d = act(client, iid, action="verify_plate").get_json()
    assert d["plate_verified"] and d["plate_verified_by"] == "SI Rao"
    d = act(client, iid, action="issue_notice").get_json()
    assert d["status"] == "notice_issued" and d["fine_amount"] == 2000        # car fine from schedule
    assert d["notice_number"].startswith("SWM/WARD12/")
    d = act(client, iid, action="mark_paid", payment_ref="UPI123").get_json()
    assert d["status"] == "paid" and d["payment_ref"] == "UPI123"
    assert [t["action"] for t in d["timeline"]] == ["detected", "verify", "verify_plate", "issue_notice", "mark_paid"]
    assert d["timeline"][1]["actor"] == "SI Rao"


def test_reject_needs_reason_and_can_reopen(client):
    iid = post(client, meta()).get_json()["id"]
    assert act(client, iid, action="reject").status_code == 409
    assert act(client, iid, action="reject", reason="shadow").get_json()["status"] == "rejected"
    assert act(client, iid, action="reopen").get_json()["status"] == "pending_review"


def test_plate_correction_and_vehicle_without_plate(client):
    iid = post(client, meta(plate=None)).get_json()["id"]
    act(client, iid, action="verify")
    assert act(client, iid, action="issue_notice").status_code == 409        # no plate yet
    assert act(client, iid, action="correct_plate", plate="ka 01 xx 12").status_code == 409
    d = act(client, iid, action="correct_plate", plate="ka 01 ab 1234").get_json()
    assert d["plate_number"] == "KA01AB1234" and d["plate_corrected"] and d["plate_display"] == "KA 01 AB 1234"
    assert act(client, iid, action="issue_notice", fine_amount=750).get_json()["fine_amount"] == 750


def test_unknown_offender_only_tracks_cleanup(client):
    iid = post(client, meta(offender="unknown")).get_json()["id"]
    act(client, iid, action="verify")
    assert act(client, iid, action="issue_notice").status_code == 409
    assert act(client, iid, action="assign_cleanup", assignee="Team B").get_json()["cleanup_status"] == "assigned"
    assert act(client, iid, action="mark_cleaned").get_json()["cleanup_status"] == "cleaned"
    assert act(client, iid, action="mark_cleaned").status_code == 409


def test_filters_stats_and_heartbeat(client):
    post(client, meta("A-1"))
    post(client, meta("A-2", offender="person"))
    post(client, meta("A-3"))
    items = client.get("/api/v1/incidents?type=vehicle&q=ka01").get_json()
    assert items["total"] == 2
    stats = client.get("/api/v1/stats").get_json()
    assert stats["totals"]["all"] == 3 and stats["by_type"]["vehicle"] == 2
    assert stats["repeat_offenders"][0]["plate"] == "KA01AB1234" and stats["repeat_offenders"][0]["count"] == 2
    r = client.post("/api/v1/cameras/CAM-01/heartbeat", json={"stats": {"fps": 6}}, headers={"X-API-Key": KEY})
    assert r.status_code == 200
    cams = client.get("/api/v1/cameras").get_json()
    assert cams[0]["online"] and cams[0]["stats"]["fps"] == 6 and cams[0]["incidents"] == 3


def test_dev_upload_validation(tmp_path):
    s = Settings(api_key=KEY, data_dir=tmp_path, dashboard_dir=tmp_path / "nodist")
    c = create_app(s).test_client()
    assert c.get("/api/v1/settings").get_json()["dev_tools"] is True
    assert c.post("/api/v1/dev/jobs", data={}, content_type="multipart/form-data").status_code == 400
    r = c.post("/api/v1/dev/jobs", data={"video": (io.BytesIO(b"x"), "notes.txt")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "Unsupported" in r.get_json()["error"]
    r = c.post("/api/v1/dev/jobs", data={"video": (io.BytesIO(b""), "clip.mp4")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "empty" in r.get_json()["error"]
    assert c.get("/api/v1/dev/jobs").get_json() == []


def test_dev_tools_can_be_disabled(tmp_path):
    s = Settings(api_key=KEY, data_dir=tmp_path, dashboard_dir=tmp_path / "nodist", dev_tools=False)
    c = create_app(s).test_client()
    assert c.get("/api/v1/settings").get_json()["dev_tools"] is False
    assert c.get("/api/v1/dev/jobs").status_code == 404


def test_works_without_timezone_database(tmp_path, monkeypatch):
    # Windows has no IANA time-zone data unless the tzdata package is installed
    import server.app as app_module
    from zoneinfo import ZoneInfoNotFoundError

    def missing(name):
        raise ZoneInfoNotFoundError(f"No time zone found with key {name}")

    monkeypatch.setattr(app_module, "ZoneInfo", missing)
    s = Settings(api_key=KEY, data_dir=tmp_path, dashboard_dir=tmp_path / "nodist")
    c = create_app(s).test_client()
    post(c, meta("TZ-1"))
    stats = c.get("/api/v1/stats").get_json()
    assert stats["totals"]["all"] == 1
    tz = app_module.load_timezone("Asia/Kolkata")
    from datetime import datetime, timezone
    assert datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone(tz).hour == 5


def test_auto_read_plate_is_stored_unverified_with_candidates(client):
    m = meta("P-1")
    m["plate"].update(level="medium", engine="plate-ocr",
                      candidates=[{"text": "KA01AB1234", "pretty": "KA 01 AB 1234", "confidence": 0.55},
                                  {"text": "KA01AB1284", "pretty": "KA 01 AB 1284", "confidence": 0.3}])
    iid = post(client, m).get_json()["id"]
    d = client.get(f"/api/v1/incidents/{iid}").get_json()
    assert d["plate_number"] == "KA01AB1234" and not d["plate_verified"] and d["plate_level"] == "medium"
    assert [c["text"] for c in d["plate_candidates"]] == ["KA01AB1234", "KA01AB1284"]
    assert client.get("/api/v1/stats").get_json()["totals"]["plates_unverified"] == 1
    assert client.get("/api/v1/incidents?plate=unverified").get_json()["total"] == 1
    # picking another candidate = officer correction, which counts as verified
    d = act(client, iid, action="correct_plate", plate="KA01AB1284").get_json()
    assert d["plate_number"] == "KA01AB1284" and d["plate_verified"] and d["plate_corrected"]
    assert act(client, iid, action="verify_plate").status_code == 409        # already verified
    assert client.get("/api/v1/incidents?plate=unverified").get_json()["total"] == 0


def test_verify_plate_only_for_vehicles_with_a_reading(client):
    pid = post(client, meta("P-2", offender="person")).get_json()["id"]
    assert act(client, pid, action="verify_plate").status_code == 409
    vid = post(client, meta("P-3", plate=None)).get_json()["id"]
    assert act(client, vid, action="verify_plate").status_code == 409


def test_old_database_is_upgraded(tmp_path):
    import sqlite3
    db = tmp_path / "litterwatch.db"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE incidents (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL UNIQUE,
        camera_id TEXT, zone TEXT, ward TEXT, location TEXT, occurred_at TEXT NOT NULL, detected_at TEXT,
        received_at TEXT NOT NULL, offender_type TEXT NOT NULL, offender_track TEXT, attribution_score REAL,
        vehicle_class TEXT, vehicle_category TEXT, linked_track TEXT, plate_number TEXT, plate_display TEXT,
        plate_confidence REAL, plate_valid INTEGER, plate_raw TEXT, plate_corrected INTEGER NOT NULL DEFAULT 0,
        litter_bbox TEXT, suspects TEXT, status TEXT NOT NULL DEFAULT 'pending_review',
        cleanup_status TEXT NOT NULL DEFAULT 'pending', cleanup_assignee TEXT, fine_amount INTEGER,
        notice_number TEXT UNIQUE, notice_issued_at TEXT, due_date TEXT, paid_at TEXT, payment_ref TEXT,
        rejection_reason TEXT, is_sample INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        INSERT INTO incidents (uid, occurred_at, received_at, offender_type, updated_at)
        VALUES ('OLD-1', '2026-10-01T10:00:00+00:00', '2026-10-01T10:00:00+00:00', 'person', 'x');""")
    con.commit(); con.close()
    c = create_app(Settings(api_key=KEY, data_dir=tmp_path, dashboard_dir=tmp_path / "nodist")).test_client()
    items = c.get("/api/v1/incidents").get_json()["items"]
    assert items[0]["uid"] == "OLD-1" and items[0]["plate_verified"] is False
