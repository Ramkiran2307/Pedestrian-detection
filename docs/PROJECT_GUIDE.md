# Swachh Netra: Project Guide

**Automatic detection of littering in clean zones, with evidence capture, number-plate reading and a municipal enforcement dashboard.**

This document explains every part of the system: what was there before, what was changed and why, how each algorithm works, how to run it on a laptop and later on a Raspberry Pi, and what its limits are. It is written to be usable as the technical chapter of the college project report.

---

## Contents

1. [Problem statement](#1-problem-statement)
2. [Starting point: the files and models received](#2-starting-point-the-files-and-models-received)
3. [Architecture](#3-architecture)
4. [What changed: file-by-file](#4-what-changed-file-by-file)
5. [The models](#5-the-models)
6. [Edge pipeline in detail](#6-edge-pipeline-in-detail)
7. [Server and database](#7-server-and-database)
8. [Enforcement workflow (Indian municipal model)](#8-enforcement-workflow-indian-municipal-model)
9. [Dashboard](#9-dashboard)
10. [Running it on a laptop](#10-running-it-on-a-laptop)
11. [Moving to the Raspberry Pi](#11-moving-to-the-raspberry-pi)
12. [Testing and results](#12-testing-and-results)
13. [Tuning guide](#13-tuning-guide)
14. [Troubleshooting](#14-troubleshooting)
15. [Limitations and future work](#15-limitations-and-future-work)
16. [Privacy, security and legal notes](#16-privacy-security-and-legal-notes)

---

## 1. Problem statement

Detect **people or vehicles that throw trash in a clean (non-dumping) area**, identify them, and store the details in a database:

* **Vehicles**: track each vehicle with a bounding box. If trash comes out of it (moving or parked), record the incident and its **number plate** (Indian format, any vehicle type: cars, bikes, buses, trucks).
* **People**: track each person. If they drop trash, record the incident with **photographic evidence** (no face recognition).
* **Processing location**: on a Raspberry Pi next to the camera, which sends the evidence clip to the server. For now, testing happens on a laptop.
* **Scope**: one camera, one clean-zone region drawn on the frame.
* **Database**: SQLite.
* **Dashboard**: a web page modelled on how an Indian municipal body would handle littering complaints.

Decisions agreed before building:

| Question | Decision |
|---|---|
| How is trash detected? | **(b) Motion-based rule**: a new object that appears and stays still in the zone after a person or vehicle passes. No trash dataset is needed. |
| What is a violation? | Trash left in a clean (unmarked-for-trash) area. |
| Which trash? | Any kind, from moving or parked vehicles and from pedestrians. |
| Person identification | **Evidence only** (photo + clip + time + track ID). |
| Plates | Indian format, all vehicle types. The plate model was trained on CCTV-style views. |
| Model split | **yolov8n → vehicles**, **yolo26s → people**. |
| Person data handling | **Cache each person/vehicle for 3–5 s**: if they littered, the data is *pushed* to the incident, otherwise it goes *into the bin*. |

---

## 2. Starting point: the files and models received

### Models

| File | What it is | Classes | Parameters | Used for |
|---|---|---|---|---|
| `yolov8n.pt` | Official Ultralytics YOLOv8-nano (2022), COCO | 80 | 3.16 M | **Vehicles** (car, motorcycle, bus, truck) |
| `yolo26s.pt` | Official Ultralytics YOLO26-small (2026), COCO, end-to-end (NMS-free) | 80 | 10.0 M | **People** |
| `license_plate.pt` | Custom fine-tune of yolov8n (May 2025, 50 epochs, Colab) | 1 (`license_plate`) | 3.01 M | **Plates** (offending vehicles only) |

The license-plate model reports precision 0.95, recall 0.90, mAP50 0.936 and mAP50-95 0.745 on its own validation set. Its training took about 83 s, so the dataset was small. All three files were checked statically before loading: they import only `torch`/`ultralytics` classes and contain no code-execution payload.

### Scripts

| File | What it did | Problems found |
|---|---|---|
| `app.py` | Flask server keeping "complaints" in a Python list | `debug=True` on `0.0.0.0` (remote code execution through the Werkzeug debugger), no authentication, data lost on restart, unbounded list, crashes on non-object JSON, field names from another idea (`"Car Slowing Down"`) |
| `plate_reader.py` | Plate detection + OCR on one image | No check that the image loaded, first OCR fragment only, no plate-format validation |
| `two_stage_reader.py` | Vehicle → plate → OCR on one image | Draws boxes on the image it then crops from (annotations leak into OCR), no empty-crop check |
| `video_plate_reader.py` | Same, per video frame | OCR on every plate in every frame (far too slow for a Pi), annotation leakage, no tracking, nothing sent to the server |
| `server.ps1` | PowerShell static file server for preview | Path-traversal risk, few MIME types, crashes if both ports are busy |

**The core gap:** nothing detected *trash* or the *act of littering*, nothing linked a piece of trash to who dropped it, and the reader scripts never talked to the server. The originals are kept unchanged in `legacy/` for reference.

---

## 3. Architecture

```
┌──────────────────────────── EDGE (laptop now / Raspberry Pi later) ─────────────────────────────┐
│                                                                                                  │
│  Camera / video ──► VideoSource ──► frame                                                        │
│                                      │                                                           │
│              ┌───────────────────────┼─────────────────────────┐                                 │
│              ▼                       ▼                         ▼                                 │
│   yolov8n + ByteTrack      yolo26s + ByteTrack        LitterDetector (background model           │
│   vehicles  V1, V2…        people  P1, P2…            of the clean zone, actors masked out)      │
│              │                       │                         │                                 │
│              └──────────┬────────────┘                         │ "new static object" event       │
│                         ▼                                      ▼                                 │
│                  TrackCache (3-5 s)  ◄──────────── Attribution: who was next to it?              │
│                  trajectory + best crops                       │                                 │
│                  ├─ littered  → PUSH ─────────────────────────►│ PendingIncident                 │
│                  └─ otherwise → BIN (deleted)                  │ (frames before + after)         │
│                                                                ▼                                 │
│                                         IncidentWorker thread: plate model + EasyOCR             │
│                                         (offending vehicles only), clip + snapshots              │
│                                                                ▼                                 │
│                                         outbox/<uid>/  ──► Uploader (retries) ─────────┐         │
│  MJPEG preview :8090 ◄── annotated frame                                               │         │
└────────────────────────────────────────────────────────────────────────────────────────┼─────────┘
                                                                                         │ HTTPS/HTTP
┌──────────────────────────── SERVER (laptop) ───────────────────────────────────────────▼─────────┐
│  Flask REST API  ──► SQLite (incidents, evidence, cameras, audit_log)  + media/ files            │
│        ▲                                                                                         │
│        └── React dashboard: Overview · Offence register · Case review · Notice · Live camera     │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Why this split:

* **Edge does the heavy, private work.** Raw video never leaves the device. Only incident evidence (a few hundred KB) is uploaded, which matters on a Pi with a mobile-data connection.
* **Store-and-forward.** Evidence is written to disk first and uploaded afterwards, so an outage loses nothing.
* **Server stays simple.** It is a small Flask app with a single-file SQLite database, which is easy to run on a college laptop.

Repository layout:

```
pedestrian-detection/
├── edge/                  camera-side pipeline (Python package)
│   ├── config.yaml        every tunable setting
│   ├── run.py             entry point:  python -m edge.run
│   ├── draw_zone.py       draw the clean-zone polygon
│   ├── pipeline.py        main loop
│   ├── detectors.py       YOLO + ByteTrack wrappers
│   ├── cache.py           3-5 s actor cache (push / bin)
│   ├── litter.py          litter (new static object) detector
│   ├── association.py     who dropped it?
│   ├── plates.py          plate detection + OCR + Indian-format correction
│   ├── evidence.py        frame ring buffer, clips, snapshots, incident worker
│   ├── uploader.py        outbox → server, heartbeats
│   ├── preview.py         MJPEG live preview server
│   ├── config.py          typed config loader
│   └── geometry.py        box / polygon helpers
├── server/                Flask + SQLite
│   ├── app.py             REST API, serves the dashboard
│   ├── db.py              schema
│   ├── workflow.py        enforcement + cleanup state machine
│   ├── settings.py        env-var settings, fine schedule
│   └── seed.py            sample data for presentations
├── dashboard/             React + TypeScript + Vite (dist/ = built site)
├── models/                yolov8n.pt, yolo26s.pt, license_plate.pt
├── tools/make_demo_video.py   synthetic test video
├── tests/                 unit + end-to-end tests
├── legacy/                the original scripts, unchanged
└── docs/PROJECT_GUIDE.md  this file
```

---

## 4. What changed: file-by-file

| Original | Replaced by | What changed and why |
|---|---|---|
| `video_plate_reader.py` loop | `edge/pipeline.py` | One loop runs both trackers, the litter detector, the cache and the evidence collection. Timestamps come from the video for files (deterministic) or the wall clock for live cameras. |
| `vehicle_model(frame, classes=...)` | `edge/detectors.py` | Now `model.track(persist=True)` with ByteTrack, so each vehicle/person keeps an ID across frames. That ID is what makes attribution possible. People now use **yolo26s** as agreed. |
| Plate OCR on every frame | `edge/plates.py` + `IncidentWorker` | OCR runs **only for vehicles linked to an incident**, on up to 5 cached crops, in a background thread. This is about 100× less OCR work and never stalls the video. |
| `ocr_result[0][1]` | `plates.join_fragments` | All fragments are joined in reading order (two-line bike plates), restricted to A–Z/0–9, corrected to the Indian format and voted across crops. |
| Drawing on the frame before cropping | `cache.py` / `evidence.py` | Crops are taken from the **clean** frame; annotations are drawn only on copies (live preview, clip). |
| (nothing) | `edge/litter.py` | **New:** detects litter with a background model of the clean zone. |
| (nothing) | `edge/cache.py` | **New:** the 3–5 s push-or-bin cache you specified. |
| (nothing) | `edge/association.py` | **New:** decides who dropped the litter. |
| `cv2.VideoWriter('mp4v')` | `evidence.write_clip` | `mp4v` does not play in browsers. Clips are now H.264 MP4 through ffmpeg (about 200 KB per clip), falling back to WebM/VP8. |
| (nothing) | `edge/uploader.py` | **New:** outbox on disk, retries, duplicate-safe, heartbeats. |
| `app.py` | `server/app.py` | Debug off by default, API key for the edge device, SQLite, input validation, evidence files, filters, statistics, workflow actions, audit log, serves the dashboard. |
| `server.ps1` | Vite dev server / Flask | The dashboard is served by Flask (built) or Vite (development, with an API proxy, so no CORS setup is needed). |
| (no dashboard source) | `dashboard/` | A new React dashboard designed around a municipal enforcement workflow. |

---

## 5. The models

### Why the weights were not retrained

* **Vehicles and people**: both YOLO models are already trained on COCO, which contains exactly the classes we need (person, car, motorcycle, bus, truck). Retraining would need a labelled dataset and GPU time without a clear gain for a demo.
* **Plates**: `license_plate.pt` already detects plates in CCTV-style views at 0.94 mAP50. The weak link in plate reading was the OCR and its post-processing, not detection, so that is where the work went (section 6.5).
* **Trash**: no trash model is used. We agreed on option (b), the motion/static-object method, which needs no training data. Section 15 explains how to add a trained model later.

### Model split

| Model | Job | Classes | Confidence | Why |
|---|---|---|---|---|
| yolov8n | Vehicles | 2 car, 3 motorcycle, 5 bus, 7 truck | 0.35 | Small and fast; vehicles are large objects |
| yolo26s | People | 0 person | 0.40 | More accurate on people (the harder class) |
| license_plate | Plate in a vehicle crop | 0 | 0.25 | Runs only for offending vehicles |

Both COCO models could detect both classes. Running them as a split doubles the detection cost, which is the main thing to revisit on the Pi (section 11). Auto-rickshaws are not a COCO class; YOLO usually labels them `car`, `motorcycle` or `truck`, which still triggers plate reading.

### Licences

All three models and the Ultralytics library are AGPL-3.0. That is fine for a college project. A commercial deployment would need an Ultralytics enterprise licence or would have to release its source code.

---

## 6. Edge pipeline in detail

Every setting mentioned below lives in `edge/config.yaml`.

### 6.1 Video source (`pipeline.VideoSource`)

* `camera.source`: a file path, a webcam index (`"0"`) or an `rtsp://` URL.
* **Clock**: for files, `t = frame_index / fps`, so results are identical however fast the computer is. For live sources, `t` is wall-clock time. You can force either with `camera.realtime`.
* `frame_stride`: process every Nth frame (use 2–3 on a Pi).
* `max_width`: frames are downscaled to 1280 px first.

### 6.2 Detection and tracking (`detectors.py`)

Each model runs `model.track(frame, persist=True, tracker="bytetrack.yaml")`. ByteTrack links detections across frames with a motion model and IoU matching, giving every object an ID. Track IDs are prefixed (`V7`, `P3`) because the two models have independent trackers. Each detection becomes `Detection(key, kind, cls_name, bbox, conf)`.

### 6.3 The actor cache: push or bin (`cache.py`)

This implements the rule you described: *"hold every person's data for 3–5 s; if it is detected it is pushed to data, else into bin."*

For every track the cache keeps a `TrackRecord`:

| Field | Purpose |
|---|---|
| `samples` | Timestamped boxes for the last `history_seconds` (12 s). This is the trajectory. |
| `crops` | The best `person_crops` (3) / `vehicle_crops` (5) images, scored by **size × sharpness × confidence** (sharpness = variance of the Laplacian). A min-heap keeps only the best ones. |
| `pinned` | Above 0 while an incident that uses this record is being finalised. |

Lifecycle:

```
track first seen ──► record created
track visible    ──► samples + crops updated (crop every 2nd observation)
track lost       ──► kept for hold_seconds (default 4 s)
   ├── littering attributed to it within that time ──► PUSHED: crops copied into the incident
   └── nothing happened ──────────────────────────────► BINNED: record deleted, nothing written to disk
```

**How a person's image gets stored**, which you asked about: the person's best crops already sit in memory in their cache record while they walk through the frame. When litter is attributed to them, those crops (plus the scene snapshot and the clip) are copied into the incident and written to disk. Everyone else's crops are deleted when their record is binned.

The live preview shows the counters: *cache N (pushed X, binned Y)*.

**A timing constraint enforced by the config loader:** an actor last seen up to `lookback_seconds` before the litter appeared must still be in the cache when the litter is *confirmed* (`static_seconds` later). So `hold_seconds ≥ lookback_seconds + static_seconds` (2.5 + 1.5 = 4.0 by default). If you set a smaller value, the loader raises it and logs a warning.

### 6.4 Litter detection (`litter.py`)

The idea: litter is something that **was not in the clean zone before, appears right after someone passes, and then stays put**.

Steps per frame, on a 640-px-wide copy for speed:

1. **Warm-up (2 s):** learn the empty scene quickly (`accumulateWeighted`, α = 0.2), except under people/vehicles.
2. **Difference:** blur the frame (5×5 Gaussian) and take the per-pixel max-channel absolute difference from the background. Pixels above `diff_threshold` (32) inside the zone polygon are foreground. Morphological open (3×3) removes noise and close (7×7) fills holes.
3. **Mask actors:** every person/vehicle box, enlarged by `actor_margin` (12 %), is removed from the foreground, because people and cars are not litter.
4. **Lighting guard:** if more than `global_change_ratio` (35 %) of the zone changes at once (lights on, cloud, camera bump), the background is reset instead of raising alerts.
5. **Blobs:** connected components with an area between `min_area_px` (60) and `max_area_ratio` (2 % of the frame).
6. **Candidate tracking:** blobs are matched to existing candidates by IoU, or by "the centre of one is inside the other" (a half-hidden object still matches).
   * If it moved more than `max_drift_px` (12 px) it is still flying or rolling, so the clock restarts at the new spot.
   * `visible_time` grows only while the blob is **not** under a person/vehicle box seen in the last `actor_memory_seconds` (1 s). This stops a parked car that the detector briefly misses from becoming "litter".
   * When `visible_time ≥ static_seconds` (1.5 s), the candidate is **confirmed** and a `LitterEvent(t_appear, t_confirm, bbox)` is emitted once.
7. **Occlusion handling:** a candidate hidden behind someone is kept, not deleted. A confirmed candidate survives `confirmed_miss_seconds` (4 s) of being unseen, so a passer-by walking over existing litter neither deletes it nor makes it count twice. (This was a real bug found in testing; see section 12.)
8. **Background update:** slow (`bg_learning_rate` 0.02) everywhere except under actors and candidates. Confirmed litter is absorbed into the background after `absorb_after_seconds` (30 s), so it is not reported again; by then the case is on the dashboard and the cleanup task exists.

Why not a trained trash detector? It needs a large labelled dataset of Indian street litter and still misses unusual items. The static-object method catches *any* object regardless of type, which matches the "any kind of trash" requirement. Its weaknesses are listed in section 15.

### 6.5 Attribution: who dropped it? (`association.py`)

When litter is confirmed, every cached actor with samples in `[t_appear − lookback_seconds, t_appear + 0.5 s]` is a suspect. For each one:

* **distance** = gap from the litter's centre to the nearest edge of the actor's box (0 if inside), divided by the box size (the diagonal for people, the longer side for vehicles). Normalising this way means "half a body away" counts the same near and far from the camera.
* An actor qualifies if distance ≤ `person_max_distance` (1.2) or `vehicle_max_distance` (1.5; things thrown from a window travel further).
* **score** = 0.7 × closeness + 0.3 × recency.

The highest score is the **offender**. Then `linked_actor` looks for a person and a vehicle that overlap at that moment (overlap ≥ 30 % of the person's box), for example a bike rider or a driver. If found, the incident gets both the person snapshot **and** the vehicle plate. The top 5 suspects and their scores are stored with the incident, so the officer can see who else was nearby.

If nobody qualifies, the incident is still reported as **unattributed** (`report_unattributed: true`). The city still needs to clean the litter.

### 6.6 Number-plate reading (`plates.py`)

This runs only in the background worker, and only for offending vehicles:

1. **Detect** the plate with `license_plate.pt` in each of up to 5 cached vehicle crops; keep the best box, with 6 % / 12 % padding.
2. **Prepare two variants:** upscaled to about 96 px tall (bicubic), and a CLAHE contrast-enhanced grayscale version.
3. **OCR** with EasyOCR, `allowlist = A-Z 0-9`, dropping fragments below confidence 0.15.
4. **Join fragments** top-to-bottom, then left-to-right (lines split when the vertical gap exceeds 0.6 × the median fragment height). This handles two-line bike plates.
5. **Normalise to Indian formats:**
   * Standard: `SS DD L{0-3} NNNN` (e.g. `KA 01 AB 1234`, `DL 3C AB 1234`, `MH 12 A 1234`)
   * BH series: `YY BH NNNN L{1-2}` (e.g. `22 BH 1234 AA`)

   Every layout that fits the string's length is tried. Characters are fixed **by slot type**: a letter slot turns `0→O, 1→I, 8→B, 5→S…` and a digit slot turns `O→0, I→1, B→8, S→5…`. The state code must be a real one (`KA`, `MH`, `DL`, `TN`, … 39 codes). A leading `IND` (from high-security plates) is stripped. The layout needing the fewest fixes wins (at most 3).
6. **Vote** across all crops and variants: weight = OCR confidence × detection confidence (× 0.35 if the format is invalid). The reported confidence = mean OCR confidence × agreement.

If the format is still invalid, the raw text is kept and the dashboard asks the officer to correct it. A notice for a vehicle cannot be issued without a valid plate.

### 6.7 Evidence (`evidence.py`)

* **Frame ring buffer:** the last `pre_seconds + 3×static_seconds + 2` seconds of frames, JPEG-compressed at 960 px. That is about 25 MB at 25 fps, which fits on a Pi. Each frame stores its detections, so annotations are drawn later.
* **Pending incident:** takes the frames from `t_appear − pre_seconds` and keeps collecting until `t_confirm + post_seconds`.
* **Worker thread** (the main loop never waits for it):
  * reads the plate;
  * renders every frame with the offender in red (`OFFENDER bus V7 | KA 05 MN 4321`), others in grey, the litter in orange, and a camera/time header;
  * writes `clip.mp4` (H.264 via ffmpeg, CRF 28, about 0.2 MB) and snapshots: `scene.jpg` (the moment the litter appeared), `person.jpg` / `vehicle.jpg`, `plate.jpg`, `litter.jpg`;
  * writes `meta.json`;
  * renames `.uid` → `uid` atomically, so the uploader never sees half-written folders.

### 6.8 Upload (`uploader.py`)

* Watches `outbox/`. Sends `POST /api/v1/incidents` (multipart: `metadata` JSON + files) with header `X-API-Key`.
* Success: the folder is moved to `sent/` (or deleted if `keep_local: false`). Network error: it stays and is retried every `retry_seconds`.
* The server treats a repeated `uid` as already stored, so retries never create duplicates.
* A heartbeat every 15 s sends fps, cache counters and the preview URL. The dashboard shows the camera as online/offline.

### 6.9 Live preview (`preview.py`)

A small threaded HTTP server on port 8090:
* `/stream.mjpg`: the annotated live feed (used by the dashboard's Live page);
* `/snapshot.jpg`: the latest frame;
* `/status`: JSON counters.

This works on a headless Pi. `--show` opens an OpenCV window instead, if you have a desktop.

---

## 7. Server and database

### Running

```
python -m server.app                        # http://127.0.0.1:5000
HOST=0.0.0.0 LW_API_KEY=<secret> python -m server.app   # reachable by the Pi
```

| Env var | Default | Meaning |
|---|---|---|
| `LW_API_KEY` | `demo-edge-key` | Must match `server.api_key` in `edge/config.yaml` |
| `LW_DATA_DIR` | `server/data` | SQLite DB + `media/` evidence |
| `LW_DASHBOARD_DIR` | `dashboard/dist` | Built dashboard |
| `LW_MUNICIPALITY` | `Demo Municipal Corporation` | Printed on notices |
| `LW_TIMEZONE` | `Asia/Kolkata` | Daily/hourly statistics |
| `HOST`, `PORT` | `127.0.0.1`, `5000` | Listen address |

### API

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/api/v1/incidents` | edge (API key) | Upload an incident + evidence (idempotent by `uid`) |
| POST | `/api/v1/cameras/<id>/heartbeat` | edge (API key) | Camera status |
| GET | `/api/v1/incidents` | dashboard | List; filters `status, type, cleanup, camera, q, from, to, overdue, page, page_size` |
| GET | `/api/v1/incidents/<id>` | dashboard | Detail + evidence URLs + audit trail + earlier cases for the same plate |
| POST | `/api/v1/incidents/<id>/actions` | dashboard | `verify, reject, issue_notice, mark_paid, escalate, reopen, assign_cleanup, mark_cleaned, correct_plate, add_note` |
| GET | `/api/v1/stats` | dashboard | KPIs, per-day / per-hour counts, pipeline, repeat offenders |
| GET | `/api/v1/cameras` | dashboard | Cameras + online flag |
| GET | `/api/v1/settings` | dashboard | Municipality, fine schedule, due days |
| GET | `/media/<uid>/<file>` | dashboard | Evidence files |

Invalid actions return **409** with a readable message (e.g. *"'issue notice' is not allowed while the case is pending review"*), which the dashboard shows as a toast.

### Schema (`server/db.py`)

```
cameras    (id PK, name, location, ward, zone, preview_url, stats JSON, last_seen, created_at)
incidents  (id PK, uid UNIQUE, camera_id, zone, ward, location,
            occurred_at, detected_at, received_at,
            offender_type [person|vehicle|unknown], offender_track, attribution_score,
            vehicle_class, vehicle_category [two_wheeler|car|heavy], linked_track,
            plate_number, plate_display, plate_confidence, plate_valid, plate_raw JSON, plate_corrected,
            litter_bbox JSON, suspects JSON,
            status, cleanup_status, cleanup_assignee,
            fine_amount, notice_number UNIQUE, notice_issued_at, due_date, paid_at, payment_ref,
            rejection_reason, is_sample, updated_at)
evidence   (id PK, incident_id FK, kind [scene|person|vehicle|plate|litter|clip], path, mime, bytes, created_at)
audit_log  (id PK, incident_id FK, at, actor, action, from_value, to_value, note)
```

Times are stored as UTC ISO-8601 and shown in IST. WAL mode lets the uploader write while the dashboard reads.

---

## 8. Enforcement workflow (Indian municipal model)

How the design maps to the way urban local bodies (ULBs) in India deal with littering:

* The **Solid Waste Management Rules, 2016** make ULBs responsible for keeping public places clean and require them to frame **bye-laws** that include **spot fines** for littering. The amounts differ by city, so the fine schedule here is a configurable sample (`server/settings.py`): person ₹500, two-wheeler ₹1,000, car ₹2,000, bus/truck ₹5,000. Set them to your city's bye-law values.
* Enforcement is usually done at **ward level** by **sanitary inspectors** / health officers. So every incident carries a ward, notice numbers include the ward (`SWM/WARD12/2026/00001`), and every action records the acting officer's name.
* Cleanliness is a Swachh Bharat Mission (Urban) / Swachh Survekshan metric, so the dashboard tracks **cleanup** (pending → assigned → cleaned) separately from **punishment**. The litter has to be cleaned even when nobody is fined.
* A human must verify every automatic detection before any penalty. That is fairer and legally safer.

State machine (`server/workflow.py`):

```
                 ┌─────────── reopen ───────────┐
                 ▼                              │
  detected ─► PENDING REVIEW ──verify──► VERIFIED ──issue notice──► NOTICE ISSUED ──mark paid──► FINE PAID
                 │                        │                           │
                 └──reject (reason)──► REJECTED ◄──reject──┘          └──escalate──► ESCALATED ──mark paid──► FINE PAID

  Cleanup (independent):  pending ──assign──► assigned ──mark cleaned──► cleaned
```

Rules enforced on the server:
* Reject needs a reason.
* No notice for an unattributed case.
* No vehicle notice without a valid plate.
* The plate cannot change after a notice.
* The fine cannot be negative.
* Due date = issue date + 15 days.
* A notice still unpaid after its due date is flagged **overdue**.

---

## 9. Dashboard

**Stack:** React 19, TypeScript, Vite 7, React Router 7, Recharts 3, lucide icons. No CSS framework: one hand-written stylesheet with design tokens and fully designed light **and** dark themes.

| Page | What it shows |
|---|---|
| **Overview** (command centre) | Incidents today (hero number), awaiting review, overdue notices, fines collected/levied, cleanup pending + officer-confirmed accuracy; incidents per day (stacked by offender type, with a table view); time-of-day histogram; enforcement pipeline counts; latest detections; repeat offenders by plate; vehicle offences by category; camera status. Refreshes every 5 s. |
| **Offence register** | Searchable (plate / notice no. / #id), filterable (type, status, cleanup, date range, overdue) paged list with thumbnails, plate badges, status and cleanup chips. Filters are kept in the URL, so links can be shared. |
| **Case review** | Evidence viewer (clip, scene, person/vehicle, plate, litter), detection facts (attribution and OCR confidence, linked rider, other suspects), repeat-offender warning, the actions valid for the current state, cleanup tracking, notes and the full audit trail. |
| **Notice** | Printable notice (Print / save as PDF) with the notice number, offence details, evidence photos, penalty and due date. Marked as a demonstration document. |
| **Live camera** | The MJPEG feed from the edge plus live counters (fps, cache active/pushed/binned, incidents, uploads, upload errors). |
| **How it works** | The pipeline explained for viewers and examiners. |

Design notes:
* Chart colours were checked with a colour-blind-safety validator (blue = pedestrian, orange = vehicle, grey = unattributed; all checks pass in both themes).
* Status always appears as **icon + label**, never colour alone.
* Columns are at most 24 px wide, with 2 px gaps between stacked segments and a hairline grid.
* Works at phone width without sideways scrolling.
* The **acting officer** name (top bar) is required before any action and goes into the audit trail.

Development: `cd dashboard && npm install && npm run dev` → http://localhost:5173 (proxies `/api` and `/media` to Flask). Production: `npm run build`; Flask serves `dashboard/dist`. The built `dist/` is committed so the demo laptop doesn't need Node.js.

---

## 10. Running it on a laptop

```bash
# 1. Python environment (Python 3.10+)
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# If cv2.imshow errors with "not implemented": pip uninstall -y opencv-python-headless && pip install --force-reinstall opencv-python
# Install ffmpeg (for small, browser-playable clips): apt install ffmpeg  /  winget install ffmpeg  /  brew install ffmpeg

# 2. Server + dashboard  (terminal 1)
python -m server.app                 # open http://127.0.0.1:5000

# 3. Test video (no camera needed)
python tools/make_demo_video.py --out samples/demo_litter.mp4

# 4. Edge pipeline  (terminal 2)
python -m edge.run                   # uses edge/config.yaml (source = the demo video)
python -m edge.run --source 0        # or your webcam
python -m edge.run --source my_clip.mp4 --show --output-video output/annotated.mp4

# 5. Draw the clean zone for a new camera / video
python -m edge.draw_zone --source my_clip.mp4

# Optional: sample data for a fuller dashboard during a presentation
python -m server.seed --count 60     # remove later with: python -m server.seed --purge
```

The first run downloads EasyOCR's models (about 100 MB) into `~/.EasyOCR/`.

What you should see with the demo video:
* Person A drops a wrapper → **incident (person)**.
* Person B walks past → **binned**.
* A bus throws litter → **incident (vehicle, plate KA 05 MN 4321)**.

Both incidents appear in the Offence register about 15 s after detection.

---

## 11. Moving to the Raspberry Pi

**Hardware:** a Raspberry Pi 4 (4 GB+) or Pi 5, a camera (Pi Camera via `libcamera` / USB webcam / IP camera RTSP), Raspberry Pi OS 64-bit.

```bash
sudo apt install -y python3-venv ffmpeg libgl1
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Network:** run the server on the laptop with `HOST=0.0.0.0 LW_API_KEY=<secret> python -m server.app`. In the Pi's `edge/config.yaml` set:

```yaml
server:  { url: "http://<laptop-ip>:5000", api_key: "<secret>" }
preview: { host: 0.0.0.0 }        # so the dashboard can show the live feed
camera:  { source: "0", frame_stride: 2 }
```

**Performance.** Two YOLO models plus tracking on a Pi CPU will run at a few frames per second at best. In order of impact:

1. **Export to NCNN** (an ARM-optimised runtime):
   `yolo export model=models/yolov8n.pt format=ncnn` and the same for `yolo26s.pt`. Then point `weights:` at the generated `*_ncnn_model` folders. If an export fails for one model, keep its `.pt`.
2. **Smaller input:** `imgsz: 416` or `320` for both models.
3. **`frame_stride: 2–3`**: littering takes seconds, so processing 5–8 frames per second is enough. Keep `static_seconds` ≥ 1.5.
4. **Single model** (if still too slow): break the split and use one model for both classes (`classes: [0, 2, 3, 5, 7]`). This is the biggest saving.
5. Plate OCR runs only per incident in the background, so it does not affect the live frame rate. An incident just takes longer to upload.

---

## 12. Testing and results

```bash
pytest                                  # 28 unit tests (~10 s)
RUN_E2E=1 pytest tests/test_end_to_end.py   # full pipeline on the demo video (~2-3 min)
cd dashboard && npm run build           # typecheck + build
```

| Test file | What it covers |
|---|---|
| `test_plates.py` | Indian format normalisation, OCR confusion fixes, IND prefix, Delhi formats, BH series, two-line joining |
| `test_cache_and_attribution.py` | Bin after hold time, pinned → pushed, best-crop selection, nearest actor blamed, bystander ignored, rider ↔ motorcycle link |
| `test_litter.py` | Static object reported once and on time, moving person ignored, object under a standing person reported after they leave, short-lived object ignored, lighting change resets |
| `test_server.py` | API key, upload + evidence, idempotent uid, input validation, full workflow, reject reason/reopen, plate correction, unattributed cleanup-only, filters/stats/heartbeat |
| `test_end_to_end.py` | Real models on the synthetic video: exactly one person and one vehicle incident, plate read correctly, passer-by binned, evidence files present |

**End-to-end results** (synthetic video, 4-core laptop CPU):

| Event | Result |
|---|---|
| Person A drops wrapper at 5.0 s | Appeared 5.4 s, confirmed 6.9 s → **person P1**, score 0.87 |
| Person B walks past | **Binned** |
| Bus throws litter at 17.0 s | Appeared 17.1 s, confirmed 19.3 s → **vehicle (bus) V7**, score 0.97 |
| Plate of the bus | **KA05MN4321**, valid, confidence 0.98 (10/10 reads agreed) |
| Throughput | 5–7 fps with both models on CPU |
| Evidence size | 0.2–0.3 MB clip + 5 JPEGs per incident |

**Bugs found and fixed during testing:**
1. A passer-by partly covering earlier litter split its blob, so it was reported again with "no suspect". Fixed with containment-based matching and a longer forgetting time for confirmed litter.
2. The plate text in the synthetic video overflowed the plate, so the plate detector missed it. Fixed in the generator (the real pipeline was fine).
3. `mp4v` clips don't play in browsers. Switched to H.264 through ffmpeg (6–11 MB → 0.2 MB per clip).

The synthetic video proves the logic, not real-world accuracy. Record a few minutes of real footage at the actual site and tune with it (section 13).

---

## 13. Tuning guide

| Symptom | Change |
|---|---|
| Small litter (wrappers far away) is missed | Lower `litter.min_area_px` (e.g. 30); raise `work_width` to 960; lower `diff_threshold` to 25 |
| Shadows / leaves trigger false litter | Raise `diff_threshold` (40); raise `static_seconds` (2.5); draw the zone tighter, away from trees |
| Parked cars or people standing still become "litter" | Raise `actor_margin` (0.2) and `actor_memory_seconds` (2.0) |
| Littering blamed on a bystander | Lower `person_max_distance` (0.8); lower `lookback_seconds` |
| Thrown-from-vehicle litter is unattributed | Raise `vehicle_max_distance` (2.0) and `lookback_seconds` (3.5); `hold_seconds` rises automatically |
| Plates not read | Mount the camera so plates are at least ~20 px tall; raise `cache.vehicle_crops`; lower `models.plate.conf` to 0.15 |
| Too slow | See section 11 |
| Same litter reported twice | Raise `confirmed_miss_seconds` and `absorb_after_seconds` |

---

## 14. Troubleshooting

| Problem | Fix |
|---|---|
| `cv2.imshow ... not implemented` | Both `opencv-python` and `opencv-python-headless` are installed (EasyOCR pulls the headless one). Use the browser preview, or reinstall `opencv-python` (section 10). |
| `ModuleNotFoundError: lap` | `pip install lap` (needed by ByteTrack). |
| yolo26s fails to load | Needs `ultralytics >= 8.3.222`. |
| Dashboard says "Cannot reach the server" | Start `python -m server.app`; in dev, Vite proxies to port 5000. |
| Uploads fail with 401 | `server.api_key` in `edge/config.yaml` ≠ `LW_API_KEY`. |
| Live page shows "preview unavailable" | `preview.enabled: true`; on the Pi set `preview.host: 0.0.0.0`; check that port 8090 is open. |
| Clip doesn't play | Install ffmpeg on the edge device (H.264 MP4). Without it, the WebM fallback plays in Chrome, Edge and Firefox. |
| Times on the clip header are in UTC | The edge device's clock/timezone is used: `sudo timedatectl set-timezone Asia/Kolkata` on the Pi. |

---

## 15. Limitations and future work

* **Static-object litter detection** cannot tell litter from a bag someone puts down on purpose; officers catch these at review. It also struggles with very small or camouflaged items, heavy rain or wind-blown leaves, and a moving camera. **Next step:** add a trained trash detector (e.g. fine-tune YOLO on the public TACO litter dataset plus your own labelled site footage) as a second check on each candidate. This plugs into `LitterDetector._track` before confirmation.
* **The act of throwing** is inferred from position and timing, not seen. Pose-based action recognition (hand-release motion) could raise confidence.
* **Plate reading** depends on camera placement; plates under ~15 px tall are rarely readable. Owner lookup (VAHAN) and e-challan integration require government API access and are out of scope.
* **One camera / one zone.** The design already includes camera IDs; supporting several zones means `zone` becomes a list in `config.yaml` with one `LitterDetector` per zone.
* **Dashboard authentication** is not implemented (fine on a closed demo network). For real use, add officer logins and roles (inspector / ward officer / admin) and HTTPS.
* **AGPL licence** of Ultralytics (see section 5).

---

## 16. Privacy, security and legal notes

* **Data minimisation:** people and vehicles not involved in an incident are binned within seconds and never written to disk; plates are read only for offenders. This follows the purpose-limitation idea of India's Digital Personal Data Protection Act, 2023.
* **No face recognition**, as agreed. Identification is by evidence photos and video only.
* **Human in the loop:** no automated penalty; every case needs an officer's verification and every action is in the audit trail.
* **Signage:** a real deployment should put up CCTV notices in the monitored area.
* **Security:** the edge needs an API key to upload; the server runs with debug off; uploads are restricted to known evidence kinds and extensions; the uid and camera ID are validated (no path tricks); uploads are size-limited (64 MB).
* **Sample data** from `server.seed` is marked *Sample* in the UI and uses district code `00`, which is never issued, so it cannot match a real vehicle. Remove it with `--purge`.
* Notices generated by this project are labelled as a demonstration, not official documents.
