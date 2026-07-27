"""Calibrated, demo-only approximation of FACEIT Rating & Round Swing.

FACEIT's production model is not public.  This module therefore uses a
transparent two-stage approximation:

1. a regularized linear baseline over round-normalized demo statistics;
2. a smooth radial-basis residual calibration over supplied FACEIT scoreboard
   references.

Calibration labels/player names are stored only for auditability and are never
used as prediction inputs.  Additional reference demos can be appended to the
JSON calibration file without changing the prediction code.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

CALIBRATION_PATH = Path(__file__).with_name("faceit_rating_calibration.json")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def build_feature_mapping(stats: Mapping[str, Any]) -> dict[str, float]:
    """Create the model's round-normalized feature mapping from one stat row."""
    rounds = max(_number(stats.get("n_rounds"), 0.0), 1.0)
    return {
        "kills_per_round": _number(stats.get("kills_per_round"), _number(stats.get("kills")) / rounds),
        "deaths_per_round": _number(stats.get("deaths_per_round"), _number(stats.get("deaths")) / rounds),
        "assists_per_round": _number(stats.get("assists_per_round"), _number(stats.get("assists")) / rounds),
        "flash_per_round": _number(stats.get("flash_assists")) / rounds,
        "adr": _number(stats.get("adr")),
        "kast": _number(stats.get("kast")),
        "impact": _number(stats.get("impact")),
        "opening_kills_pr": _number(stats.get("opening_kills")) / rounds,
        "opening_deaths_pr": _number(stats.get("opening_deaths")) / rounds,
        "trade_kills_pr": _number(stats.get("trade_kills")) / rounds,
        "clutch_attempts_pr": _number(stats.get("clutch_attempts")) / rounds,
        "clutches_won_pr": _number(stats.get("clutches_won")) / rounds,
        "multi_kill_2k_pr": _number(stats.get("multi_kill_2k")) / rounds,
        "multi_kill_3k_pr": _number(stats.get("multi_kill_3k")) / rounds,
        "multi_kill_4k_pr": _number(stats.get("multi_kill_4k")) / rounds,
        "multi_kill_5k_pr": _number(stats.get("multi_kill_5k")) / rounds,
        "round_mvps_pr": _number(stats.get("round_mvps")) / rounds,
        "headshots_pr": _number(stats.get("headshots")) / rounds,
        "headshot_pct": _number(stats.get("headshot_pct")),
        "team_win_rate": _number(stats.get("team_win_rate")),
    }


class _CalibratedRBFModel:
    def __init__(self, payload: dict[str, Any]):
        self.version = str(payload.get("version") or "unknown")
        self.features = tuple(str(v) for v in payload["features"])
        anchors = payload["anchors"]
        self.x = np.asarray([anchor["features"] for anchor in anchors], dtype=float)
        self.y_rating = np.asarray([anchor["rating"] for anchor in anchors], dtype=float)
        self.y_swing = np.asarray([anchor["round_swing"] for anchor in anchors], dtype=float)
        self.labels = tuple(str(anchor.get("player_label") or "") for anchor in anchors)
        self.sources = tuple(str(anchor.get("source_match") or "") for anchor in anchors)

        self.mean = self.x.mean(axis=0)
        self.scale = self.x.std(axis=0)
        self.scale[self.scale < 1e-9] = 1.0
        self.z = (self.x - self.mean) / self.scale

        alpha = float(payload.get("base_ridge_alpha", 0.05))
        self.length_scale = max(float(payload.get("rbf_length_scale", 2.0)), 1e-6)
        regularization = max(float(payload.get("rbf_regularization", 1e-10)), 0.0)

        design = np.column_stack([np.ones(len(self.z)), self.z])
        penalty = np.eye(design.shape[1]) * alpha
        penalty[0, 0] = 0.0
        gram = design.T @ design + penalty
        self.beta_rating = np.linalg.solve(gram, design.T @ self.y_rating)
        self.beta_swing = np.linalg.solve(gram, design.T @ self.y_swing)

        distances2 = np.sum((self.z[:, None, :] - self.z[None, :, :]) ** 2, axis=2)
        kernel = np.exp(-distances2 / (2.0 * self.length_scale**2))
        system = kernel + np.eye(len(kernel)) * regularization
        rating_residual = self.y_rating - design @ self.beta_rating
        swing_residual = self.y_swing - design @ self.beta_swing
        self.weight_rating = np.linalg.solve(system, rating_residual)
        self.weight_swing = np.linalg.solve(system, swing_residual)

    def vector(self, mapping: Mapping[str, Any]) -> np.ndarray:
        return np.asarray([_number(mapping.get(name)) for name in self.features], dtype=float)

    def predict(self, mapping: Mapping[str, Any]) -> tuple[float, float, float]:
        x = self.vector(mapping)
        z = (x - self.mean) / self.scale
        design = np.r_[1.0, z]
        distances2 = np.sum((self.z - z) ** 2, axis=1)
        kernel = np.exp(-distances2 / (2.0 * self.length_scale**2))
        rating = float(design @ self.beta_rating + kernel @ self.weight_rating)
        swing = float(design @ self.beta_swing + kernel @ self.weight_swing)
        distance = float(np.sqrt(max(float(distances2.min()), 0.0)))
        return min(2.50, max(0.20, rating)), min(25.0, max(-25.0, swing)), distance

    def calibration_errors(self) -> dict[str, float]:
        rating_errors: list[float] = []
        swing_errors: list[float] = []
        for row, target_rating, target_swing in zip(self.x, self.y_rating, self.y_swing):
            mapping = dict(zip(self.features, row))
            rating, swing, _ = self.predict(mapping)
            rating_errors.append(abs(rating - float(target_rating)))
            swing_errors.append(abs(swing - float(target_swing)))
        return {
            "rating_mae": float(np.mean(rating_errors)),
            "rating_max_error": float(np.max(rating_errors)),
            "round_swing_mae": float(np.mean(swing_errors)),
            "round_swing_max_error": float(np.max(swing_errors)),
            "anchors": float(len(self.x)),
        }


@lru_cache(maxsize=1)
def model() -> _CalibratedRBFModel:
    payload = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    return _CalibratedRBFModel(payload)


def predict_faceit(stats: Mapping[str, Any]) -> tuple[float, float, float, str]:
    """Return ``rating, round_swing, calibration_distance, model_version``."""
    features = build_feature_mapping(stats)
    rating, swing, distance = model().predict(features)
    return rating, swing, distance, model().version


def calibration_errors() -> dict[str, float]:
    return model().calibration_errors()
