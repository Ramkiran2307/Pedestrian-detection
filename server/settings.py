"""Server settings, read from environment variables (all optional for the demo).

  LW_API_KEY          shared secret the edge device sends in X-API-Key
  LW_DATA_DIR         where the SQLite DB and evidence files live (server/data)
  LW_DASHBOARD_DIR    built React app to serve at / (dashboard/dist)
  LW_MUNICIPALITY     name printed on notices
  LW_TIMEZONE         used for daily/hourly statistics (Asia/Kolkata)
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
