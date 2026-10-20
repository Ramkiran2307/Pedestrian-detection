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
    d = act(client, iid, action="issue_notice").get_json()
    assert d["status"] == "notice_issued" and d["fine_amount"] == 2000        # car fine from schedule
    assert d["notice_number"].startswith("SWM/WARD12/")
    d = act(client, iid, action="mark_paid", payment_ref="UPI123").get_json()
    assert d["status"] == "paid" and d["payment_ref"] == "UPI123"
    assert [t["action"] for t in d["timeline"]] == ["detected", "verify", "issue_notice", "mark_paid"]
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
