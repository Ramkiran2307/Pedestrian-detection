"""Measure plate-reading accuracy on synthetic CCTV-quality Indian plates.

Each test plate is a random valid Indian number rendered like a real plate, then
degraded the way a CCTV camera degrades it: shrunk to a small size, blurred,
noisy, JPEG-compressed and jittering between frames (the vehicle moves). Every
plate gets several frames, like the cached crops of a tracked vehicle.

    python tools/eval_plate_ocr.py                    # new pipeline only (fast)
    python tools/eval_plate_ocr.py --easyocr          # also the old EasyOCR reader (slow)
    python tools/eval_plate_ocr.py --heights 16 20 28 --plates 40
"""
from __future__ import annotations

import argparse
import random
import string
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from edge.plates import STATE_CODES, normalize_indian_plate  # noqa: E402

STATES = sorted(STATE_CODES - {"OR", "UA", "DN"})


def random_plate(rng: random.Random) -> str:
    kind = rng.choices(["std2", "std1", "delhi", "bh"], weights=[60, 25, 8, 7])[0]
    num = f"{rng.randint(1, 9999):04d}"
    if kind == "bh":
        return f"{rng.randint(21, 26)}BH{num}{''.join(rng.choices(string.ascii_uppercase, k=rng.choice([1, 2])))}"
    if kind == "delhi":
        return f"DL{rng.randint(1, 9)}C{rng.choice(string.ascii_uppercase)}{num}"
    series = "".join(rng.choices(string.ascii_uppercase.replace("I", "").replace("O", ""), k=2 if kind == "std2" else 1))
    return f"{rng.choice(STATES)}{rng.randint(1, 99):02d}{series}{num}"


def _fit_font(d: ImageDraw.ImageDraw, text: str, font_path: str, max_w: int, max_h: int) -> ImageFont.FreeTypeFont:
    size = max_h
    while size > 10:
        font = ImageFont.truetype(font_path, size)
        l, t, r, b = d.textbbox((0, 0), text, font=font)
        if r - l <= max_w and b - t <= max_h:
            return font
        size -= 2
    return ImageFont.truetype(font_path, size)


def render(text: str, font_path: str, two_line: bool) -> np.ndarray:
    W, H = (300, 180) if two_line else (520, 115)
    img = Image.new("RGB", (W, H), (240, 240, 236))
    d = ImageDraw.Draw(img)
    d.rectangle([3, 3, W - 4, H - 4], outline=(20, 20, 20), width=5)
    d.text((10, H // 2), "IND", fill=(30, 60, 150), font=ImageFont.truetype(font_path, 16), anchor="lm")
    if two_line:   # bikes: "KA01" over "AB1234"
        lines = [text[:4], text[4:]]
        font = _fit_font(d, max(lines, key=len), font_path, W - 70, 70)
        for i, line in enumerate(lines):
            d.text((W // 2 + 14, 50 + i * 82), line, fill=(15, 15, 15), font=font, anchor="mm")
    else:
        font = _fit_font(d, text, font_path, W - 80, 86)
        d.text((W // 2 + 20, H // 2 + 3), text, fill=(15, 15, 15), font=font, anchor="mm")
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def degrade(plate: np.ndarray, height: int, rng: np.random.Generator) -> np.ndarray:
    h, w = plate.shape[:2]
    # perspective jitter (camera angle + vehicle movement between frames)
    j = 0.04
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = (src + rng.uniform(-j, j, (4, 2)) * [w, h]).astype(np.float32)
    pad = int(0.25 * h)
    canvas = cv2.copyMakeBorder(plate, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(55, 55, 60))
    M = cv2.getPerspectiveTransform((src + pad).astype(np.float32), (dst + pad).astype(np.float32))
    canvas = cv2.warpPerspective(canvas, M, (canvas.shape[1], canvas.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    # crop like the plate detector does (a little padding around the plate)
    ys, xs = int(pad - 0.15 * h), int(pad - 0.08 * w)
    canvas = canvas[max(0, ys):canvas.shape[0] - max(0, ys), max(0, xs):canvas.shape[1] - max(0, xs)]
    s = height / plate.shape[0]
    small = cv2.resize(cv2.GaussianBlur(canvas, (0, 0), rng.uniform(0.6, 1.6) / s), None, fx=s, fy=s,
                       interpolation=cv2.INTER_AREA)
    if rng.random() < 0.5:   # motion blur
        k = int(rng.integers(2, 4))
        kernel = np.zeros((k, k), np.float32); kernel[k // 2, :] = 1.0 / k
        small = cv2.filter2D(small, -1, kernel)
    small = np.clip(small.astype(np.float32) * rng.uniform(0.75, 1.1) + rng.normal(0, rng.uniform(3, 8), small.shape),
                    0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(35, 70))])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--heights", type=int, nargs="+", default=[14, 18, 24, 32], help="plate height in pixels")
    ap.add_argument("--plates", type=int, default=30, help="plates per height")
    ap.add_argument("--frames", type=int, default=10, help="frames (crops) per plate")
    ap.add_argument("--easyocr", action="store_true", help="also evaluate the old EasyOCR reader (slow)")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--auto", type=float, default=0.65, help="'high confidence' threshold to evaluate")
    ap.add_argument("--save", help="folder to save a few example frames")
    args = ap.parse_args()

    import matplotlib
    font = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans-Bold.ttf")
    from edge.plate_ocr import PlateOCR
    ocr = PlateOCR(["cct-s-v2-global-model", "cct-xs-v2-global-model"])
    old = None
    if args.easyocr:
        from edge.config import ModelCfg, OcrCfg
        from edge.plates import PlateReader
        old = PlateReader(ModelCfg(), OcrCfg(), "cpu")

    rng, nrng = random.Random(args.seed), np.random.default_rng(args.seed)
    print(f"{'height':>6} | {'new exact':>9} {'new chars':>9} {'high conf':>11} {'high ok':>7} | "
          f"{'single frame':>12}" + (f" | {'old EasyOCR':>11}" if old else ""))
    for height in args.heights:
        stats = dict(exact=0, chars=0.0, auto=0, auto_ok=0, single=0, old=0, t=0.0)
        for i in range(args.plates):
            truth = random_plate(rng)
            two_line = rng.random() < 0.25          # bikes and some cars have two-line plates
            plate = render(truth, font, two_line)
            frames = [degrade(plate, height if not two_line else int(height * 1.6), nrng) for _ in range(args.frames)]
            if args.save and i < 3:
                Path(args.save).mkdir(parents=True, exist_ok=True)
                cv2.imwrite(f"{args.save}/h{height}_{i}_{truth}.png", frames[0])
            t = time.time()
            res = ocr.read(frames)
            stats["t"] += time.time() - t
            best = res.candidates[0]
            stats["exact"] += best.text == truth
            stats["chars"] += sum(a == b for a, b in zip(best.text, truth)) / max(len(truth), len(best.text))
            if best.confidence >= args.auto:
                stats["auto"] += 1
                stats["auto_ok"] += best.text == truth
            single = ocr.read(frames[:1]).candidates[0].text
            stats["single"] += single == truth
            if old:
                stats["old"] += old.ocr_crops(frames).text == truth
        n = args.plates
        line = (f"{height:>4}px | {stats['exact'] / n:>9.0%} {stats['chars'] / n:>9.0%} {stats['auto'] / n:>11.0%} "
                f"{(stats['auto_ok'] / stats['auto'] if stats['auto'] else 0):>7.0%} | {stats['single'] / n:>12.0%}")
        if old:
            line += f" | {stats['old'] / n:>11.0%}"
        print(line + f"   ({stats['t'] / n:.2f}s per plate)")


if __name__ == "__main__":
    main()
