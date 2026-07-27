"""Shared fixtures for cs2parser tests."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest


def _rounds_df(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["round_num", "total_damage"])


def _teams_df(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["steamid", "name"])


@pytest.fixture
def sample_tables() -> dict[str, pd.DataFrame]:
    return {
        "rounds": _rounds_df([(1, 100), (2, 200), (3, 300)]),
        "teams": _teams_df([
            ("76561198000000001", "playerA"),
            ("76561198000000002", "playerB"),
        ]),
    }


@pytest.fixture
def output_root(tmp_path) -> Path:
    return tmp_path / "output"
