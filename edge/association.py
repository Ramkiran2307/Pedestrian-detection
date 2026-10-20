"""Attribution: who dropped the litter?

When the litter detector confirms a new static object that first became visible
at ``t_appear``, we look at every cached person/vehicle seen during the
``lookback`` window before it and measure how close each one came to the spot.
Distances are measured from the litter's centre to the edge of the actor's box,
divided by the box size, so "half a body away" means the same thing near and
far from the camera.

The closest, most recent actor within the allowed distance is the offender.
A person and a vehicle that overlap at that moment (a rider on a bike, a driver
in a car) are linked so the incident gets both the person snapshot and the
number plate.
"""
from __future__ import annotations

from dataclasses import dataclass

from .cache import TrackCache, TrackRecord
from .config import AttributionCfg
from .geometry import Box, center, diagonal, gap_to_box, intersection, area


@dataclass
class Suspect:
    record: TrackRecord
    score: float          # 0..1, higher = more likely
    distance: float       # normalised gap, 0 = litter was under/inside the box
    t_closest: float


def rank_suspects(cache: TrackCache, litter_box: Box, t_appear: float, cfg: AttributionCfg) -> list[Suspect]:
    lx, ly = center(litter_box)
    t0, t1 = t_appear - cfg.lookback_seconds, t_appear + 0.5
    out: list[Suspect] = []
    for rec in cache.active(t0, t1):
        limit = cfg.vehicle_max_distance if rec.kind == "vehicle" else cfg.person_max_distance
        best = None
        for s in rec.samples_between(t0, t1):
            # Vehicles: use the longer side (throwing from a window can land a bit away).
            size = max(s.bbox[2] - s.bbox[0], s.bbox[3] - s.bbox[1]) if rec.kind == "vehicle" else diagonal(s.bbox)
            d = gap_to_box(s.bbox, lx, ly) / max(size, 1.0)
            if best is None or d < best[0] or (d == best[0] and s.t > best[1]):
                best = (d, s.t)
        if best is None or best[0] > limit:
            continue
        d, ts = best
        closeness = 1.0 - d / limit
        recency = 1.0 - min(1.0, abs(t_appear - ts) / max(cfg.lookback_seconds, 1e-6))
        out.append(Suspect(rec, round(0.7 * closeness + 0.3 * recency, 4), round(d, 4), ts))
    out.sort(key=lambda s: s.score, reverse=True)
    return out


def linked_actor(cache: TrackCache, primary: Suspect) -> TrackRecord | None:
    """Find the vehicle a person was riding/driving, or the rider of a vehicle."""
    want = "vehicle" if primary.record.kind == "person" else "person"
    pbox = primary.record.bbox_near(primary.t_closest)
    if pbox is None:
        return None
    best, best_overlap = None, 0.0
    for rec in cache.active(primary.t_closest - 0.5, primary.t_closest + 0.5):
        if rec.kind != want:
            continue
        box = rec.bbox_near(primary.t_closest)
        if box is None:
            continue
        person_box = pbox if want == "vehicle" else box
        overlap = intersection(pbox, box) / max(1.0, area(person_box))
        if overlap > best_overlap:
            best, best_overlap = rec, overlap
    return best if best_overlap >= 0.3 else None
