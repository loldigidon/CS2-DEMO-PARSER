"""Tests for strong Parquet output validation in pipeline.already_parsed()."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline import already_parsed


def _write_match(root: Path, match_id: str, round_nums: list[int], *, valid: bool = True) -> None:
    match_dir = root / match_id
    match_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"round_num": round_nums}).to_parquet(match_dir / "rounds.parquet", index=False)
    pd.DataFrame({"round_num": [1], "tick": [1], "steamid": ["76561198000000001"]}).to_parquet(
        match_dir / "ticks.parquet", index=False
    )
    pd.DataFrame({
        "team_clan_name": ["A", "B"],
        "steamid": ["76561198000000001", "76561198000000002"],
        "name": ["p1", "p2"],
    }).to_parquet(match_dir / "teams.parquet", index=False)
    pd.DataFrame({
        "check": ["rounds_present"],
        "status": ["pass" if valid else "fail"],
        "severity": ["info" if valid else "error"],
        "details": ["ok" if valid else "bad"],
    }).to_parquet(match_dir / "validation.parquet", index=False)
    pd.DataFrame({"tickrate": [64]}).to_parquet(match_dir / "parse_metadata.parquet", index=False)


def test_clean_match_is_parsed(tmp_path):
    _write_match(tmp_path, "m1", list(range(1, 25)))
    assert already_parsed(["m1"], tmp_path) is True


def test_duplicated_match_is_not_parsed(tmp_path):
    _write_match(tmp_path, "m1", [1, 1, 2, 2, 3, 3])
    assert already_parsed(["m1"], tmp_path) is False


def test_gapped_match_is_not_parsed(tmp_path):
    _write_match(tmp_path, "m1", [1, 2, 4, 5])
    assert already_parsed(["m1"], tmp_path) is False


def test_validation_error_is_not_parsed(tmp_path):
    _write_match(tmp_path, "m1", list(range(1, 13)), valid=False)
    assert already_parsed(["m1"], tmp_path) is False


def test_missing_core_table_is_not_parsed(tmp_path):
    _write_match(tmp_path, "m1", list(range(1, 13)))
    (tmp_path / "m1" / "ticks.parquet").unlink()
    assert already_parsed(["m1"], tmp_path) is False


def test_bo3_all_maps_required(tmp_path):
    _write_match(tmp_path, "m1-a", list(range(1, 25)))
    _write_match(tmp_path, "m1-b", list(range(1, 25)))
    assert already_parsed(["m1-a", "m1-b", "m1-c"], tmp_path) is False


def test_bo3_all_clean_is_parsed(tmp_path):
    for match_id in ("m1-a", "m1-b", "m1-c"):
        _write_match(tmp_path, match_id, list(range(1, 25)))
    assert already_parsed(["m1-a", "m1-b", "m1-c"], tmp_path) is True


def test_overtime_match_is_parsed(tmp_path):
    _write_match(tmp_path, "m1", list(range(1, 43)))
    assert already_parsed(["m1"], tmp_path) is True
