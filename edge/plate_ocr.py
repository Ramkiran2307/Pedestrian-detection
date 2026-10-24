"""Plate enhancement + plate-specialised OCR with Indian-format decoding.

Why this exists: from a CCTV camera a number plate is often only ~20-25 px tall and
blurry, so a general text reader (EasyOCR) gets little more than noise. Two things
make a big difference:

1. **Enhance using many frames.** The same plate is seen in many frames while the
   vehicle is in view. The sharpest crops are upscaled, aligned to each other (ECC
   image registration) and averaged: noise and blur that differ from frame to frame
   cancel out, the characters stay. The result is sharpened (unsharp mask) and
   contrast-normalised (CLAHE). This "fused" image is also saved as evidence so the
   officer sees a clearer plate.

2. **Read with plate OCR models, decode with the Indian format.** fast-plate-ocr's
   CCT models are trained on licence plates and output, for each of 10 character
   positions, a probability for every character. Those probabilities are averaged
   over several models, every selected frame, its enhanced version and the fused
   image. Then, instead of taking the most likely character in each position on its
   own, we pick the most likely string that *is a valid Indian plate*:
   ``SS DD L(0-3) NNNN`` with a real state code, or ``YY BH NNNN L(1-2)``.
   A letter can't win in a digit position, so 8/B, 0/O, 1/I confusions are resolved
   by position automatically.

The reading is a suggestion: the dashboard requires an officer to confirm the plate
before a notice can be issued.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import cv2
import numpy as np

from .plates import STATE_CODES

log = logging.getLogger(__name__)

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"


def sharpness(img: np.ndarray) -> float:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# ----------------------------------------------------------------- enhancement
def enhance(img: np.ndarray, scale: float = 4.0) -> np.ndarray:
    """Single-image enhancement: upscale, denoise, unsharp mask, contrast (CLAHE)."""
    big = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
    big = cv2.bilateralFilter(big, 5, 30, 5)
    return _sharpen_contrast(big)


def _sharpen_contrast(img: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(img, (0, 0), 2.0)
    img = cv2.addWeighted(img, 1.8, blur, -0.8, 0)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def fuse(crops: list[np.ndarray], scale: int = 4) -> tuple[np.ndarray, int]:
    """Multi-frame fusion: align every crop to the sharpest one and average them.

    Returns the fused, sharpened image and how many frames were combined."""
    crops = sorted(crops, key=sharpness, reverse=True)
    h, w = (int(v) for v in np.median([c.shape[:2] for c in crops], axis=0))
    size = (max(8, w * scale), max(4, h * scale))
    ups = [cv2.resize(c, size, interpolation=cv2.INTER_CUBIC).astype(np.float32) for c in crops]
    ref = cv2.cvtColor(ups[0], cv2.COLOR_BGR2GRAY)
    acc, used = ups[0].copy(), 1
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-4)
    for up in ups[1:]:
        warp = np.eye(2, 3, dtype=np.float32)
        try:
            cc, warp = cv2.findTransformECC(ref, cv2.cvtColor(up, cv2.COLOR_BGR2GRAY), warp,
                                            cv2.MOTION_AFFINE, criteria, None, 5)
        except cv2.error:
            continue  # could not align (e.g. very different view): leave it out
        if cc < 0.6:
            continue
        acc += cv2.warpAffine(up, warp, size, flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)
        used += 1
    fused = (acc / used).clip(0, 255).astype(np.uint8)
    return _sharpen_contrast(fused), used


def tight_crop(img: np.ndarray) -> np.ndarray:
    """Trim the detector's padding down to the plate itself (the bright rectangle).
    The plate OCR models are trained on tightly cropped plates."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img
    x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
    H, W = gray.shape
    # only trust it if it looks like a plate: most of the crop, wider than tall
    if w * h < 0.35 * W * H or w < 1.8 * h:
        return img
    px, py = max(1, w // 40), max(1, h // 12)
    return img[max(0, y - py):min(H, y + h + py), max(0, x - px):min(W, x + w + px)]


# ------------------------------------------------------------------ decoding
# How common each layout is on Indian roads (rough prior). Without it a long plate
# that the model half-reads tends to decode as a rare short format.
FORMAT_PRIOR = {
    "LLDDLLDDDD": 0.55,   # KA 01 AB 1234  (most plates)
    "LLDDLDDDD": 0.20,    # MH 12 A 1234
    "LLDLLDDDD": 0.06,    # DL 3 CA 1234   (Delhi)
    "LLDLDDDD": 0.03,     # DL 3 C 1234
    "LLDDLLLDDDD": 0.02,  # three-letter series (too long for 10 slots, kept for completeness)
    "LLDLLLDDDD": 0.02,
    "LLDDDDDD": 0.02,     # old plates without series letters
    "LLDDDDD": 0.01,
    "DDLLDDDDLL": 0.05,   # 22 BH 1234 AA
    "DDLLDDDDL": 0.02,    # 22 BH 1234 A
}


def _templates(slots: int):
    for dl in (2, 1):
        for sl in (2, 1, 3, 0):
            t = "LL" + "D" * dl + "L" * sl + "DDDD"
            if len(t) <= slots:
                yield t, "standard"
    for sl in (2, 1):
        t = "DDLLDDDD" + "L" * sl
        if len(t) <= slots:
            yield t, "bh"


@dataclass
class Decoded:
    text: str
    fmt: str
    confidence: float        # geometric-mean probability per position (0..1)
    char_conf: list[float] = field(default_factory=list)


def constrained_decode(P: np.ndarray, alphabet: str, pad: str = "_", top: int = 3) -> list[Decoded]:
    """Most likely valid Indian plates for a (slots x alphabet) probability matrix."""
    slots = P.shape[0]
    L = np.log(np.clip(P, 1e-6, 1.0))
    idx = {c: i for i, c in enumerate(alphabet)}
    pad_i = idx[pad]
    let = [idx[c] for c in LETTERS if c in idx]
    dig = [idx[c] for c in DIGITS if c in idx]
    states = sorted(STATE_CODES, key=lambda s: -(L[0, idx[s[0]]] + L[1, idx[s[1]]]))[:3]
    out: dict[str, Decoded] = {}
    for template, fmt in _templates(slots):
        heads = [list(s) for s in states] if fmt == "standard" else [[None, None]]
        for head in heads:
            chars, logp = [], []
            for i, kind in enumerate(template):
                if fmt == "standard" and i < 2:
                    j = idx[head[i]]
                elif fmt == "bh" and i in (2, 3):
                    j = idx["BH"[i - 2]]
                else:
                    pool = let if kind == "L" else dig
                    j = max(pool, key=lambda k: L[i, k])
                chars.append(alphabet[j])
                logp.append(L[i, j])
            logp += [L[i, pad_i] for i in range(len(template), slots)]
            text = "".join(chars)
            conf = float(np.exp(np.mean(logp)))
            rank = float(np.mean(logp)) + np.log(FORMAT_PRIOR.get(template, 0.01)) / slots
            if text not in out or rank > out[text][0]:
                out[text] = (rank, Decoded(text, fmt, conf, [round(float(np.exp(v)), 3) for v in logp[:len(template)]]))
    return [d for _, d in sorted(out.values(), key=lambda x: -x[0])][:top]


# -------------------------------------------------------------- recognizer
class PlateOCR:
    """Ensemble of fast-plate-ocr models (downloaded once, ~5 MB each, then cached)."""

    def __init__(self, model_names: list[str], device: str = "cpu"):
        from fast_plate_ocr import LicensePlateRecognizer
        from fast_plate_ocr.inference.plate_recognizer import _load_image_from_source, preprocess_image

        self._load_image, self._preprocess = _load_image_from_source, preprocess_image
        self.models = [LicensePlateRecognizer(n, device="cuda" if device not in ("cpu", "") else "cpu")
                       for n in model_names]
        cfg = self.models[0].config
        self.alphabet, self.pad, self.slots = cfg.alphabet, cfg.pad_char, cfg.max_plate_slots
        for m in self.models[1:]:
            if (m.config.alphabet, m.config.max_plate_slots) != (self.alphabet, self.slots):
                raise ValueError("plate OCR models in an ensemble must share alphabet and slot count")

    def per_image_probs(self, images: list[np.ndarray]) -> np.ndarray:
        """(N, slots, alphabet) probabilities per image, averaged over the models (BGR input)."""
        total = np.zeros((len(images), self.slots, len(self.alphabet)), np.float64)
        for m in self.models:
            rgb = m.config.image_color_mode == "rgb"
            batch = [cv2.cvtColor(i, cv2.COLOR_BGR2RGB) if rgb else cv2.cvtColor(i, cv2.COLOR_BGR2GRAY)
                     for i in images]
            x = self._preprocess(self._load_image(batch, m.config))
            out = m.model.run([m.plate_output_name], {"input": x})[0]
            total += out.reshape(len(images), self.slots, len(self.alphabet))
        return total / len(self.models)

    def probs(self, images: list[np.ndarray], weights: list[float] | None = None) -> np.ndarray:
        """Weighted average of per-position probabilities over all images."""
        w = np.asarray(weights or [1.0] * len(images), np.float64)
        return np.tensordot(w, self.per_image_probs(images), axes=1) / w.sum()

    def read(self, plate_crops: list[np.ndarray], max_frames: int = 12) -> "EnsembleRead":
        """Every frame and two fused images each 'vote' for their best valid Indian plate.

        Chosen by measuring on synthetic CCTV plates (tools/eval_plate_ocr.py): voting
        per image beats averaging characters, because a frame where the model skipped
        a character shifts all later positions; two fusions (12 and 6 sharpest frames)
        add views that are much cleaner than any single frame."""
        crops = [tight_crop(c) for c in sorted(plate_crops, key=sharpness, reverse=True)[:max_frames]]
        k = len(crops)
        if k > 1:
            fused, used = fuse(crops)
            fused_top = fuse(crops[:max(2, k // 2)])[0]
        else:
            fused, used, fused_top = enhance(crops[0]), 1, enhance(crops[0])
        images = crops + [fused, fused_top]
        weights = [1.0] * k + [max(1.0, k / 2), max(1.0, k / 3)]
        votes: dict[str, float] = {}
        best: dict[str, Decoded] = {}
        for P, w in zip(self.per_image_probs(images), weights):
            d = constrained_decode(P, self.alphabet, self.pad, top=1)[0]
            votes[d.text] = votes.get(d.text, 0.0) + w * d.confidence
            if d.text not in best or d.confidence > best[d.text].confidence:
                best[d.text] = d
        total = sum(votes.values()) or 1.0
        ranked = sorted(votes, key=votes.get, reverse=True)[:3]
        # confidence = how much of the (confidence-weighted) vote the reading got
        candidates = [Decoded(t, best[t].fmt, votes[t] / total, best[t].char_conf) for t in ranked]
        return EnsembleRead(candidates, fused, used, k)


@dataclass
class EnsembleRead:
    candidates: list[Decoded]     # confidence = share of the vote (0..1)
    enhanced: np.ndarray
    fused_frames: int
    frames: int
