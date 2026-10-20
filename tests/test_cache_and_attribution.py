import numpy as np

from edge.association import linked_actor, rank_suspects
from edge.cache import TrackCache
from edge.config import AttributionCfg, CacheCfg
from edge.detectors import Detection

FRAME = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)


def det(key, kind, box, cls=None):
    return Detection(key, kind, cls or ("person" if kind == "person" else "car"), box, 0.9)


def test_records_are_binned_after_hold_seconds():
    cache = TrackCache(CacheCfg(hold_seconds=4.0))
    cache.observe(det("P1", "person", (100, 100, 200, 400)), 0.0, FRAME)
    assert cache.expire(3.9) == []
    binned = cache.expire(4.1)
    assert [r.key for r in binned] == ["P1"]
    assert cache.binned_total == 1 and "P1" not in cache.records


def test_pinned_records_are_pushed_not_binned():
    cache = TrackCache(CacheCfg(hold_seconds=1.0))
    rec = cache.observe(det("P1", "person", (100, 100, 200, 400)), 0.0, FRAME)
    cache.pin(rec)
    assert cache.expire(10.0) == [] and "P1" in cache.records
    cache.unpin(rec)
    assert cache.expire(10.0) == []          # pushed records are never counted as binned
    assert cache.pushed_total == 1 and cache.binned_total == 0


def test_keeps_only_the_best_crops():
    cache = TrackCache(CacheCfg(person_crops=2, crop_every_n=1))
    for i, h in enumerate([100, 300, 200, 50]):
        cache.observe(det("P1", "person", (100, 100, 160, 100 + h)), i * 0.1, FRAME)
    crops = cache.records["P1"].best_crops()
    assert len(crops) == 2
    assert crops[0].shape[0] > crops[1].shape[0]   # largest first


def test_nearest_recent_actor_is_blamed():
    cache = TrackCache(CacheCfg())
    cfg = AttributionCfg()
    for i in range(20):  # dropper walks right past the litter spot at x=500
        t = i * 0.1
        x = 400 + i * 20
        cache.observe(det("P1", "person", (x, 300, x + 100, 600)), t, FRAME)
        # bystander far away on the other side of the frame
        cache.observe(det("P2", "person", (1100, 300, 1200, 600)), t, FRAME)
    suspects = rank_suspects(cache, (500, 610, 530, 630), t_appear=1.0, cfg=cfg)
    assert suspects and suspects[0].record.key == "P1"
    assert all(s.record.key != "P2" for s in suspects)


def test_nobody_close_means_no_suspect():
    cache = TrackCache(CacheCfg())
    cache.observe(det("P1", "person", (0, 0, 50, 120)), 0.0, FRAME)
    assert rank_suspects(cache, (1000, 650, 1020, 670), 0.2, AttributionCfg()) == []


def test_rider_is_linked_to_motorcycle():
    cache = TrackCache(CacheCfg())
    for i in range(5):
        cache.observe(det("P4", "person", (300, 300, 380, 520)), i * 0.1, FRAME)
        cache.observe(det("V2", "vehicle", (280, 380, 420, 600), cls="motorcycle"), i * 0.1, FRAME)
    s = rank_suspects(cache, (330, 610, 350, 625), 0.4, AttributionCfg())
    person = next(x for x in s if x.record.kind == "person")
    assert linked_actor(cache, person).key == "V2"
