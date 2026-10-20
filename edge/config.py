"""Typed configuration for the edge (camera-side) pipeline.

Everything tunable lives in ``edge/config.yaml``. This module turns that YAML
into dataclasses, fills in defaults, resolves relative paths against the
config file's folder and sanity-checks values that depend on each other.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


@dataclass
class CameraCfg:
    id: str = "CAM-01"
    name: str = "Demo camera"
    location: str = ""
    ward: str = ""
    # A video file path, a webcam index ("0") or an RTSP/HTTP stream URL.
    source: str = "0"
    # None = decide automatically: wall clock for live sources, video time for files.
    realtime: bool | None = None
    # Process every Nth frame (2 halves the CPU load on a Raspberry Pi).
    frame_stride: int = 1
    # Frames wider than this are downscaled before anything else happens.
    max_width: int = 1280
    loop: bool = False


@dataclass
class ModelCfg:
    weights: str = ""
    conf: float = 0.35
    imgsz: int = 640
    classes: list[int] = field(default_factory=list)


@dataclass
class ModelsCfg:
    device: str = "cpu"
    tracker: str = "bytetrack.yaml"
    # Model split agreed for the project: yolov8n tracks vehicles, yolo26s tracks people.
    vehicle: ModelCfg = field(default_factory=lambda: ModelCfg("../models/yolov8n.pt", 0.35, 640, [2, 3, 5, 7]))
    person: ModelCfg = field(default_factory=lambda: ModelCfg("../models/yolo26s.pt", 0.40, 640, [0]))
    plate: ModelCfg = field(default_factory=lambda: ModelCfg("../models/license_plate.pt", 0.25, 640, [0]))


@dataclass
class ZoneCfg:
    name: str = "Clean Zone A"
    # Polygon points as [x, y] fractions of the frame (0..1). Empty = whole frame.
    polygon: list[list[float]] = field(default_factory=list)


@dataclass
class LitterCfg:
    work_width: int = 640
    warmup_seconds: float = 2.0
    learn_seconds: float = 0.5
    diff_threshold: int = 32
    min_area_px: int = 60
    max_area_ratio: float = 0.12
    group_px: int = 15
    growth_ratio: float = 1.5
    static_seconds: float = 1.5
    max_drift_px: float = 12.0
    miss_tolerance_seconds: float = 0.6
    confirmed_miss_seconds: float = 4.0
    actor_margin: float = 0.12
    actor_memory_seconds: float = 1.0
    bg_learning_rate: float = 0.02
    global_change_ratio: float = 0.35
    absorb_after_seconds: float = 30.0
    merge_seconds: float = 8.0


@dataclass
class CacheCfg:
    # How long a person/vehicle is held after it was last seen before it is binned.
    hold_seconds: float = 4.0
    history_seconds: float = 12.0
    person_crops: int = 3
    vehicle_crops: int = 5
    crop_every_n: int = 2


@dataclass
class AttributionCfg:
    lookback_seconds: float = 2.5
    # Max gap between the litter and the actor's box, in multiples of the box size.
    person_max_distance: float = 1.2
    vehicle_max_distance: float = 1.5
    report_unattributed: bool = True


@dataclass
class OcrCfg:
    languages: list[str] = field(default_factory=lambda: ["en"])
    gpu: bool = False
    min_confidence: float = 0.15


@dataclass
class EvidenceCfg:
    pre_seconds: float = 5.0
    post_seconds: float = 3.0
    clip_width: int = 960
    jpeg_quality: int = 80
    outbox_dir: str = "outbox"
    sent_dir: str = "sent"
    keep_local: bool = True


@dataclass
class ServerCfg:
    enabled: bool = True
    url: str = "http://127.0.0.1:5000"
    api_key: str = "demo-edge-key"
    heartbeat_seconds: float = 15.0
    timeout_seconds: float = 20.0
    retry_seconds: float = 10.0


@dataclass
class PreviewCfg:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8090
    width: int = 960
    show_window: bool = False


@dataclass
class EdgeConfig:
    camera: CameraCfg = field(default_factory=CameraCfg)
    models: ModelsCfg = field(default_factory=ModelsCfg)
    zone: ZoneCfg = field(default_factory=ZoneCfg)
    litter: LitterCfg = field(default_factory=LitterCfg)
    cache: CacheCfg = field(default_factory=CacheCfg)
    attribution: AttributionCfg = field(default_factory=AttributionCfg)
    ocr: OcrCfg = field(default_factory=OcrCfg)
    evidence: EvidenceCfg = field(default_factory=EvidenceCfg)
    server: ServerCfg = field(default_factory=ServerCfg)
    preview: PreviewCfg = field(default_factory=PreviewCfg)
    base_dir: Path = field(default_factory=Path.cwd)

    def resolve(self, path: str) -> str:
        """Resolve a path from the config relative to the config file's folder."""
        p = Path(path)
        return str(p if p.is_absolute() else (self.base_dir / p).resolve())

    @property
    def ring_seconds(self) -> float:
        # The clip starts pre_seconds before the litter appeared, and the litter is only
        # confirmed static_seconds later (or later still if someone walks in front of it).
        return self.evidence.pre_seconds + self.litter.static_seconds * 3 + 2.0


def _build(cls: type, data: dict[str, Any] | None, where: str):
    obj = cls()
    if not data:
        return obj
    known = {f.name: f for f in fields(cls)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Unknown config key '{where}.{key}'")
        current = getattr(obj, key)
        if is_dataclass(current):
            value = _build(type(current), value, f"{where}.{key}")
        setattr(obj, key, value)
    return obj


def load_config(path: str | Path, overrides: dict[str, dict[str, Any]] | None = None) -> EdgeConfig:
    path = Path(path).resolve()
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    for section, values in (overrides or {}).items():
        raw.setdefault(section, {}).update(values)
    cfg: EdgeConfig = _build(EdgeConfig, raw, "config")
    cfg.base_dir = path.parent
    _validate(cfg)
    return cfg


def _validate(cfg: EdgeConfig) -> None:
    for name in ("vehicle", "person", "plate"):
        m: ModelCfg = getattr(cfg.models, name)
        m.weights = cfg.resolve(m.weights)
        if not Path(m.weights).exists():
            raise FileNotFoundError(f"{name} model not found: {m.weights}")
    # A tracked person/vehicle must still be in the cache when the litter is confirmed:
    # it could have been last seen `lookback` seconds before the litter appeared, and
    # confirmation takes another `static_seconds`.
    needed = cfg.attribution.lookback_seconds + cfg.litter.static_seconds
    if cfg.cache.hold_seconds < needed:
        log.warning("cache.hold_seconds=%.1f is shorter than lookback+static=%.1f; raising it",
                    cfg.cache.hold_seconds, needed)
        cfg.cache.hold_seconds = needed
    src = cfg.camera.source
    if not src.isdigit() and "://" not in src:
        cfg.camera.source = cfg.resolve(src)
    for key in ("outbox_dir", "sent_dir"):
        setattr(cfg.evidence, key, cfg.resolve(getattr(cfg.evidence, key)))
    if cfg.camera.frame_stride < 1:
        cfg.camera.frame_stride = 1
