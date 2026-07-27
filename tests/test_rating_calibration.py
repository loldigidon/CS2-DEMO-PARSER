from cs2parser.advanced import calibration_errors as advanced_calibration_errors
from cs2parser.advanced import model as advanced_model
from cs2parser.rating import calibration_errors, model


def test_faceit_rating_calibration_max_error_is_below_one_hundredth():
    errors = calibration_errors()
    assert errors["anchors"] == 70
    assert errors["rating_max_error"] <= 0.01
    assert errors["round_swing_max_error"] <= 0.01


def test_calibration_prediction_uses_features_not_player_labels():
    calibrated = model()
    row = dict(zip(calibrated.features, calibrated.x[0]))
    first = calibrated.predict(row)
    # Labels are audit metadata and changing them cannot affect the prediction.
    original = calibrated.labels
    calibrated.labels = tuple("renamed" for _ in original)
    try:
        second = calibrated.predict(row)
    finally:
        calibrated.labels = original
    assert first == second


def test_advanced_calibration_matches_all_five_reference_matches():
    errors = advanced_calibration_errors()
    assert errors["anchors"] == 50
    assert errors["rws_max_error"] <= 0.01
    assert errors["single_max_error"] <= 0.1


def test_advanced_calibration_uses_demo_features_only():
    calibrated = advanced_model()
    mapping = dict(zip(calibrated.features, calibrated.x[0]))
    first = calibrated.predict(mapping)
    mapping["player"] = "renamed"
    mapping["match_id"] = "different"
    second = calibrated.predict(mapping)
    assert first == second
