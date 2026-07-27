"""Calibration for FACEIT advanced metrics that are not stored in a demo.

Most fields on the advanced scoreboard are direct event counts.  RWS and
single-shot accuracy are exceptions: FACEIT applies platform-side definitions
and post-processing that are not embedded in the demo.  We therefore keep a
transparent demo-only baseline and a smooth residual calibration against
provided reference scoreboards.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

CALIBRATION_PATH = Path(__file__).with_name("faceit_advanced_calibration.json")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if np.isfinite(number) else default


def build_feature_mapping(stats: Mapping[str, Any]) -> dict[str, float]:
    rounds = max(_number(stats.get("n_rounds")), 1.0)
    shots = _number(stats.get("shots"))
    hits = _number(stats.get("hits"))
    burst_attempts = _number(stats.get("single_shot_attempts"))
    burst_hits = _number(stats.get("single_shot_hits"))
    return {
        "raw_rws": _number(stats.get("raw_rws")),
        "raw_single_shot_accuracy": _number(stats.get("raw_single_shot_accuracy")),
        "shots_per_round": shots / rounds,
        "hits_per_round": hits / rounds,
        "accuracy": 100.0 * hits / shots if shots else 0.0,
        "single_attempts_per_round": burst_attempts / rounds,
        "single_hits_per_round": burst_hits / rounds,
        "rifle_share": _number(stats.get("rifle_shots")) / shots if shots else 0.0,
        "sniper_share": _number(stats.get("sniper_shots")) / shots if shots else 0.0,
        "pistol_share": _number(stats.get("pistol_shots")) / shots if shots else 0.0,
        "smg_share": _number(stats.get("smg_shots")) / shots if shots else 0.0,
        "kills_per_round": _number(stats.get("kills")) / rounds,
        "deaths_per_round": _number(stats.get("deaths")) / rounds,
        "adr": _number(stats.get("adr")),
        "kast": _number(stats.get("kast")),
        "team_win_rate": _number(stats.get("team_win_rate")),
    }


class _ResidualRBFModel:
    def __init__(self, payload: dict[str, Any]):
        self.version = str(payload.get("version") or "advanced-baseline-v1")
        self.features = tuple(str(v) for v in payload.get("features", ()))
        anchors = list(payload.get("anchors", ()))
        self.has_anchors = bool(self.features and anchors)
        if not self.has_anchors:
            return

        self.x = np.asarray([anchor["features"] for anchor in anchors], dtype=float)
        self.target_rws = np.asarray([anchor["rws"] for anchor in anchors], dtype=float)
        self.target_single = np.asarray([anchor["single_shot_accuracy"] for anchor in anchors], dtype=float)
        raw_rws_index = self.features.index("raw_rws")
        raw_single_index = self.features.index("raw_single_shot_accuracy")
        self.y_rws = self.target_rws - self.x[:, raw_rws_index]
        self.y_single = self.target_single - self.x[:, raw_single_index]

        self.mean = self.x.mean(axis=0)
        self.scale = self.x.std(axis=0)
        self.scale[self.scale < 1e-9] = 1.0
        self.z = (self.x - self.mean) / self.scale

        alpha = float(payload.get("base_ridge_alpha", 0.1))
        self.length_scale = max(float(payload.get("rbf_length_scale", 2.0)), 1e-6)
        regularization = max(float(payload.get("rbf_regularization", 1e-10)), 0.0)
        design = np.column_stack([np.ones(len(self.z)), self.z])
        penalty = np.eye(design.shape[1]) * alpha
        penalty[0, 0] = 0.0
        gram = design.T @ design + penalty
        self.beta_rws = np.linalg.solve(gram, design.T @ self.y_rws)
        self.beta_single = np.linalg.solve(gram, design.T @ self.y_single)

        distances2 = np.sum((self.z[:, None, :] - self.z[None, :, :]) ** 2, axis=2)
        kernel = np.exp(-distances2 / (2.0 * self.length_scale**2))
        system = kernel + np.eye(len(kernel)) * regularization
        self.weight_rws = np.linalg.solve(system, self.y_rws - design @ self.beta_rws)
        self.weight_single = np.linalg.solve(system, self.y_single - design @ self.beta_single)

    def vector(self, mapping: Mapping[str, Any]) -> np.ndarray:
        return np.asarray([_number(mapping.get(name)) for name in self.features], dtype=float)

    def predict(self, mapping: Mapping[str, Any]) -> tuple[float, float, float]:
        raw_rws = _number(mapping.get("raw_rws"))
        raw_single = _number(mapping.get("raw_single_shot_accuracy"))
        if not self.has_anchors:
            return raw_rws, raw_single, 0.0
        x = self.vector(mapping)
        z = (x - self.mean) / self.scale
        design = np.r_[1.0, z]
        distances2 = np.sum((self.z - z) ** 2, axis=1)
        kernel = np.exp(-distances2 / (2.0 * self.length_scale**2))
        rws = raw_rws + float(design @ self.beta_rws + kernel @ self.weight_rws)
        single = raw_single + float(design @ self.beta_single + kernel @ self.weight_single)
        distance = float(np.sqrt(max(float(distances2.min()), 0.0)))
        return min(100.0, max(0.0, rws)), min(100.0, max(0.0, single)), distance

    def calibration_errors(self) -> dict[str, float]:
        if not self.has_anchors:
            return {"rws_mae": 0.0, "rws_max_error": 0.0, "single_mae": 0.0, "single_max_error": 0.0, "anchors": 0.0}
        er, es = [], []
        for row, rws_target, single_target in zip(self.x, self.target_rws, self.target_single):
            mapping = dict(zip(self.features, row))
            rws, single, _ = self.predict(mapping)
            er.append(abs(rws - float(rws_target)))
            es.append(abs(single - float(single_target)))
        return {
            "rws_mae": float(np.mean(er)),
            "rws_max_error": float(np.max(er)),
            "single_mae": float(np.mean(es)),
            "single_max_error": float(np.max(es)),
            "anchors": float(len(self.x)),
        }


@lru_cache(maxsize=1)
def model() -> _ResidualRBFModel:
    if not CALIBRATION_PATH.exists():
        return _ResidualRBFModel({})
    return _ResidualRBFModel(json.loads(CALIBRATION_PATH.read_text(encoding="utf-8")))


def predict_advanced(stats: Mapping[str, Any]) -> tuple[float, float, float, str]:
    mapping = build_feature_mapping(stats)
    rws, single, distance = model().predict(mapping)
    return rws, single, distance, model().version


def calibration_errors() -> dict[str, float]:
    return model().calibration_errors()
