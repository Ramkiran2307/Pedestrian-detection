"""Store-and-forward uploader.

Incidents are first written to ``outbox/<uid>/`` on disk, then this thread sends
them to the server. If the network or server is down nothing is lost: folders
stay in the outbox and are retried. The server ignores duplicates (same uid),
so sending twice is harmless.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import shutil
import threading
import time
from pathlib import Path

import requests

from .config import EdgeConfig

log = logging.getLogger(__name__)


class Uploader(threading.Thread):
    def __init__(self, cfg: EdgeConfig, stats_fn=None):
        super().__init__(name="uploader", daemon=True)
        self.cfg = cfg
        self.stats_fn = stats_fn
        self.outbox = Path(cfg.evidence.outbox_dir)
        self.sent = Path(cfg.evidence.sent_dir)
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.uploaded = 0
        self.failures = 0
        self.last_error: str | None = None
        self._last_heartbeat = 0.0

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.cfg.server.api_key}

    def notify(self, _path=None) -> None:
        self.wake.set()

    def stop(self) -> None:
        self.stopping.set()
        self.wake.set()

    def pending(self) -> list[Path]:
        if not self.outbox.exists():
            return []
        return sorted(p for p in self.outbox.iterdir() if p.is_dir() and not p.name.startswith(".")
                      and (p / "meta.json").exists())

    def upload(self, folder: Path) -> bool:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        handles = {}
        try:
            for kind, name in meta.get("files", {}).items():
                path = folder / name
                if path.exists():
                    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    handles[kind] = (path.name, open(path, "rb"), mime)
            r = requests.post(f"{self.cfg.server.url}/api/v1/incidents", headers=self.headers,
                              data={"metadata": json.dumps(meta)}, files=handles,
                              timeout=self.cfg.server.timeout_seconds)
        finally:
            for _, fh, _ in handles.values():
                fh.close()
        if r.status_code in (200, 201):
            log.info("uploaded %s -> incident #%s", folder.name, r.json().get("id"))
            return True
        self.last_error = f"HTTP {r.status_code}: {r.text[:200]}"
        log.warning("upload of %s rejected: %s", folder.name, self.last_error)
        return False

    def _done(self, folder: Path) -> None:
        if self.cfg.evidence.keep_local:
            self.sent.mkdir(parents=True, exist_ok=True)
            target = self.sent / folder.name
            if target.exists():
                shutil.rmtree(target)
            folder.rename(target)
        else:
            shutil.rmtree(folder)

    def heartbeat(self) -> None:
        cam = self.cfg.camera
        body = {"name": cam.name, "location": cam.location, "ward": cam.ward, "zone": self.cfg.zone.name,
                "preview_url": f"http://{self._preview_host()}:{self.cfg.preview.port}/stream.mjpg"
                if self.cfg.preview.enabled else None,
                "stats": self.stats_fn() if self.stats_fn else {}}
        try:
            requests.post(f"{self.cfg.server.url}/api/v1/cameras/{cam.id}/heartbeat", json=body,
                          headers=self.headers, timeout=5)
        except requests.RequestException as e:
            log.debug("heartbeat failed: %s", e)

    def _preview_host(self) -> str:
        host = self.cfg.preview.host
        return "localhost" if host in ("0.0.0.0", "127.0.0.1") else host

    def run(self) -> None:
        while not self.stopping.is_set():
            now = time.monotonic()
            if now - self._last_heartbeat >= self.cfg.server.heartbeat_seconds:
                self._last_heartbeat = now
                self.heartbeat()
            for folder in self.pending():
                try:
                    if self.upload(folder):
                        self.uploaded += 1
                        self._done(folder)
                    else:
                        self.failures += 1
                except requests.RequestException as e:
                    self.failures += 1
                    self.last_error = str(e)
                    log.warning("server unreachable (%s); will retry", e.__class__.__name__)
                    break
            self.wake.wait(self.cfg.server.retry_seconds)
            self.wake.clear()

    def flush(self, timeout: float = 30.0) -> None:
        """Try to send everything still in the outbox (used on shutdown)."""
        deadline = time.monotonic() + timeout
        while self.pending() and time.monotonic() < deadline:
            folder = self.pending()[0]
            try:
                if self.upload(folder):
                    self.uploaded += 1
                    self._done(folder)
                    continue
            except requests.RequestException as e:
                self.last_error = str(e)
            break
