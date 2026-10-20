"""Generate a synthetic test video for the litter pipeline (no real footage needed).

Scenario (25 fps, 1280x720, ~26 s):
  0-2 s    empty street (the pipeline learns the background)
  2-10 s   person A walks left->right and drops a wrapper at ~5 s   -> should be reported
  9-16 s   person B walks right->left and drops nothing             -> should be binned
  14-22 s  a bus drives left->right; litter is thrown from it ~17 s -> should be reported (+plate)
  22-26 s  empty street

The people and the bus are cut out of the sample images that ship with the
ultralytics package, so the real YOLO models detect them.

    python tools/make_demo_video.py --out samples/demo_litter.mp4
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

W, H, FPS = 1280, 720, 25


def background(rng: np.random.Generator) -> np.ndarray:
    img = np.zeros((H, W, 3), np.uint8)
    for y in range(H):  # sky -> buildings -> road
        if y < 300:
            img[y] = (200 - y // 6, 170 - y // 8, 140 - y // 10)
        elif y < 380:
            img[y] = (95, 105, 120)
        else:
            img[y] = (92, 96, 98)
    for x in range(0, W, 160):  # building blocks
        cv2.rectangle(img, (x + 10, 120 + (x // 160 % 3) * 30), (x + 140, 380), (110 + x % 40, 120, 135), -1)
        for wy in range(150 + (x // 160 % 3) * 30, 360, 40):
            for wx in range(x + 25, x + 130, 35):
                cv2.rectangle(img, (wx, wy), (wx + 18, wy + 22), (170, 190, 200), -1)
    cv2.rectangle(img, (0, 380), (W, 400), (150, 150, 150), -1)  # kerb
    texture = rng.normal(0, 6, (H - 400, W, 1)).astype(np.int16)
    img[400:] = np.clip(img[400:].astype(np.int16) + texture, 0, 255).astype(np.uint8)
    for x in range(0, W, 120):  # lane dashes
        cv2.rectangle(img, (x, 560), (x + 60, 566), (210, 210, 210), -1)
    return img


def wrapper(rng: np.random.Generator, color) -> np.ndarray:
    """A crumpled snack wrapper / plastic bag shape with an alpha mask."""
    patch = np.zeros((26, 34, 4), np.uint8)
    pts = np.array([[2, 12], [9, 2], [20, 4], [31, 9], [29, 20], [18, 24], [6, 22]], np.int32)
    pts += rng.integers(-1, 2, pts.shape).astype(np.int32)
    cv2.fillPoly(patch, [pts], (*color, 255))
    cv2.line(patch, (8, 8), (26, 18), (255, 255, 255, 255), 2)
    return patch


def paste(frame: np.ndarray, sprite: np.ndarray, x: int, y: int) -> None:
    h, w = sprite.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sx0, sy0 = x0 - x, y0 - y
    region = sprite[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    if region.shape[2] == 4:
        a = region[:, :, 3:4].astype(np.float32) / 255.0
        frame[y0:y1, x0:x1] = (region[:, :, :3] * a + frame[y0:y1, x0:x1] * (1 - a)).astype(np.uint8)
    else:
        frame[y0:y1, x0:x1] = region


def add_plate(bus: np.ndarray, text: str) -> np.ndarray:
    h, w = bus.shape[:2]
    pw, ph = int(w * 0.42), int(h * 0.085)
    x, y = (w - pw) // 2, int(h * 0.80)
    cv2.rectangle(bus, (x, y), (x + pw, y + ph), (245, 245, 245), -1)
    cv2.rectangle(bus, (x, y), (x + pw, y + ph), (20, 20, 20), 2)
    scale = ph / 30.0
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
    scale *= min(1.0, (pw * 0.9) / tw)   # fit the text inside the plate
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
    cv2.putText(bus, text, (x + (pw - tw) // 2, y + (ph + th) // 2), cv2.FONT_HERSHEY_DUPLEX, scale,
                (15, 15, 15), 2, cv2.LINE_AA)
    return bus


def main() -> None:
    from ultralytics.utils import ASSETS

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="samples/demo_litter.mp4")
    ap.add_argument("--plate", default="KA 05 MN 4321")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    src = cv2.imread(str(ASSETS / "bus.jpg"))
    person_a = cv2.resize(src[400:902, 48:247], None, fx=0.6, fy=0.6)     # ~120x300
    person_b = cv2.resize(src[394:880, 668:809], None, fx=0.62, fy=0.62)
    bus = cv2.resize(src[230:738, 344:668], None, fx=1.15, fy=1.15)         # bus front, no people
    bus = add_plate(bus, args.plate)
    bg = background(rng)
    litter_a = wrapper(rng, (40, 40, 220))
    litter_b = wrapper(rng, (30, 200, 230))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    total = 26 * FPS
    dropped: list[tuple[np.ndarray, int, int]] = []
    drop_a_done = drop_bus_done = False
    for i in range(total):
        t = i / FPS
        frame = bg.copy()
        for sprite, x, y in dropped:
            paste(frame, sprite, x, y)

        if 2.0 <= t <= 10.0:   # person A, left -> right, feet at y=660
            ph, pw = person_a.shape[:2]
            x = int(-pw + (t - 2.0) / 8.0 * (W + pw))
            paste(frame, person_a, x, 660 - ph)
            if t >= 5.0 and not drop_a_done:
                dropped.append((litter_a, x + pw // 2 - 40, 668))
                drop_a_done = True
        if 9.0 <= t <= 16.0:   # person B, right -> left, feet at y=700, no littering
            ph, pw = person_b.shape[:2]
            x = int(W - (t - 9.0) / 7.0 * (W + pw))
            paste(frame, person_b, x, 700 - ph)
        if 14.0 <= t <= 22.0:  # bus, left -> right, bottom at y=690
            bh, bw = bus.shape[:2]
            x = int(-bw + (t - 14.0) / 8.0 * (W + bw))
            paste(frame, bus, x, 690 - bh)
            if t >= 17.0 and not drop_bus_done:
                dropped.append((litter_b, x - 50, 668))
                drop_bus_done = True

        noise = rng.normal(0, 2.5, frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        out.write(frame)
    out.release()
    print(f"wrote {args.out} ({total} frames, {total / FPS:.0f} s)")


if __name__ == "__main__":
    main()
