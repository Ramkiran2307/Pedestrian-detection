# Swachh Netra: litter detection and enforcement

College project: a camera-side AI pipeline that catches **people and vehicles throwing trash in a clean zone**, records evidence (video clip, photos, Indian number plate) and sends it to a **municipal enforcement dashboard** (SQLite + Flask + React).

| | |
|---|---|
| Vehicles | `models/yolov8n.pt` + ByteTrack |
| People | `models/yolo26s.pt` + ByteTrack |
| Litter | Background model of the clean zone: a new object that stays still after someone passes |
| Who did it | A 3–5 s cache of every person/vehicle: *pushed* into the incident if they littered, otherwise *binned* |
| Plates | `models/license_plate.pt` + EasyOCR + Indian format correction (KA 01 AB 1234, 22 BH 1234 AA) |
| Server | Flask REST API + SQLite, review → notice → payment workflow + cleanup tracking |
| Dashboard | React + TypeScript (overview, offence register, case review, printable notice, live camera) |

## Quick start (laptop)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt                          # also install ffmpeg for browser-playable clips

python -m server.app                                     # terminal 1 → open http://127.0.0.1:5000
python tools/make_demo_video.py --out samples/demo_litter.mp4
python -m edge.run                                       # terminal 2 → incidents appear on the dashboard
```

Use your own camera with `python -m edge.run --source 0`, and draw its clean zone with `python -m edge.draw_zone --source 0`.

**Testing with footage, no terminal needed:** open the dashboard, scroll to **Test with your own footage** at the bottom of the Overview, and upload a video. It is processed on the server machine and the dashboard updates itself when the incidents arrive (development tool; turn it off with `LW_DEV_TOOLS=0`).

## Documentation

**[docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)** explains everything in detail: architecture, every algorithm and setting, what changed from the original scripts (kept in `legacy/`), the Indian municipal workflow, the Raspberry Pi deployment, tests and results, tuning, limitations and privacy.

## Tests

```bash
pytest                                       # unit tests
RUN_E2E=1 pytest tests/test_end_to_end.py    # full pipeline on the demo video
cd dashboard && npm install && npm run build # dashboard typecheck + build
```

Models and the Ultralytics library are AGPL-3.0.
