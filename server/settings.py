"""Server settings, read from environment variables (all optional for the demo).

  LW_API_KEY          shared secret the edge device sends in X-API-Key
  LW_DATA_DIR         where the SQLite DB and evidence files live (server/data)
  LW_DASHBOARD_DIR    built React app to serve at / (dashboard/dist)
  LW_MUNICIPALITY     name printed on notices
  LW_TIMEZONE         used for daily/hourly statistics (Asia/Kolkata)
  LW_DEV_TOOLS        1 (default) = dashboard can upload a video for processing; 0 = off
  LW_DASHBOARD_PASSWORD  access code for the dashboard (empty = no login; set it when online)
  LW_CORS_ORIGINS     comma-separated sites allowed to call the API from a browser, e.g.
                      https://swachh-netra.vercel.app or https://*.vercel.app (dashboard on Vercel)
  LW_SEED_SAMPLE      e.g. 40 = add that many sample incidents when the database is empty
  PORT / HOST         where to listen (5000 / 127.0.0.1)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.environ.get("LW_API_KEY", "demo-edge-key"))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("LW_DATA_DIR", ROOT / "server" / "data")))
    dashboard_dir: Path = field(default_factory=lambda: Path(os.environ.get("LW_DASHBOARD_DIR", ROOT / "dashboard" / "dist")))
    municipality: str = field(default_factory=lambda: os.environ.get("LW_MUNICIPALITY", "Demo Municipal Corporation"))
    department: str = "Solid Waste Management Department"
    timezone: str = field(default_factory=lambda: os.environ.get("LW_TIMEZONE", "Asia/Kolkata"))
    notice_prefix: str = "SWM"
    notice_due_days: int = 15
    camera_online_seconds: int = 60
    max_upload_mb: int = 64
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", 5000)))
    # Development-phase video upload (dashboard -> edge pipeline on this machine)
    dev_tools: bool = field(default_factory=lambda: os.environ.get("LW_DEV_TOOLS", "1") != "0")
    dev_max_upload_mb: int = 1024
    dev_preview_port: int = 8091
    # Hosting (dashboard on Vercel, API on Render)
    dashboard_password: str = field(default_factory=lambda: os.environ.get("LW_DASHBOARD_PASSWORD", ""))
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip().rstrip("/") for o in os.environ.get("LW_CORS_ORIGINS", "").split(",") if o.strip()])
    seed_sample: int = field(default_factory=lambda: int(os.environ.get("LW_SEED_SAMPLE", "0") or 0))
    # Sample spot-fine schedule (INR). Real amounts come from each city's SWM bye-laws.
    fines: dict[str, int] = field(default_factory=lambda: {
        "person": 500, "two_wheeler": 1000, "car": 2000, "heavy": 5000, "unknown": 0,
    })

    @property
    def db_path(self) -> Path:
        return self.data_dir / "litterwatch.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"
