"""Optional access code for the dashboard (set LW_DASHBOARD_PASSWORD).

When the dashboard is on the public internet (Vercel + Render), anyone who finds
the address could otherwise verify cases, issue notices and see evidence photos.
With a password set:

* ``POST /api/v1/auth {"password": ...}`` returns a signed token valid for
  ``TOKEN_HOURS``. The dashboard sends it as ``Authorization: Bearer <token>``.
* Evidence URLs in API responses carry the token as ``?t=...`` because ``<img>`` and
  ``<video>`` tags can't send headers.
* The edge device is unaffected: it keeps using ``X-API-Key``.

Tokens are ``<expiry>.<HMAC-SHA256(expiry)>``, keyed by the password and API key,
so changing either signs everyone out. No cookies, so it works across the two
domains (vercel.app -> onrender.com) where browsers block third-party cookies.
"""
from __future__ import annotations

import hashlib
import hmac
import time

TOKEN_HOURS = 12


def _key(password: str, api_key: str) -> bytes:
    return hashlib.sha256(f"lw-dashboard|{password}|{api_key}".encode()).digest()


def issue_token(password: str, api_key: str, now: float | None = None) -> tuple[str, int]:
    exp = int((now or time.time()) + TOKEN_HOURS * 3600)
    sig = hmac.new(_key(password, api_key), str(exp).encode(), hashlib.sha256).hexdigest()[:40]
    return f"{exp}.{sig}", exp


def check_token(token: str | None, password: str, api_key: str, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or int(exp_s) < (now or time.time()):
        return False
    good = hmac.new(_key(password, api_key), exp_s.encode(), hashlib.sha256).hexdigest()[:40]
    return hmac.compare_digest(sig, good)


def password_ok(given: str, password: str) -> bool:
    return hmac.compare_digest(given.encode(), password.encode())
