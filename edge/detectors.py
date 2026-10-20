"""YOLO detection + ByteTrack tracking for the two actor types.

Model split (as agreed for the project):
  * vehicles -> models/yolov8n.pt (COCO classes car, motorcycle, bus, truck)
  * people   -> models/yolo26s.pt (COCO class person)

Each model keeps its own tracker, so track ids are prefixed ("V7", "P3") to keep
them unique across the two.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import ModelCfg
from .geometry import Box


@dataclass
class Detection:
    key: str          # "P3" / "V7" - unique per camera session
    kind: str         # "person" | "vehicle"
    cls_name: str     # "person", "car", "motorcycle", "bus", "truck"
    bbox: Box
    conf: float


class TrackedDetector:
    def __init__(self, kind: str, cfg: ModelCfg, device: str, tracker: str):
        from ultralytics import YOLO

        self.kind = kind
        self.prefix = "V" if kind == "vehicle" else "P"
        self.cfg = cfg
        self.device = device
        self.tracker = tracker
        self.model = YOLO(cfg.weights)
        self.names = self.model.names

    def __call__(self, frame: np.ndarray) -> list[Detection]:
        result = self.model.track(
            frame,
            persist=True,
            tracker=self.tracker,
            classes=self.cfg.classes or None,
            conf=self.cfg.conf,
            imgsz=self.cfg.imgsz,
            device=self.device,
            verbose=False,
        )[0]
        boxes = result.boxes
        if boxes is None or boxes.id is None:
            return []
        out = []
        for xyxy, conf, cls, tid in zip(boxes.xyxy.tolist(), boxes.conf.tolist(),
                                        boxes.cls.tolist(), boxes.id.tolist()):
            out.append(Detection(
                key=f"{self.prefix}{int(tid)}",
                kind=self.kind,
                cls_name=self.names[int(cls)],
                bbox=tuple(xyxy),
                conf=float(conf),
            ))
        return out
