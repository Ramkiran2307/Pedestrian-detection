import numpy as np
import pytest

from edge.plate_ocr import constrained_decode, fuse, tight_crop

ALPHA = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_"


def matrix(spec):
    """spec: list of {char: prob} per slot (10 slots); the rest of each row is spread thin."""
    P = np.full((10, len(ALPHA)), 1e-4)
    for i, row in enumerate(spec):
        for ch, p in row.items():
            P[i, ALPHA.index(ch)] = p
    return P / P.sum(axis=1, keepdims=True)


def test_digit_and_letter_slots_resolve_confusions():
    # the model is torn between B/8 and O/0 in several places; the Indian layout decides
    spec = [{"K": .9}, {"A": .9}, {"O": .5, "0": .4}, {"1": .9}, {"A": .9}, {"8": .5, "B": .45},
            {"1": .9}, {"2": .9}, {"3": .9}, {"B": .5, "8": .45}]
    best = constrained_decode(matrix(spec), ALPHA)[0]
    assert best.text == "KA01AB1238" and best.fmt == "standard"


def test_invalid_state_code_is_replaced_by_closest_real_one():
    spec = [{"K": .6, "X": .3}, {"X": .5, "A": .4}, {"0": .9}, {"5": .9}, {"M": .9}, {"N": .9},
            {"4": .9}, {"3": .9}, {"2": .9}, {"1": .9}]
    assert constrained_decode(matrix(spec), ALPHA)[0].text == "KA05MN4321"


def test_bh_series():
    spec = [{"2": .9}, {"2": .9}, {"B": .9}, {"H": .9}, {"1": .9}, {"2": .9}, {"3": .9}, {"4": .9},
            {"A": .9}, {"A": .9}]
    best = constrained_decode(matrix(spec), ALPHA)[0]
    assert best.text == "22BH1234AA" and best.fmt == "bh"


def test_common_layout_beats_rare_short_one_when_unsure():
    # positions 8-9 are uncertain between "pad" and digits: prefer the common 10-character layout
    spec = [{"A": .9}, {"P": .9}, {"0": .8}, {"9": .8}, {"B": .8}, {"D": .8}, {"2": .8}, {"2": .8},
            {"2": .5, "_": .45}, {"2": .5, "_": .45}]
    assert constrained_decode(matrix(spec), ALPHA)[0].text == "AP09BD2222"


def test_alternative_layouts_are_distinct_and_best_first():
    # (character-level alternatives for the officer come from the per-frame vote in PlateOCR.read)
    spec = [{"K": .9}, {"A": .9}, {"0": .9}, {"1": .9}, {"A": .9}, {"B": .9}, {"1": .9}, {"2": .9},
            {"3": .55, "8": .4}, {"4": .9}]
    cands = constrained_decode(matrix(spec), ALPHA, top=3)
    assert cands[0].text == "KA01AB1234"
    assert len({c.text for c in cands}) == len(cands) == 3


def plate_image(rng, dx=0, dy=0):
    img = np.full((24, 60, 3), 60, np.uint8)
    img[3:21, 4:56] = 235                                     # white plate
    for k, x in enumerate(range(8, 52, 6)):                   # dark "characters"
        img[7 + dy:17 + dy, x + dx:x + dx + 3] = 20 + 10 * (k % 3)
    return np.clip(img + rng.normal(0, 18, img.shape), 0, 255).astype(np.uint8)


def test_fusion_reduces_noise():
    rng = np.random.default_rng(0)
    frames = [plate_image(rng, dx=int(rng.integers(-1, 2)), dy=int(rng.integers(-1, 2))) for _ in range(10)]
    fused, used = fuse(frames, scale=2)
    assert used >= 6                                          # most frames aligned and combined
    single = frames[0].astype(np.float32)
    # noise on the flat white strip above the characters: fused is smoother than one frame
    patch = lambda im, s: im[int(4 * s):int(6 * s), int(5 * s):int(50 * s)].astype(np.float32).std()
    assert patch(fused, 2) < patch(single, 1)


def test_tight_crop_trims_detector_padding():
    rng = np.random.default_rng(1)
    img = plate_image(rng)
    t = tight_crop(img)
    assert t.shape[0] < img.shape[0] and t.shape[1] < img.shape[1]


def test_plate_models_read_a_clean_synthetic_plate():
    pytest.importorskip("fast_plate_ocr")
    import random

    import matplotlib
    from pathlib import Path

    from edge.plate_ocr import PlateOCR
    from tools.eval_plate_ocr import degrade, render

    font = str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans-Bold.ttf")
    try:
        ocr = PlateOCR(["cct-s-v2-global-model"])
    except Exception as e:  # first run needs to download the model
        pytest.skip(f"plate OCR model unavailable: {e}")
    rng = np.random.default_rng(3)
    frames = [degrade(render("KA05MN4321", font, False), 36, rng) for _ in range(8)]
    res = ocr.read(frames)
    assert res.candidates[0].text == "KA05MN4321"
    assert res.enhanced.shape[0] > frames[0].shape[0]       # enhanced image is upscaled
