import numpy as np

from edge.config import LitterCfg
from edge.geometry import Zone
from edge.litter import LitterDetector

H, W, FPS = 360, 640, 10


def scene(rng):
    base = np.full((H, W, 3), 110, np.uint8)
    return base, rng


def frame_with(base, rng, objects=(), person=None):
    f = base.copy()
    for (x, y) in objects:
        f[y:y + 14, x:x + 18] = (30, 30, 220)
    if person:
        x1, y1, x2, y2 = person
        f[y1:y2, x1:x2] = (200, 150, 90)
    noise = rng.normal(0, 2, f.shape).astype(np.int16)
    return np.clip(f.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def run(steps, cfg=None):
    rng = np.random.default_rng(1)
    base, _ = scene(rng)
    det = LitterDetector(cfg or LitterCfg(work_width=640, min_area_px=40), Zone("z", []))
    events = []
    for i, (objects, person) in enumerate(steps):
        boxes = [tuple(map(float, person))] if person else []
        events += [(i / FPS, e) for e in det.update(frame_with(base, rng, objects, person), i / FPS, boxes)]
    return events


def test_static_object_is_reported_once():
    steps = [((), None)] * 30                         # 3 s empty (warm-up)
    steps += [(((300, 300),), None)] * 60             # object lies there for 6 s
    events = run(steps)
    assert len(events) == 1
    t, e = events[0]
    assert 3.0 + 1.5 <= t <= 3.0 + 2.2                # confirmed ~static_seconds after appearing
    assert abs(e.t_appear - 3.0) < 0.2
    x1, y1, x2, y2 = e.bbox
    assert x1 <= 305 and x2 >= 312 and y1 <= 305 and y2 >= 310


def test_moving_person_is_ignored():
    steps = [((), None)] * 30
    for i in range(50):                                # a person box sweeps across
        x = 20 + i * 12
        steps.append(((), (x, 150, x + 40, 330)))
    steps += [((), None)] * 30
    assert run(steps) == []


def test_object_hidden_under_person_is_reported_after_they_leave():
    steps = [((), None)] * 30
    steps += [(((300, 300),), (280, 150, 340, 330))] * 20   # standing on top of it
    steps += [(((300, 300),), None)] * 30                   # walked away
    events = run(steps)
    assert len(events) == 1
    assert events[0][1].t_appear >= 5.0                     # only counted once visible


def test_object_that_disappears_quickly_is_not_reported():
    steps = [((), None)] * 30
    steps += [(((300, 300),), None)] * 8                     # 0.8 s only
    steps += [((), None)] * 30
    assert run(steps) == []


def test_lighting_change_resets_instead_of_alerting():
    rng = np.random.default_rng(2)
    det = LitterDetector(LitterCfg(work_width=640), Zone("z", []))
    events = []
    for i in range(80):
        level = 110 if i < 40 else 170
        f = np.full((H, W, 3), level, np.uint8)
        events += det.update(f, i / FPS, [])
    assert events == [] and det.resets >= 1
