"""Number-plate reading tuned for Indian plates.

Steps for one vehicle:
1. Run models/license_plate.pt on several cached crops of the vehicle.
2. Upscale + contrast-enhance each plate crop and run EasyOCR (A-Z, 0-9 only).
3. Join OCR fragments in reading order (handles two-line bike plates).
4. Normalise to the Indian format, fixing typical OCR confusions by position
   (e.g. "0" in a letter slot becomes "O", "B" in a digit slot becomes "8").
5. Vote across all crops; the plate text with the highest confidence wins.

Formats accepted:
  * Standard:  SS DD L(0-3) NNNN   e.g. KA01AB1234, DL3CAB1234, MH12A1234
  * BH series: YY BH NNNN L(1-2)   e.g. 22BH1234AA
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import ModelCfg, OcrCfg

log = logging.getLogger(__name__)

STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CH", "CG", "DD", "DL", "DN", "GA", "GJ", "HP", "HR", "JH",
    "JK", "KA", "KL", "LA", "LD", "MH", "ML", "MN", "MP", "MZ", "NL", "OD", "OR", "PB", "PY",
    "RJ", "SK", "TN", "TR", "TS", "UK", "UA", "UP", "WB",
}
TO_DIGIT = {"O": "0", "D": "0", "Q": "0", "U": "0", "I": "1", "L": "1", "J": "1", "T": "7",
            "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4"}
TO_LETTER = {"0": "O", "1": "I", "2": "Z", "4": "A", "5": "S", "6": "G", "7": "T", "8": "B"}
ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
MAX_FIXES = 3


@dataclass
class PlateGuess:
    text: str
    valid: bool
    fixes: int = 0
    fmt: str | None = None   # "standard" | "bh" | None

    @property
    def pretty(self) -> str:
        """KA01AB1234 -> 'KA 01 AB 1234' for display."""
        m = re.fullmatch(r"([A-Z]{2})(\d{1,2})([A-Z]{0,3})(\d{4})", self.text)
        if self.fmt == "standard" and m:
            return " ".join(p for p in m.groups() if p)
        m = re.fullmatch(r"(\d{2})(BH)(\d{4})([A-Z]{1,2})", self.text)
        if self.fmt == "bh" and m:
            return " ".join(m.groups())
        return self.text


def _templates(n: int) -> list[tuple[str, str]]:
    out = []
    for dl in (2, 1):
        for sl in (2, 1, 3, 0):
            t = "L" * 2 + "D" * dl + "L" * sl + "D" * 4
            if len(t) == n:
                out.append((t, "standard"))
    for sl in (2, 1):
        t = "DDLLDDDD" + "L" * sl
        if len(t) == n:
            out.append((t, "bh"))
    return out


def _fit(text: str, template: str) -> tuple[str, int] | None:
    chars, fixes = [], 0
    for ch, slot in zip(text, template):
        if slot == "L":
            if ch.isalpha():
                chars.append(ch)
            elif ch in TO_LETTER:
                chars.append(TO_LETTER[ch]); fixes += 1
            else:
                return None
        else:
            if ch.isdigit():
                chars.append(ch)
            elif ch in TO_DIGIT:
                chars.append(TO_DIGIT[ch]); fixes += 1
            else:
                return None
    return "".join(chars), fixes


def normalize_indian_plate(raw: str) -> PlateGuess:
    text = re.sub(r"[^A-Z0-9]", "", raw.upper())
    variants = [(text, 0)]
    if text.startswith("IND"):            # the "IND" mark on high-security plates
        variants.append((text[3:], 0))
    if len(text) > 8:                     # one stray character at either end
        variants += [(text[1:], 1), (text[:-1], 1)]
    best: tuple[int, str, str] | None = None
    for candidate, penalty in variants:
        for template, fmt in _templates(len(candidate)):
            fitted = _fit(candidate, template)
            if fitted is None:
                continue
            value, fixes = fitted
            cost = fixes + penalty
            if fmt == "standard" and value[:2] not in STATE_CODES:
                cost += 2
            if fmt == "bh" and value[2:4] != "BH":
                cost += 2
            if best is None or cost < best[0]:
                best = (cost, value, fmt)
    if best is None or best[0] > MAX_FIXES:
        return PlateGuess(text, False)
    return PlateGuess(best[1], True, best[0], best[2])


def join_fragments(fragments: list[tuple[list, str, float]]) -> tuple[str, float]:
    """Order EasyOCR fragments top-to-bottom, then left-to-right, and join them."""
    if not fragments:
        return "", 0.0
    items = []
    for box, text, conf in fragments:
        ys = [p[1] for p in box]; xs = [p[0] for p in box]
        items.append(((min(ys) + max(ys)) / 2, min(xs), max(ys) - min(ys), text, conf))
    items.sort(key=lambda i: i[0])
    median_h = float(np.median([i[2] for i in items])) or 1.0
    lines: list[list] = [[items[0]]]
    for it in items[1:]:
        if it[0] - lines[-1][-1][0] > 0.6 * median_h:
            lines.append([it])
        else:
            lines[-1].append(it)
    text = "".join("".join(i[3] for i in sorted(line, key=lambda i: i[1])) for line in lines)
    conf = float(np.mean([i[4] for i in items]))
    return text, conf


@dataclass
class PlateResult:
    text: str | None = None
    pretty: str | None = None
    confidence: float = 0.0
    valid: bool = False
    raw: list[str] = field(default_factory=list)
    plate_image: np.ndarray | None = None
    detections: int = 0


class PlateReader:
    """Loaded lazily inside the evidence worker thread (EasyOCR is slow to start)."""

    def __init__(self, plate_cfg: ModelCfg, ocr_cfg: OcrCfg, device: str):
        self.plate_cfg = plate_cfg
        self.ocr_cfg = ocr_cfg
        self.device = device
        self._model = None
        self._reader = None

    def _load(self):
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self.plate_cfg.weights)
        if self._reader is None:
            import easyocr
            self._reader = easyocr.Reader(self.ocr_cfg.languages, gpu=self.ocr_cfg.gpu, verbose=False)

    def find_plate(self, vehicle_img: np.ndarray) -> tuple[np.ndarray, float] | None:
        self._load()
        r = self._model(vehicle_img, conf=self.plate_cfg.conf, imgsz=self.plate_cfg.imgsz,
                        device=self.device, verbose=False)[0]
        if r.boxes is None or len(r.boxes) == 0:
            return None
        i = int(r.boxes.conf.argmax())
        x1, y1, x2, y2 = r.boxes.xyxy[i].tolist()
        h, w = vehicle_img.shape[:2]
        px, py = (x2 - x1) * 0.06, (y2 - y1) * 0.12
        x1, y1 = int(max(0, x1 - px)), int(max(0, y1 - py))
        x2, y2 = int(min(w, x2 + px)), int(min(h, y2 + py))
        if x2 - x1 < 8 or y2 - y1 < 4:
            return None
        return vehicle_img[y1:y2, x1:x2].copy(), float(r.boxes.conf[i])

    def _variants(self, plate: np.ndarray) -> list[np.ndarray]:
        h = plate.shape[0]
        s = max(1.0, 96.0 / max(h, 1))
        big = cv2.resize(plate, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(big, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)
        return [big, clahe]

    def ocr(self, plate: np.ndarray) -> list[tuple[str, float]]:
        self._load()
        out = []
        for img in self._variants(plate):
            frags = self._reader.readtext(img, allowlist=ALNUM, detail=1, paragraph=False)
            frags = [f for f in frags if f[2] >= self.ocr_cfg.min_confidence]
            text, conf = join_fragments(frags)
            if text:
                out.append((text, conf))
        return out

    def read(self, vehicle_crops: list[np.ndarray]) -> PlateResult:
        result = PlateResult()
        votes: dict[str, float] = defaultdict(float)
        confs: dict[str, list[float]] = defaultdict(list)
        guesses: dict[str, PlateGuess] = {}
        best_plate, best_plate_conf = None, -1.0
        for crop in vehicle_crops:
            found = self.find_plate(crop)
            if found is None:
                continue
            plate, det_conf = found
            result.detections += 1
            if det_conf > best_plate_conf:
                best_plate, best_plate_conf = plate, det_conf
            for text, conf in self.ocr(plate):
                result.raw.append(text)
                g = normalize_indian_plate(text)
                votes[g.text] += conf * det_conf * (1.0 if g.valid else 0.35)
                confs[g.text].append(conf)
                if g.text not in guesses or (g.valid and not guesses[g.text].valid):
                    guesses[g.text] = g
        result.plate_image = best_plate
        if votes:
            winner = max(votes, key=votes.get)
            g = guesses[winner]
            share = votes[winner] / sum(votes.values())   # how much the reads agree
            mean_conf = float(np.mean(confs[winner]))
            result.text, result.pretty, result.valid = g.text, g.pretty, g.valid
            result.confidence = round(mean_conf * (0.5 + 0.5 * share) * (1.0 if g.valid else 0.5), 3)
        log.info("plate read: %s (valid=%s, conf=%.2f, raw=%s)", result.text, result.valid,
                 result.confidence, result.raw)
        return result
