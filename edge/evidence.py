"""Evidence: rolling frame buffer, clip/snapshot rendering and the incident worker.

* ``FrameRing`` keeps the last few seconds of frames (JPEG-compressed, so it fits
  in a Raspberry Pi's memory) together with what was detected in each frame.
* When litter is confirmed, a ``PendingIncident`` grabs the frames from before
  the drop and keeps collecting for ``post_seconds`` more.
* ``IncidentWorker`` (a background thread, so the video loop never stalls) then
  reads the plate, renders the annotated clip and snapshots, writes everything to
  ``outbox/<uid>/`` and lets the uploader send it to the server.
"""
from __future__ import annotations

import json
import logging
import queue
import shutil
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from .cache import TrackCache, TrackRecord
from .config import EdgeConfig
from .geometry import Box, crop, scale
from .plates import PlateReader, PlateResult

log = logging.getLogger(__name__)

COLORS = {
    "person": (235, 160, 40),     # BGR: blue-ish
    "vehicle": (90, 200, 60),     # green
    "offender": (40, 40, 230),    # red
    "litter": (0, 165, 255),      # orange
    "muted": (170, 170, 170),
}


@dataclass
class FrameEntry:
    t: float
    jpeg: bytes
    scale: float                      # entry pixels = full-res pixels * scale
    actors: list[tuple[str, str, str, Box]]   # (key, kind, cls_name, full-res box)
    litter: list[tuple[Box, float, bool]]


class FrameRing:
    def __init__(self, seconds: float, width: int, quality: int):
        self.seconds = seconds
        self.width = width
        self.quality = quality
        self.items: deque[FrameEntry] = deque()

    def add(self, t: float, frame: np.ndarray, actors, litter) -> FrameEntry:
        h, w = frame.shape[:2]
        s = min(1.0, self.width / w)
        small = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else frame
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        entry = FrameEntry(t, buf.tobytes() if ok else b"", s, actors, litter)
        self.items.append(entry)
        while self.items and self.items[0].t < t - self.seconds:
            self.items.popleft()
        return entry

    def since(self, t0: float) -> list[FrameEntry]:
        return [e for e in self.items if e.t >= t0]


@dataclass
class Actor:
    """Frozen copy of the cache data an incident needs (the cache keeps changing)."""
    key: str
    kind: str
    cls_name: str
    score: float
    crops: list[np.ndarray]
    record: TrackRecord


@dataclass
class PendingIncident:
    uid: str
    t_appear: float
    t_confirm: float
    occurred_at: datetime
    litter_box: Box
    litter_crop: np.ndarray
    offender: Actor | None
    linked: Actor | None
    suspects: list[dict]
    frames: list[FrameEntry]
    collect_until: float
    done: bool = False
    more_litter: list[Box] = field(default_factory=list)   # extra pieces merged into this incident
    extra: dict = field(default_factory=dict)

    @property
    def highlight(self) -> set[str]:
        return {a.key for a in (self.offender, self.linked) if a}


def decode(entry: FrameEntry) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(entry.jpeg, np.uint8), cv2.IMREAD_COLOR)


def _label(img, text, x, y, color):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    y = max(th + 4, int(y))
    cv2.rectangle(img, (int(x), y - th - 6), (int(x) + tw + 6, y), color, -1)
    cv2.putText(img, text, (int(x) + 3, y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)


def render(entry: FrameEntry, inc: PendingIncident | None, header: str, plate_text: str | None = None) -> np.ndarray:
    img = decode(entry)
    s = entry.scale
    hl = inc.highlight if inc else set()
    for key, kind, cls_name, box in entry.actors:
        x1, y1, x2, y2 = (int(v) for v in scale(box, s))
        if key in hl:
            cv2.rectangle(img, (x1, y1), (x2, y2), COLORS["offender"], 3)
            tag = f"OFFENDER {cls_name} {key}"
            if kind == "vehicle" and plate_text:
                tag += f" | {plate_text}"
            _label(img, tag, x1, y1, COLORS["offender"])
        else:
            cv2.rectangle(img, (x1, y1), (x2, y2), COLORS["muted"], 1)
    if inc and entry.t >= inc.t_appear:
        for box in [inc.litter_box, *inc.more_litter]:
            x1, y1, x2, y2 = (int(v) for v in scale(box, s))
            cv2.rectangle(img, (x1 - 4, y1 - 4), (x2 + 4, y2 + 4), COLORS["litter"], 2)
            _label(img, "LITTER", x1 - 4, y1 - 6, COLORS["litter"])
    cv2.rectangle(img, (0, 0), (img.shape[1], 24), (20, 20, 20), -1)
    cv2.putText(img, header, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def write_clip(frames: list[np.ndarray], fps: float, out_base: Path) -> Path | None:
    """H.264 MP4 via ffmpeg when installed (small, plays everywhere), else WebM/VP8 via OpenCV."""
    if not frames:
        return None
    fps = float(min(30.0, max(2.0, fps)))
    if shutil.which("ffmpeg"):
        path = _write_ffmpeg(frames, fps, out_base.with_suffix(".mp4"))
        if path:
            return path
    h, w = frames[0].shape[:2]
    for fourcc, ext in (("VP80", ".webm"), ("mp4v", ".mp4")):
        path = out_base.with_suffix(ext)
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
        if not writer.isOpened():
            continue
        for f in frames:
            writer.write(f)
        writer.release()
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def _write_ffmpeg(frames: list[np.ndarray], fps: float, path: Path) -> Path | None:
    h, w = frames[0].shape[:2]
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", f"{fps:.3f}", "-i", "-",
           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "28", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for f in frames:
            proc.stdin.write(np.ascontiguousarray(f).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait(timeout=120) == 0 and path.exists() and path.stat().st_size > 0:
            return path
        log.warning("ffmpeg failed (%s); falling back to OpenCV", err.strip()[:200])
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("ffmpeg unavailable (%s); falling back to OpenCV", e)
    return None


VEHICLE_CATEGORY = {"motorcycle": "two_wheeler", "car": "car", "bus": "heavy", "truck": "heavy"}


class IncidentWorker(threading.Thread):
    def __init__(self, cfg: EdgeConfig, cache: TrackCache, on_ready=None):
        super().__init__(name="incident-worker", daemon=True)
        self.cfg = cfg
        self.cache = cache
        self.on_ready = on_ready
        self.q: queue.Queue[PendingIncident | None] = queue.Queue()
        self.plates = PlateReader(cfg.models.plate, cfg.ocr, cfg.models.device)
        self.completed = 0
        Path(cfg.evidence.outbox_dir).mkdir(parents=True, exist_ok=True)

    def submit(self, inc: PendingIncident) -> None:
        self.q.put(inc)

    def stop(self) -> None:
        self.q.put(None)

    def run(self) -> None:
        while True:
            inc = self.q.get()
            if inc is None:
                return
            try:
                self.finalize(inc)
                self.completed += 1
            except Exception:  # never let one bad incident kill the worker
                log.exception("failed to finalise incident %s", inc.uid)
            finally:
                for a in (inc.offender, inc.linked):
                    if a:
                        self.cache.unpin(a.record)

    def finalize(self, inc: PendingIncident) -> Path:
        cam = self.cfg.camera
        tmp = Path(self.cfg.evidence.outbox_dir) / f".{inc.uid}"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)

        vehicle = next((a for a in (inc.offender, inc.linked) if a and a.kind == "vehicle"), None)
        person = next((a for a in (inc.offender, inc.linked) if a and a.kind == "person"), None)
        plate = self.plates.read(vehicle.crops) if vehicle else PlateResult()

        files: dict[str, str] = {}

        def save(name: str, img: np.ndarray | None) -> None:
            if img is not None and img.size:
                cv2.imwrite(str(tmp / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
                files[name] = f"{name}.jpg"

        stamp = inc.occurred_at.astimezone().strftime("%d-%m-%Y %H:%M:%S")
        header = f"{cam.id} | {self.cfg.zone.name} | {stamp}"
        rendered = [render(e, inc, header, plate.pretty if plate.valid else None) for e in inc.frames]
        # Scene snapshot: the moment the litter first appeared, with the offender marked.
        scene_idx = min(range(len(inc.frames)), key=lambda i: abs(inc.frames[i].t - inc.t_appear)) if inc.frames else None
        if scene_idx is not None:
            save("scene", rendered[scene_idx])
        save("litter", inc.litter_crop)
        if person:
            save("person", person.crops[0] if person.crops else None)
        if vehicle:
            save("vehicle", vehicle.crops[0] if vehicle.crops else None)
        save("plate", plate.plate_image)

        if len(inc.frames) > 1:
            span = inc.frames[-1].t - inc.frames[0].t
            fps = (len(inc.frames) - 1) / span if span > 0 else 10.0
            clip = write_clip(rendered, fps, tmp / "clip")
            if clip:
                files["clip"] = clip.name

        off = inc.offender
        meta = {
            "uid": inc.uid,
            "camera": {"id": cam.id, "name": cam.name, "location": cam.location, "ward": cam.ward},
            "zone": self.cfg.zone.name,
            "occurred_at": inc.occurred_at.isoformat(),
            "detected_at": (inc.occurred_at + timedelta(seconds=inc.t_confirm - inc.t_appear)).isoformat(),
            "offender_type": off.kind if off else "unknown",
            "offender_track": off.key if off else None,
            "attribution_score": off.score if off else 0.0,
            "vehicle_class": vehicle.cls_name if vehicle else None,
            "vehicle_category": VEHICLE_CATEGORY.get(vehicle.cls_name) if vehicle else None,
            "linked_track": inc.linked.key if inc.linked else None,
            "plate": {
                # An unreadable plate is sent as "not read" (with the raw OCR guesses) so the
                # officer types it in from plate.jpg, instead of a wrong number being stored.
                "text": plate.text if plate.valid else None,
                "pretty": plate.pretty if plate.valid else None,
                "confidence": plate.confidence, "valid": plate.valid,
                "raw": plate.raw[:12], "detections": plate.detections,
            } if vehicle else None,
            "litter_bbox": [round(v, 1) for v in inc.litter_box],
            "more_litter": [[round(v, 1) for v in b] for b in inc.more_litter],
            "suspects": inc.suspects,
            "files": files,
            "edge_version": "1.0",
        }
        (tmp / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        final = Path(self.cfg.evidence.outbox_dir) / inc.uid
        tmp.rename(final)   # atomic: the uploader only ever sees complete folders
        log.info("incident %s ready: %s %s plate=%s", inc.uid, meta["offender_type"],
                 off.key if off else "-", plate.text if plate.valid else ("not read" if vehicle else "-"))
        if self.on_ready:
            self.on_ready(final)
        return final


def new_uid(camera_id: str) -> str:
    return f"{camera_id}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"


def freeze_actor(rec: TrackRecord, score: float) -> Actor:
    return Actor(rec.key, rec.kind, rec.cls_name, score, rec.best_crops(), rec)


def litter_crop(frame: np.ndarray, box: Box) -> np.ndarray:
    img = crop(frame, box, pad=1.0)
    if img.size and max(img.shape[:2]) < 160:
        s = 160 / max(img.shape[:2])
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
    return img
