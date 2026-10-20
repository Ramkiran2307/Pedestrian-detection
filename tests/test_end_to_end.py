"""Full pipeline on the synthetic demo video (real models, ~2 minutes on a laptop CPU).

    RUN_E2E=1 pytest tests/test_end_to_end.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(os.environ.get("RUN_E2E") != "1", reason="set RUN_E2E=1 to run the slow end-to-end test")


def test_demo_video_produces_person_and_vehicle_incidents(tmp_path):
    video = tmp_path / "demo.mp4"
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_demo_video.py"), "--out", str(video)], check=True)

    from edge.config import load_config
    from edge.pipeline import Pipeline

    cfg = load_config(ROOT / "edge" / "config.yaml", {
        "camera": {"source": str(video)},
        "evidence": {"outbox_dir": str(tmp_path / "outbox"), "sent_dir": str(tmp_path / "sent")},
        "preview": {"enabled": False},
    })
    stats = Pipeline(cfg, use_server=False).run()
    metas = [json.loads(p.read_text()) for p in (tmp_path / "outbox").glob("*/meta.json")]
    kinds = sorted(m["offender_type"] for m in metas)
    assert kinds == ["person", "vehicle"], metas
    vehicle = next(m for m in metas if m["offender_type"] == "vehicle")
    assert vehicle["plate"]["text"] == "KA05MN4321"
    assert stats["cache_binned"] >= 1          # the innocent passer-by was binned
    for m in metas:
        assert {"scene", "litter", "clip"} <= set(m["files"])
