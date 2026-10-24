"""Hosting setup: dashboard on another site (CORS), access-code login, sample seeding."""
import io
import json

from server import auth
from server.app import create_app
from server.settings import Settings

KEY = "edge-key"
VERCEL = "https://swachh-netra.vercel.app"


def app_with(tmp_path, **kw):
    s = Settings(api_key=KEY, data_dir=tmp_path, dashboard_dir=tmp_path / "nodist", dev_tools=False, **kw)
    return create_app(s).test_client()


def upload(c, uid="H-1"):
    meta = {"uid": uid, "camera": {"id": "CAM-01"}, "zone": "Z", "occurred_at": "2026-10-20T10:00:00+00:00",
            "offender_type": "person", "files": {}}
    return c.post("/api/v1/incidents", data={"metadata": json.dumps(meta), "scene": (io.BytesIO(b"img"), "scene.jpg")},
                  headers={"X-API-Key": KEY}, content_type="multipart/form-data")


def test_cors_only_for_allowed_sites(tmp_path):
    c = app_with(tmp_path, cors_origins=[VERCEL])
    r = c.get("/api/v1/stats", headers={"Origin": VERCEL})
    assert r.headers["Access-Control-Allow-Origin"] == VERCEL
    assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
    assert "Access-Control-Allow-Origin" not in c.get("/api/v1/stats", headers={"Origin": "https://evil.example"}).headers
    pre = c.open("/api/v1/incidents/1/actions", method="OPTIONS",
                 headers={"Origin": VERCEL, "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200 and pre.headers["Access-Control-Allow-Origin"] == VERCEL


def test_cors_wildcard_for_vercel_previews(tmp_path):
    c = app_with(tmp_path, cors_origins=["https://*.vercel.app"])
    origin = "https://swachh-netra-git-main-ram.vercel.app"
    assert c.get("/api/v1/health", headers={"Origin": origin}).headers["Access-Control-Allow-Origin"] == origin


def test_no_password_means_open_dashboard(tmp_path):
    c = app_with(tmp_path)
    assert c.get("/api/v1/settings").get_json()["auth_required"] is False
    assert c.get("/api/v1/stats").status_code == 200


def test_access_code_protects_dashboard_and_evidence(tmp_path):
    c = app_with(tmp_path, dashboard_password="green-city-42")
    assert c.get("/api/v1/settings").get_json()["auth_required"] is True     # public
    assert c.get("/api/v1/health").status_code == 200                         # public
    r = c.get("/api/v1/stats")
    assert r.status_code == 401 and r.get_json()["auth_required"] is True
    # the camera can still upload with its API key
    assert upload(c).status_code == 201
    assert c.post("/api/v1/auth", json={"password": "wrong"}).status_code == 401
    token = c.post("/api/v1/auth", json={"password": "green-city-42"}).get_json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    items = c.get("/api/v1/incidents", headers=h).get_json()["items"]
    scene = items[0]["evidence"]["scene"]
    assert scene.startswith("/media/") and f"?t={token}" in scene              # images carry the token
    assert c.get(scene).data == b"img"
    assert c.get(scene.split("?")[0]).status_code == 401                      # not without it
    r = c.post(f"/api/v1/incidents/{items[0]['id']}/actions", json={"action": "verify", "actor": "SI"}, headers=h)
    assert r.status_code == 200


def test_tokens_expire_and_are_tied_to_the_password():
    t, exp = auth.issue_token("pw", KEY, now=1000)
    assert auth.check_token(t, "pw", KEY, now=1001)
    assert not auth.check_token(t, "pw", KEY, now=exp + 1)
    assert not auth.check_token(t, "other", KEY, now=1001)
    assert not auth.check_token("123.abc", "pw", KEY, now=1001)
    assert not auth.check_token(None, "pw", KEY)


def test_sample_seed_only_on_empty_database(tmp_path):
    c = app_with(tmp_path, seed_sample=15)
    assert c.get("/api/v1/incidents").get_json()["total"] == 15
    c2 = app_with(tmp_path, seed_sample=15)          # restart: not seeded twice
    assert c2.get("/api/v1/incidents").get_json()["total"] == 15
