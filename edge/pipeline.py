"""The edge pipeline: one camera, one clean zone.

Per frame:
  1. vehicles  <- yolov8n + ByteTrack     (detectors.py)
     people    <- yolo26s + ByteTrack
  2. every track is added to the short-term cache       (cache.py)
  3. litter detector looks for new static objects       (litter.py)
  4. confirmed litter -> rank suspects in the cache     (association.py)
     -> PendingIncident collecting the evidence clip    (evidence.py)
  5. cache records not seen for hold_seconds are binned
  6. annotated frame -> live preview / optional output video
Finished incidents are written to the outbox and uploaded (uploader.py).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np

from .association import linked_actor, rank_suspects
from .cache import TrackCache
from .config import EdgeConfig
from .detectors import Detection, TrackedDetector
from .evidence import (COLORS, FrameRing, IncidentWorker, PendingIncident, freeze_actor, litter_crop,
                       new_uid)
from .geometry import Zone
from .litter import LitterDetector
from .preview import PreviewServer
from .uploader import Uploader

log = logging.getLogger(__name__)


class VideoSource:
    def __init__(self, source: str, realtime: bool | None, stride: int, max_width: int, loop: bool):
        self.is_live = source.isdigit() or "://" in source
        self.cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video source: {source}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 0.0
        if not 1.0 <= self.fps <= 120.0:
            self.fps = 25.0
        self.realtime = self.is_live if realtime is None else realtime
        self.stride = stride
        self.max_width = max_width
        self.loop = loop and not self.is_live
        self.index = 0
        self.total = 0 if self.is_live else int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.loops = 0
        self.t0 = time.monotonic()

    def read(self) -> tuple[np.ndarray, float] | None:
        for _ in range(self.stride - 1):
            if self.cap.grab():
                self.index += 1
        ok, frame = self.cap.read()
        if not ok:
            if self.loop:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.loops += 1
                ok, frame = self.cap.read()
            if not ok:
                return None
        self.index += 1
        h, w = frame.shape[:2]
        if w > self.max_width:
            frame = cv2.resize(frame, (self.max_width, int(h * self.max_width / w)), interpolation=cv2.INTER_AREA)
        t = time.monotonic() - self.t0 if self.realtime else self.index / self.fps
        return frame, t

    def release(self) -> None:
        self.cap.release()


class Pipeline:
    def __init__(self, cfg: EdgeConfig, use_server: bool = True, show: bool = False,
                 output_video: str | None = None):
        self.cfg = cfg
        m = cfg.models
        log.info("loading models (vehicles: %s, people: %s)", Path(m.vehicle.weights).name, Path(m.person.weights).name)
        self.vehicles = TrackedDetector("vehicle", m.vehicle, m.device, m.tracker)
        self.people = TrackedDetector("person", m.person, m.device, m.tracker)
        self.zone = Zone(cfg.zone.name, cfg.zone.polygon)
        self.cache = TrackCache(cfg.cache)
        self.litter = LitterDetector(cfg.litter, self.zone)
        self.ring = FrameRing(cfg.ring_seconds, cfg.evidence.clip_width, cfg.evidence.jpeg_quality)
        self.uploader = Uploader(cfg, self.stats) if use_server and cfg.server.enabled else None
        self.worker = IncidentWorker(cfg, self.cache, on_ready=self.uploader.notify if self.uploader else None)
        self.preview = None
        if cfg.preview.enabled:
            try:
                self.preview = PreviewServer(cfg.preview.host, cfg.preview.port, cfg.preview.width)
            except OSError as e:  # port already used (e.g. another edge run): carry on without it
                log.warning("live preview disabled: cannot listen on %s:%d (%s)",
                            cfg.preview.host, cfg.preview.port, e)
        self.show = show or cfg.preview.show_window
        self.output_video = output_video
        self.writer: cv2.VideoWriter | None = None
        self.pending: list[PendingIncident] = []
        self.recent: list[PendingIncident] = []
        self.incidents = 0
        self.unattributed = 0
        self.frames = 0
        self.fps = 0.0
        self.wall0 = datetime.now(timezone.utc)
        self.last_t = 0.0

    # ------------------------------------------------------------------ status
    def stats(self) -> dict:
        return {
            "frames": self.frames, "fps": round(self.fps, 1), "video_time": round(self.last_t, 1),
            "cache_active": len(self.cache.records), "cache_pushed": self.cache.pushed_total,
            "cache_binned": self.cache.binned_total, "incidents": self.incidents,
            "evidence_ready": self.worker.completed,
            "uploaded": self.uploader.uploaded if self.uploader else 0,
            "upload_error": self.uploader.last_error if self.uploader else None,
            "warming_up": self.litter.warming_up,
        }

    # -------------------------------------------------------------------- loop
    def run(self, max_frames: int | None = None, progress=None) -> dict:
        """Process the source until it ends. ``progress(stats)`` is called about once a second
        (stats include ``position`` and ``total`` frames for video files)."""
        c = self.cfg.camera
        src = VideoSource(c.source, c.realtime, c.frame_stride, c.max_width, c.loop)
        log.info("source %s (%s, %.1f fps, %s clock)", c.source, "live" if src.is_live else "file",
                 src.fps, "wall" if src.realtime else "video")
        self.worker.start()
        if self.uploader:
            self.uploader.start()
        if self.preview:
            self.preview.start()
            log.info("live preview: http://%s:%d/stream.mjpg", self.cfg.preview.host, self.cfg.preview.port)
        tick = last_report = time.monotonic()
        try:
            while max_frames is None or self.frames < max_frames:
                item = src.read()
                if item is None:
                    break
                frame, t = item
                self.step(frame, t)
                self.frames += 1
                now = time.monotonic()
                self.fps = 0.9 * self.fps + 0.1 * (1.0 / max(now - tick, 1e-6)) if self.frames > 1 else 0.0
                tick = now
                if progress and now - last_report >= 1.0:
                    last_report = now
                    progress({**self.stats(), "position": src.index, "total": src.total})
                if self.show:
                    cv2.imshow("Litter watch", self.annotated)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        break
        except KeyboardInterrupt:
            log.info("stopping...")
        finally:
            src.release()
            self.shutdown()
            if progress:
                progress({**self.stats(), "position": src.index, "total": src.total, "finished": True})
        return self.stats()

    def step(self, frame: np.ndarray, t: float) -> None:
        self.last_t = t
        dets: list[Detection] = self.vehicles(frame) + self.people(frame)
        for d in dets:
            self.cache.observe(d, t, frame)

        for ev in self.litter.update(frame, t, [d.bbox for d in dets]):
            self._open_incident(ev, frame)

        litter_items = self.litter.overlay_items()
        entry = self.ring.add(t, frame, [(d.key, d.kind, d.cls_name, d.bbox) for d in dets], litter_items)
        for inc in self.pending:
            if not inc.frames or inc.frames[-1] is not entry:
                inc.frames.append(entry)
            if t >= inc.collect_until:
                inc.done = True
                self.worker.submit(inc)
        self.pending = [p for p in self.pending if not p.done]

        for rec in self.cache.expire(t):
            log.debug("binned %s (%s) - not involved in any incident", rec.key, rec.cls_name)

        self.annotated = self._draw(frame, dets, litter_items)
        if self.preview:
            self.preview.update(self.annotated, self.stats())
        if self.output_video:
            if self.writer is None:
                h, w = self.annotated.shape[:2]
                self.writer = cv2.VideoWriter(self.output_video, cv2.VideoWriter_fourcc(*"mp4v"), 25, (w, h))
            self.writer.write(self.annotated)

    def _open_incident(self, ev, frame: np.ndarray) -> None:
        cfg = self.cfg
        suspects = rank_suspects(self.cache, ev.bbox, ev.t_appear, cfg.attribution)
        if not suspects:
            self.unattributed += 1
            if not cfg.attribution.report_unattributed:
                log.info("litter at %s with nobody nearby - ignored", [round(v) for v in ev.bbox])
                return
        top = suspects[0] if suspects else None
        link_rec = linked_actor(self.cache, top) if top else None
        involved = [r for r in (top.record if top else None, link_rec) if r]
        if self._merge_into_recent(ev, involved):
            return
        offender = freeze_actor(top.record, top.score) if top else None
        linked = freeze_actor(link_rec, 0.0) if link_rec else None
        for a in (offender, linked):
            if a:
                self.cache.pin(a.record)
        inc = PendingIncident(
            uid=new_uid(cfg.camera.id),
            t_appear=ev.t_appear,
            t_confirm=ev.t_confirm,
            occurred_at=self.wall0 + timedelta(seconds=ev.t_appear),
            litter_box=ev.bbox,
            litter_crop=litter_crop(frame, ev.bbox),
            offender=offender,
            linked=linked,
            suspects=[{"track": s.record.key, "type": s.record.kind, "class": s.record.cls_name,
                       "score": s.score, "distance": s.distance} for s in suspects[:5]],
            frames=self.ring.since(ev.t_appear - cfg.evidence.pre_seconds),
            collect_until=ev.t_confirm + cfg.evidence.post_seconds,
        )
        self.pending.append(inc)
        self.recent.append(inc)
        self.incidents += 1
        log.info("LITTER confirmed at t=%.1fs (appeared %.1fs) -> %s", ev.t_confirm, ev.t_appear,
                 f"{offender.kind} {offender.key} (score {offender.score:.2f})" if offender else "no suspect")

    def _merge_into_recent(self, ev, involved: list) -> bool:
        """One dumping act often shows up as several pieces (bag, bottles, paper) confirmed
        a moment apart, sometimes pinned on the car and sometimes on the passenger who got
        out of it. Pieces involving the same people/vehicles, or lying right next to litter
        that was just reported, within merge_seconds belong to the incident already opened."""
        window = self.cfg.litter.merge_seconds
        actors = {r.key for r in involved}
        self.recent = [r for r in self.recent if ev.t_confirm - r.t_confirm <= window]
        for inc in reversed(self.recent):
            inc_actors = {a.key for a in (inc.offender, inc.linked) if a}
            same_actors = bool(actors & inc_actors)
            near = any(_near(ev.bbox, b) for b in [inc.litter_box, *inc.more_litter])
            if not (same_actors or (near and (not actors or not inc_actors))):
                continue
            if not inc.done:
                inc.more_litter.append(ev.bbox)
                # e.g. the car was blamed first and now its passenger is seen dumping: keep both
                extra = next((r for r in involved if r.key not in inc_actors and inc.offender
                              and r.kind != inc.offender.kind), None)
                if extra and inc.linked is None:
                    inc.linked = freeze_actor(extra, 0.0)
                    self.cache.pin(extra)
            log.info("LITTER at t=%.1fs merged into incident %s (same dumping act)", ev.t_confirm, inc.uid)
            return True
        return False

    # ----------------------------------------------------------------- drawing
    def _draw(self, frame: np.ndarray, dets: list[Detection], litter_items) -> np.ndarray:
        img = frame.copy()
        h, w = img.shape[:2]
        pts = self.zone.points(w, h)
        overlay = img.copy()
        cv2.fillPoly(overlay, [pts], (80, 160, 60))
        img = cv2.addWeighted(overlay, 0.12, img, 0.88, 0)
        cv2.polylines(img, [pts], True, (80, 200, 80), 2)
        pinned = {k for k, r in self.cache.records.items() if r.pinned}
        for d in dets:
            x1, y1, x2, y2 = (int(v) for v in d.bbox)
            color = COLORS["offender"] if d.key in pinned else COLORS[d.kind]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            tag = f"{d.key} {d.cls_name}" + (" OFFENDER" if d.key in pinned else "")
            cv2.putText(img, tag, (x1 + 2, max(14, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
        for box, progress, confirmed in litter_items:
            x1, y1, x2, y2 = (int(v) for v in box)
            color = COLORS["offender"] if confirmed else COLORS["litter"]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            bar = int((x2 - x1) * progress)
            cv2.rectangle(img, (x1, y2 + 3), (x1 + bar, y2 + 7), color, -1)
            cv2.putText(img, "LITTER" if confirmed else "watching", (x1, max(12, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        s = self.stats()
        hud = (f"{self.cfg.camera.id} | {s['fps']:.1f} fps | cache {s['cache_active']} "
               f"(pushed {s['cache_pushed']}, binned {s['cache_binned']}) | incidents {s['incidents']}"
               + (" | learning background..." if s["warming_up"] else ""))
        cv2.rectangle(img, (0, 0), (w, 26), (20, 20, 20), -1)
        cv2.putText(img, hud, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        return img

    # ---------------------------------------------------------------- shutdown
    def shutdown(self) -> None:
        for inc in self.pending:          # stream ended before post_seconds elapsed
            self.worker.submit(inc)
        self.pending = []
        self.worker.stop()
        self.worker.join()
        if self.uploader:
            self.uploader.stop()
            self.uploader.join(timeout=5)
            self.uploader.flush()
        if self.writer:
            self.writer.release()
        if self.preview:
            self.preview.stop()
        if self.show:
            cv2.destroyAllWindows()
        log.info("done: %s", self.stats())


def _near(a, b) -> bool:
    """Boxes touch once each is grown by its own size (fragments of one pile)."""
    from .geometry import expand, intersection
    return intersection(expand(a, 1.0), expand(b, 1.0)) > 0
