"""Run the litter-watch edge pipeline.

    python -m edge.run                                  # uses edge/config.yaml
    python -m edge.run --source samples/demo_litter.mp4 --show
    python -m edge.run --source 0                       # laptop webcam
    python -m edge.run --no-server --output-video output/annotated.mp4
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import load_config
from .pipeline import Pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect littering in a clean zone and report offenders.")
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")))
    ap.add_argument("--source", help="override camera.source (file, webcam index or stream URL)")
    ap.add_argument("--no-server", action="store_true", help="keep incidents in the local outbox only")
    ap.add_argument("--no-preview", action="store_true", help="disable the MJPEG preview server")
    ap.add_argument("--show", action="store_true", help="open an OpenCV window (needs a desktop)")
    ap.add_argument("--output-video", help="also save the annotated video to this file")
    ap.add_argument("--max-frames", type=int)
    ap.add_argument("--realtime", choices=["auto", "yes", "no"], default="auto")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("ultralytics", "urllib3", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    overrides = {}
    if args.source:
        src = str(Path(args.source).resolve()) if Path(args.source).exists() else args.source
        overrides["camera"] = {"source": src}
    cfg = load_config(args.config, overrides)
    if args.no_preview:
        cfg.preview.enabled = False
    if args.realtime != "auto":
        cfg.camera.realtime = args.realtime == "yes"
    if args.output_video:
        Path(args.output_video).parent.mkdir(parents=True, exist_ok=True)

    stats = Pipeline(cfg, use_server=not args.no_server, show=args.show,
                     output_video=args.output_video).run(args.max_frames)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
