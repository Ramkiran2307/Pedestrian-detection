from edge.plates import join_fragments, normalize_indian_plate


def test_standard_plate_passes_through():
    g = normalize_indian_plate("KA01AB1234")
    assert g.valid and g.text == "KA01AB1234" and g.fixes == 0 and g.pretty == "KA 01 AB 1234"


def test_spaces_and_symbols_are_removed():
    assert normalize_indian_plate("ka-01 ab.1234").text == "KA01AB1234"


def test_position_based_ocr_fixes():
    # 0/O, 1/I, 8/B, 5/S confusions are fixed according to the slot type
    g = normalize_indian_plate("KAO1A81234")
    assert g.valid and g.text == "KA01AB1234" and g.fixes == 2
    assert normalize_indian_plate("MH12AB5S78").text == "MH12AB5578"


def test_ind_prefix_on_hsrp_plates():
    g = normalize_indian_plate("INDTN09BC4321")
    assert g.valid and g.text == "TN09BC4321"


def test_delhi_single_digit_district_and_short_series():
    assert normalize_indian_plate("DL3CAB1234").valid
    assert normalize_indian_plate("MH12A1234").valid


def test_bh_series():
    g = normalize_indian_plate("22BH1234AA")
    assert g.valid and g.fmt == "bh" and g.pretty == "22 BH 1234 AA"


def test_garbage_is_invalid():
    assert not normalize_indian_plate("HELLO").valid
    assert not normalize_indian_plate("").valid


def test_two_line_bike_plate_is_joined_in_reading_order():
    # second line listed first; it must still come out top line first
    frags = [
        ([[0, 40], [100, 40], [100, 70], [0, 70]], "AB1234", 0.9),
        ([[10, 0], [90, 0], [90, 30], [10, 30]], "KA01", 0.8),
    ]
    text, conf = join_fragments(frags)
    assert text == "KA01AB1234"
    assert abs(conf - 0.85) < 1e-6
