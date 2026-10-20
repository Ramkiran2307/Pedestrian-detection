"""Small box / polygon helpers shared by the edge modules.

Boxes are always (x1, y1, x2, y2) in pixels of the full-resolution frame.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

Box = tuple[float, float, float, float]


def area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def center(b: Box) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def diagonal(b: Box) -> float:
    return math.hypot(b[2] - b[0], b[3] - b[1])


def intersection(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def expand(b: Box, frac: float, width: int | None = None, height: int | None = None) -> Box:
    dx = (b[2] - b[0]) * frac
    dy = (b[3] - b[1]) * frac
    out = (b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy)
    return clip(out, width, height) if width and height else out


def clip(b: Box, width: int, height: int) -> Box:
    return (max(0.0, min(b[0], width)), max(0.0, min(b[1], height)),
            max(0.0, min(b[2], width)), max(0.0, min(b[3], height)))


def scale(b: Box, s: float) -> Box:
    return (b[0] * s, b[1] * s, b[2] * s, b[3] * s)


def gap_to_box(b: Box, px: float, py: float) -> float:
    """Distance from a point to the nearest edge of a box (0 if inside)."""
    dx = max(b[0] - px, 0.0, px - b[2])
    dy = max(b[1] - py, 0.0, py - b[3])
    return math.hypot(dx, dy)


def crop(frame: np.ndarray, b: Box, pad: float = 0.0) -> np.ndarray:
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in expand(b, pad, w, h))
    if x2 <= x1 or y2 <= y1:
        return np.zeros((0, 0, 3), dtype=frame.dtype)
    return frame[y1:y2, x1:x2].copy()


class Zone:
    """The 'clean area' polygon. Stored normalised (0..1) so it fits any resolution."""

    def __init__(self, name: str, polygon: list[list[float]]):
        self.name = name
        self.polygon = [tuple(map(float, p)) for p in polygon] if len(polygon) >= 3 else []
        self._mask_cache: dict[tuple[int, int], np.ndarray] = {}

    def points(self, width: int, height: int) -> np.ndarray:
        if not self.polygon:
            return np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.int32)
        return np.array([[round(x * (width - 1)), round(y * (height - 1))] for x, y in self.polygon], np.int32)

    def mask(self, width: int, height: int) -> np.ndarray:
        key = (width, height)
        if key not in self._mask_cache:
            m = np.zeros((height, width), np.uint8)
            cv2.fillPoly(m, [self.points(width, height)], 255)
            self._mask_cache[key] = m
        return self._mask_cache[key]

    def contains(self, x: float, y: float, width: int, height: int) -> bool:
        if not self.polygon:
            return True
        return cv2.pointPolygonTest(self.points(width, height).astype(np.float32), (float(x), float(y)), False) >= 0
