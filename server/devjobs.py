"""Development tool: process an uploaded video from the dashboard.

The dashboard uploads a video file -> it is saved under data/dev_jobs/<job>/ ->
the normal edge pipeline (``python -m edge.run``) processes it in a background
process, as camera ``DEV-UPLOAD`` -> incidents reach this server through the
usual upload API, so the dashboard updates by itself. Jobs run one at a time.

Enabled by default for the development phase; set LW_DEV_TOOLS=0 to turn the
upload off (for example on a shared network).
"""
from __future__ import annotations

import json
import logging
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .settings import ROOT, Settings

log = logging.getLogger("litterwatch.devjobs")

VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg"}
DEV_CAMERA = "DEV-UPLOAD"
PROGRESS = "@@PROGRESS "


@dataclass
class Job:
    id: str
    filename: str
    path: Path
    full_frame: bool
    status: str = "queued"           # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    progress: dict = field(default_factory=dict)
    error: str | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=60))
    proc: subprocess.Popen | None = None

    def to_dict(self) -> dict:
        p = self.progress
        total = p.get("total") or 0
        return {
            "id": self.id, "filename": self.filename, "status": self.status, "full_frame": self.full_frame,
            "created": self.created, "started": self.started, "finished": self.finished,
            "frames": p.get("position", 0), "total_frames": total,
            "percent": round(100 * min(1.0, p.get("position", 0) / total), 1) if total else None,
            "fps": p.get("fps"), "incidents": p.get("incidents", 0), "uploaded": p.get("uploaded", 0),
            "binned": p.get("cache_binned", 0), "error": self.error,
            "log_tail": list(self.log)[-12:] if self.status == "failed" else [],
        }


class JobRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.dir = settings.data_dir / "dev_jobs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.q: queue.Queue[Job] = queue.Queue()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, name="dev-jobs", daemon=True)
        self.thread.start()

    # ------------------------------------------------------------------ API
    def submit(self, storage, full_frame: bool) -> Job:
        name = Path(storage.filename or "video.mp4").name
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXT:
            raise ValueError(f"Unsupported file type '{ext or '?'}'. Use one of: {', '.join(sorted(VIDEO_EXT))}")
        job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        folder = self.dir / job_id
        folder.mkdir(parents=True)
        path = folder / f"input{ext}"
        storage.save(path)
        if path.stat().st_size == 0:
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError("The uploaded file is empty")
        job = Job(job_id, name, path, full_frame)
        with self.lock:
            self.jobs[job_id] = job
        self.q.put(job)
        log.info("dev job %s queued: %s", job_id, name)
        return job

    def list(self) -> list[dict]:
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)
        return [j.to_dict() for j in jobs[:20]]

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.status = "cancelled"
            job.finished = time.time()
            if job.proc and job.proc.poll() is None:
                job.proc.terminate()
        return job

    # --------------------------------------------------------------- worker
    def _command(self, job: Job) -> list[str]:
        s = self.settings
        overrides = {
            "camera": {"id": DEV_CAMERA, "name": f"Uploaded video: {job.filename}", "location": "Developer upload",
                       "ward": "Dev", "realtime": False, "loop": False},
            "evidence": {"outbox_dir": str(job.path.parent / "outbox"), "sent_dir": str(job.path.parent / "sent")},
            "server": {"enabled": True, "url": f"http://127.0.0.1:{s.port}", "api_key": s.api_key,
                       "heartbeat_seconds": 5, "retry_seconds": 3},
            "preview": {"enabled": True, "host": "127.0.0.1", "port": s.dev_preview_port},
        }
        if job.full_frame:
            overrides["zone"] = {"name": "Whole frame", "polygon": []}
        return [sys.executable, "-u", "-m", "edge.run", "--source", str(job.path), "--progress",
                "--override", json.dumps(overrides)]

    def _loop(self) -> None:
        while True:
            job = self.q.get()
            if job.status == "cancelled":
                continue
            try:
                self._run(job)
            except Exception as e:  # keep the runner alive whatever happens
                log.exception("dev job %s crashed", job.id)
                job.status, job.error, job.finished = "failed", str(e), time.time()

    def _run(self, job: Job) -> None:
        job.status, job.started = "running", time.time()
        log.info("dev job %s running", job.id)
        job.proc = subprocess.Popen(self._command(job), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
        with open(job.path.parent / "run.log", "w", encoding="utf-8") as logfile:
            for line in job.proc.stdout:
                logfile.write(line)
                if line.startswith(PROGRESS):
                    try:
                        job.progress = json.loads(line[len(PROGRESS):])
                    except ValueError:
                        pass
                elif line.strip():
                    job.log.append(line.rstrip()[:300])
        code = job.proc.wait()
        job.finished = time.time()
        if job.status == "cancelled":
            return
        if code == 0:
            job.status = "done"
            log.info("dev job %s done: %s incidents", job.id, job.progress.get("incidents", 0))
        else:
            job.status = "failed"
            errors = [l for l in job.log if "Error" in l or "error" in l or "Traceback" in l]
            job.error = (errors[-1] if errors else (job.log[-1] if job.log else f"exit code {code}"))[:300]
            log.warning("dev job %s failed: %s", job.id, job.error)
