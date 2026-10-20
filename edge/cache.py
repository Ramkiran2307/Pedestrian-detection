"""The short-term actor cache ("hold for a few seconds, then push or bin").

Every tracked person and vehicle gets a TrackRecord holding:
  * its recent trajectory (timestamped boxes), used to work out who was next to
    a piece of litter when it appeared, and
  * a handful of its best image crops, used as evidence (and, for vehicles, to
    read the number plate later).

A record lives while the track is visible and for ``hold_seconds`` after it was
last seen. If a littering incident is attributed to it in that window it is
*pushed* (its data goes into the incident). Otherwise it is *binned*: dropped
from memory with nothing written to disk, so passers-by leave no trace.
"""
from __future__ import annotations

import heapq
import itertools
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import CacheCfg
from .detectors import Detection
from .geometry import Box, area, crop

MAX_CROP_SIDE = 960
_tiebreak = itertools.count()


@dataclass
class Sample:
    t: float
    bbox: Box
    conf: float


@dataclass
class TrackRecord:
    key: str
    kind: str
    cls_name: str
    first_seen: float
    last_seen: float
    samples: deque[Sample] = field(default_factory=deque)
    # min-heap of (score, tiebreak, t, crop) so the worst crop is popped first
    crops: list = field(default_factory=list)
    observations: int = 0
    pinned: int = 0   # >0 while an incident that uses this record is being finalised
    pushed: bool = False

    def samples_between(self, t0: float, t1: float) -> list[Sample]:
        return [s for s in self.samples if t0 <= s.t <= t1]

    def bbox_near(self, t: float) -> Box | None:
        if not self.samples:
            return None
        return min(self.samples, key=lambda s: abs(s.t - t)).bbox

    def best_crops(self) -> list[np.ndarray]:
        return [c for _, _, _, c in sorted(self.crops, key=lambda x: -x[0])]

    def age_since_seen(self, now: float) -> float:
        return now - self.last_seen


def sharpness(img: np.ndarray) -> float:
    if img.size == 0:
        return 0.0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if gray.shape[0] > 160:
        gray = cv2.resize(gray, (int(gray.shape[1] * 160 / gray.shape[0]), 160))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class TrackCache:
    def __init__(self, cfg: CacheCfg):
        self.cfg = cfg
        self.records: dict[str, TrackRecord] = {}
        self.pushed_total = 0
        self.binned_total = 0

    def observe(self, det: Detection, t: float, frame: np.ndarray) -> TrackRecord:
        rec = self.records.get(det.key)
        if rec is None:
            rec = TrackRecord(det.key, det.kind, det.cls_name, t, t)
            self.records[det.key] = rec
        rec.last_seen = t
        rec.cls_name = det.cls_name
        rec.samples.append(Sample(t, det.bbox, det.conf))
        while rec.samples and rec.samples[0].t < t - self.cfg.history_seconds:
            rec.samples.popleft()
        if rec.observations % max(1, self.cfg.crop_every_n) == 0:
            self._consider_crop(rec, det, t, frame)
        rec.observations += 1
        return rec

    def _consider_crop(self, rec: TrackRecord, det: Detection, t: float, frame: np.ndarray) -> None:
        limit = self.cfg.vehicle_crops if rec.kind == "vehicle" else self.cfg.person_crops
        img = crop(frame, det.bbox, pad=0.06)
        if img.size == 0:
            return
        # Bigger and sharper crops make better evidence and better plate reads.
        score = area(det.bbox) * min(1.0, sharpness(img) / 150.0) * det.conf
        if len(rec.crops) >= limit and score <= rec.crops[0][0]:
            return
        h, w = img.shape[:2]
        if max(h, w) > MAX_CROP_SIDE:
            s = MAX_CROP_SIDE / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        item = (score, next(_tiebreak), t, img)
        if len(rec.crops) < limit:
            heapq.heappush(rec.crops, item)
        else:
            heapq.heapreplace(rec.crops, item)

    def active(self, t0: float, t1: float) -> list[TrackRecord]:
        """Records with at least one sample in [t0, t1]."""
        return [r for r in self.records.values() if r.samples and r.last_seen >= t0 and r.first_seen <= t1]

    def pin(self, rec: TrackRecord) -> None:
        rec.pinned += 1
        if not rec.pushed:
            rec.pushed = True
            self.pushed_total += 1

    def unpin(self, rec: TrackRecord) -> None:
        rec.pinned = max(0, rec.pinned - 1)

    def expire(self, now: float) -> list[TrackRecord]:
        """Remove records not seen for hold_seconds. Returns the ones that were binned."""
        binned = []
        for key, rec in list(self.records.items()):
            if rec.pinned or now - rec.last_seen <= self.cfg.hold_seconds:
                continue
            del self.records[key]
            if not rec.pushed:
                binned.append(rec)
        self.binned_total += len(binned)
        return binned
