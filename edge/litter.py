"""Litter detection without a trained trash model ("new static object" method).

Idea: litter is something that was not in the clean area before, appears right
after a person or vehicle passes, and then stays put. So we

1. keep a background image of the empty zone (slowly updated, never under
   people/vehicles or under things we are currently watching),
2. find pixels that differ from that background inside the zone,
3. ignore anything covered by a (slightly enlarged) person/vehicle box,
4. follow the remaining blobs over time; a blob that stays in the same place for
   ``static_seconds`` while nobody is standing on it is reported as litter.

Everything runs on a downscaled copy of the frame (``work_width``) for speed;
results are mapped back to full-resolution coordinates.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import LitterCfg
from .geometry import Box, Zone, expand, intersection, iou, scale

_ids = itertools.count(1)


@dataclass
class Candidate:
    id: int
    bbox: Box                  # work-resolution coordinates
    origin: tuple[float, float]
    first_seen: float
    last_seen: float
    visible_time: float = 0.0  # time spent visible and not under a person/vehicle
    confirmed: bool = False
    confirmed_at: float | None = None

    def progress(self, static_seconds: float) -> float:
        return 1.0 if self.confirmed else min(1.0, self.visible_time / static_seconds)


@dataclass
class LitterEvent:
    candidate_id: int
    t_appear: float
    t_confirm: float
    bbox: Box                  # full-resolution coordinates
    area_px: float
    extra: dict = field(default_factory=dict)


class LitterDetector:
    def __init__(self, cfg: LitterCfg, zone: Zone):
        self.cfg = cfg
        self.zone = zone
        self.bg: np.ndarray | None = None
        self.start_t: float | None = None
        self.prev_t: float | None = None
        self.scale = 1.0
        self.candidates: dict[int, Candidate] = {}
        self.recent_actors: list[tuple[float, Box]] = []   # work-res boxes
        self.last_fg: np.ndarray | None = None
        self.resets = 0

    # -- helpers -----------------------------------------------------------------
    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        self.scale = min(1.0, self.cfg.work_width / w)
        small = cv2.resize(frame, (int(w * self.scale), int(h * self.scale)), interpolation=cv2.INTER_AREA) \
            if self.scale < 1.0 else frame
        return cv2.GaussianBlur(small, (5, 5), 0).astype(np.float32)

    def _actor_mask(self, shape: tuple[int, int], boxes: list[Box]) -> np.ndarray:
        h, w = shape
        mask = np.zeros((h, w), np.uint8)
        for b in boxes:
            x1, y1, x2, y2 = (int(round(v)) for v in expand(b, self.cfg.actor_margin, w, h))
            mask[y1:y2, x1:x2] = 255
        return mask

    def _reset_background(self, small: np.ndarray) -> None:
        self.bg = small.copy()
        self.candidates.clear()
        self.resets += 1

    @property
    def warming_up(self) -> bool:
        return self.start_t is None or self.prev_t is None or \
            self.prev_t - self.start_t < self.cfg.warmup_seconds

    # -- main entry ----------------------------------------------------------------
    def update(self, frame: np.ndarray, t: float, actor_boxes: list[Box]) -> list[LitterEvent]:
        small = self._prepare(frame)
        h, w = small.shape[:2]
        dt = 0.0 if self.prev_t is None else max(0.0, t - self.prev_t)
        self.prev_t = t
        boxes = [scale(b, self.scale) for b in actor_boxes]
        self.recent_actors = [(ts, b) for ts, b in self.recent_actors if t - ts <= self.cfg.actor_memory_seconds]
        self.recent_actors += [(t, b) for b in boxes]
        actor_mask = self._actor_mask((h, w), boxes)
        zone_mask = self.zone.mask(w, h)

        if self.bg is None:
            self.start_t = t
            self.bg = small.copy()
            return []

        if self.warming_up:
            # Learn the empty scene quickly, except where people/vehicles stand.
            cv2.accumulateWeighted(small, self.bg, 0.2, mask=cv2.bitwise_not(actor_mask))
            return []

        diff = cv2.absdiff(small, self.bg).max(axis=2)
        fg = np.where(diff > self.cfg.diff_threshold, 255, 0).astype(np.uint8)
        fg = cv2.bitwise_and(fg, zone_mask)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))

        zone_area = max(1, int(cv2.countNonZero(zone_mask)))
        free = cv2.bitwise_and(fg, cv2.bitwise_not(actor_mask))
        if cv2.countNonZero(free) / zone_area > self.cfg.global_change_ratio:
            # Lights switched on, cloud passed, camera nudged... start over.
            self._reset_background(small)
            return []
        self.last_fg = free

        blobs = self._blobs(free, zone_area)
        events = self._track(blobs, t, dt, actor_mask)

        # Update the background everywhere except actors and watched blobs.
        keep = actor_mask.copy()
        for c in self.candidates.values():
            x1, y1, x2, y2 = (int(round(v)) for v in expand(c.bbox, 0.3, w, h))
            keep[y1:y2, x1:x2] = 255
        cv2.accumulateWeighted(small, self.bg, self.cfg.bg_learning_rate, mask=cv2.bitwise_not(keep))
        self._absorb_old(small, t)
        return events

    def _blobs(self, fg: np.ndarray, zone_area: int) -> list[tuple[Box, float]]:
        n, _, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
        out = []
        max_area = self.cfg.max_area_ratio * fg.shape[0] * fg.shape[1]
        for i in range(1, n):
            x, y, bw, bh, a = stats[i]
            if a < self.cfg.min_area_px or a > max_area:
                continue
            out.append(((float(x), float(y), float(x + bw), float(y + bh)), float(a)))
        return out

    def _match(self, box: Box, taken: set[int]) -> Candidate | None:
        """Same object as an existing candidate? Overlap, or a partial view of it."""
        best, best_score = None, 0.0
        for c in self.candidates.values():
            if c.id in taken:
                continue
            score = iou(box, c.bbox)
            # Half hidden behind a passer-by: the visible part is smaller but sits on top of it.
            if _inside(_center(box), expand(c.bbox, 0.5)) or _inside(_center(c.bbox), expand(box, 0.5)):
                score = max(score, 0.3)
            if c.confirmed and score > 0:
                score += 0.2
            if score > best_score:
                best, best_score = c, score
        return best if best_score >= 0.25 else None

    def _covered_by_actor(self, box: Box) -> bool:
        a = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        return any(intersection(box, b) / a > 0.5 for _, b in self.recent_actors)

    def _track(self, blobs: list[tuple[Box, float]], t: float, dt: float, actor_mask: np.ndarray) -> list[LitterEvent]:
        events: list[LitterEvent] = []
        matched: set[int] = set()
        for box, a in blobs:
            best = self._match(box, matched)
            if best is None:
                c = Candidate(next(_ids), box, _center(box), t, t)
                self.candidates[c.id] = c
                matched.add(c.id)
                continue
            matched.add(best.id)
            best.last_seen = t
            if best.confirmed:
                continue  # already reported; a partial view must not re-trigger or move it
            best.bbox = box
            drift = math.dist(_center(box), best.origin)
            if drift > self.cfg.max_drift_px:
                # It moved: still flying / rolling / being carried. Restart the clock here.
                best.origin = _center(box)
                best.first_seen = t
                best.visible_time = 0.0
                continue
            if not self._covered_by_actor(box):
                best.visible_time += dt
            if not best.confirmed and best.visible_time >= self.cfg.static_seconds:
                best.confirmed = True
                best.confirmed_at = t
                inv = 1.0 / self.scale
                events.append(LitterEvent(best.id, best.first_seen, t, scale(box, inv), a * inv * inv))

        h, w = actor_mask.shape
        for cid, c in list(self.candidates.items()):
            if cid in matched:
                continue
            x1, y1, x2, y2 = (int(round(v)) for v in c.bbox)
            region = actor_mask[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
            occluded = region.size > 0 and region.mean() > 127
            tolerance = self.cfg.confirmed_miss_seconds if c.confirmed else self.cfg.miss_tolerance_seconds
            if occluded:
                c.last_seen = t  # someone is standing in front of it; keep waiting
            elif t - c.last_seen > tolerance:
                del self.candidates[cid]  # gone (picked up / was never really there)
        return events

    def _absorb_old(self, small: np.ndarray, t: float) -> None:
        """After a while, confirmed litter becomes part of the background (no repeat alerts)."""
        h, w = small.shape[:2]
        for cid, c in list(self.candidates.items()):
            if c.confirmed and c.confirmed_at is not None and t - c.confirmed_at > self.cfg.absorb_after_seconds:
                x1, y1, x2, y2 = (int(round(v)) for v in expand(c.bbox, 0.3, w, h))
                self.bg[y1:y2, x1:x2] = small[y1:y2, x1:x2]
                del self.candidates[cid]

    def overlay_items(self) -> list[tuple[Box, float, bool]]:
        """(full-res box, progress 0..1, confirmed) for drawing."""
        inv = 1.0 / self.scale
        return [(scale(c.bbox, inv), c.progress(self.cfg.static_seconds), c.confirmed)
                for c in self.candidates.values()]


def _inside(p: tuple[float, float], b: Box) -> bool:
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def _center(b: Box) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
