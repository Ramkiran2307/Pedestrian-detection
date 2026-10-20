"""Draw the clean-zone polygon on the first frame of the camera/video.

    python -m edge.draw_zone                      # uses camera.source from config
    python -m edge.draw_zone --source 0           # webcam

Left-click adds a point, right-click / U undoes, R resets, S or Enter saves the
polygon into edge/config.yaml (zone.polygon), Esc quits without saving.
Without a desktop (e.g. SSH into the Pi) use --grid to save a reference image
with a 10% grid and type the points into config.yaml by hand.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import cv2
import numpy as np
import yaml


def first_frame(source: str):
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"Could not read a frame from {source}")
    return frame


def save_polygon(config: Path, points: list[list[float]]) -> None:
    text = config.read_text(encoding="utf-8")
    flow = "[" + ", ".join(f"[{x:.3f}, {y:.3f}]" for x, y in points) + "]"
    new, n = re.subn(r"(?m)^(\s*polygon:).*$", lambda m: f"{m.group(1)} {flow}", text, count=1)
    if n == 0:
        raise SystemExit("No 'polygon:' line found under zone: in the config")
    config.write_text(new, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.yaml")))
    ap.add_argument("--source")
    ap.add_argument("--grid", action="store_true", help="save a gridded reference image instead")
    args = ap.parse_args()
    config = Path(args.config)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    source = args.source or str(raw["camera"]["source"])
    if not source.isdigit() and "://" not in source and not Path(source).is_absolute():
        source = str((config.parent / source).resolve())
    frame = first_frame(source)
    h, w = frame.shape[:2]

    if args.grid:
        img = frame.copy()
        for i in range(1, 10):
            x, y = int(w * i / 10), int(h * i / 10)
            cv2.line(img, (x, 0), (x, h), (0, 255, 255), 1)
            cv2.line(img, (0, y), (w, y), (0, 255, 255), 1)
            cv2.putText(img, f"{i/10:.1f}", (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
            cv2.putText(img, f"{i/10:.1f}", (2, y - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        out = config.parent / "zone_reference.jpg"
        cv2.imwrite(str(out), img)
        print(f"Saved {out}. Read the grid and write zone.polygon as [[x, y], ...] fractions.")
        return

    points: list[list[float]] = [list(p) for p in (raw.get("zone", {}).get("polygon") or [])]

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append([x / (w - 1), y / (h - 1)])
        elif event == cv2.EVENT_RBUTTONDOWN and points:
            points.pop()

    win = "Draw clean zone (S=save, U=undo, R=reset, Esc=quit)"
    cv2.namedWindow(win)
    cv2.setMouseCallback(win, on_mouse)
    while True:
        img = frame.copy()
        pts = [(int(x * (w - 1)), int(y * (h - 1))) for x, y in points]
        if len(pts) >= 3:
            overlay = img.copy()
            cv2.fillPoly(overlay, [np.array(pts)], (80, 160, 60))
            img = cv2.addWeighted(overlay, 0.3, img, 0.7, 0)
        for i, p in enumerate(pts):
            cv2.circle(img, p, 5, (0, 0, 255), -1)
            if i:
                cv2.line(img, pts[i - 1], p, (0, 255, 0), 2)
        cv2.imshow(win, img)
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("s"), 13) and len(points) >= 3:
            save_polygon(config, points)
            print(f"Saved {len(points)} points to {config}")
            break
        if key == ord("u") and points:
            points.pop()
        if key == ord("r"):
            points.clear()
        if key == 27:
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
