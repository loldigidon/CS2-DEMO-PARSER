from __future__ import annotations

import pandas as pd

from config import ParserConfig
from cs2parser.positions import POSITIONS_COLUMNS, SAVED_TICK_COLUMNS, positions_sampled, saved_ticks


def test_positions_are_enabled_by_default():
    assert ParserConfig().with_positions is True


def test_saved_ticks_keeps_only_core_columns():
    ticks = pd.DataFrame(
        [
            {
                "round_num": 1,
                "tick": 16,
                "steamid": "1",
                "name": "player",
                "health": 100,
                "is_alive": True,
                "balance": 800,
                "current_equip_value": 1500,
                "team_name": "CT",
                "team_clan_name": "A",
                "X": 1.0,
                "Y": 2.0,
            }
        ]
    )

    out = saved_ticks(ticks)

    assert list(out.columns) == SAVED_TICK_COLUMNS
    assert "X" not in out.columns
    assert out.iloc[0]["steamid"] == "1"


def test_positions_sampled_returns_sampled_spatial_rows():
    ticks = pd.DataFrame(
        [
            {
                "round_num": 1,
                "tick": 8,
                "steamid": "1",
                "name": "player",
                "team_name": "CT",
                "team_clan_name": "A",
                "X": 10.0,
                "Y": 20.0,
                "Z": 30.0,
                "yaw": 40.0,
                "pitch": 50.0,
                "is_alive": True,
            },
            {
                "round_num": 1,
                "tick": 9,
                "steamid": "1",
                "name": "player",
                "team_name": "CT",
                "team_clan_name": "A",
                "X": 11.0,
                "Y": 21.0,
                "Z": 31.0,
                "yaw": 41.0,
                "pitch": 51.0,
                "is_alive": True,
            },
        ]
    )

    out = positions_sampled(
        ticks,
        ParserConfig(with_positions=True, position_sample=8),
    )

    assert list(out.columns) == POSITIONS_COLUMNS
    assert len(out) == 1
    assert out.iloc[0]["side"] == "ct"
    assert out.iloc[0]["X"] == 10.0
