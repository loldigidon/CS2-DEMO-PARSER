"""Tests for fully local player statistics derived from demo tables."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from config import ParserConfig
from cs2parser.players import EXPECTED_COLUMNS, player_stats
from cs2parser.storage import save_all


def _tables() -> dict[str, pd.DataFrame]:
    ticks = pd.DataFrame([
        {"round_num": 1, "tick": 200, "steamid": "76561199015906221", "name": "Alice", "side": "ct", "team_clan_name": "A", "is_alive": True, "health": 60},
        {"round_num": 1, "tick": 200, "steamid": "76561198000000002", "name": "Bob", "side": "t", "team_clan_name": "B", "is_alive": False, "health": 0},
    ])
    kills = pd.DataFrame([{
        "round_num": 1, "tick": 100,
        "attacker_name": "Alice", "attacker_steamid": "76561199015906221", "attacker_side": "ct", "attacker_team_clan_name": "A",
        "victim_name": "Bob", "victim_steamid": "76561198000000002", "victim_side": "t", "victim_team_clan_name": "B",
        "assister_name": None, "assister_steamid": None, "assister_side": None,
        "weapon": "m4a1_silencer", "headshot": True,
    }])
    damages = pd.DataFrame([{
        "round_num": 1,
        "attacker_name": "Alice", "attacker_steamid": "76561199015906221", "attacker_side": "ct", "attacker_team_clan_name": "A",
        "victim_name": "Bob", "victim_steamid": "76561198000000002", "victim_side": "t", "victim_team_clan_name": "B",
        "dmg_health_real": 100,
    }])
    openings = pd.DataFrame([{
        "round_num": 1, "opener": "Alice", "opener_steamid": "76561199015906221", "opener_side": "ct",
        "opened_on": "Bob", "opened_on_steamid": "76561198000000002", "victim_side": "t",
    }])
    attempts = pd.DataFrame([{
        "round_num": 1, "clutch_player": "Alice", "clutch_player_steamid": "76561199015906221", "clutch_side": "ct",
    }])
    clutches = attempts.copy()
    return {
        "ticks": ticks,
        "kills": kills,
        "damages": damages,
        "trades": pd.DataFrame(),
        "opening_kills": openings,
        "clutch_attempts": attempts,
        "clutches": clutches,
    }


def test_schema_is_stable_and_complete():
    result = player_stats(SimpleNamespace(), ParserConfig(tickrate=64), _tables())
    assert list(result.columns) == EXPECTED_COLUMNS
    assert set(result["side"]) == {"all", "ct", "t"}


def test_local_stats_values_and_exact_identifier():
    result = player_stats(SimpleNamespace(), ParserConfig(tickrate=64), _tables())
    alice = result[(result["name"] == "Alice") & (result["side"] == "all")].iloc[0]
    bob = result[(result["name"] == "Bob") & (result["side"] == "all")].iloc[0]

    assert alice["steamid"] == "76561199015906221"
    assert alice["kills"] == 1
    assert alice["deaths"] == 0
    assert alice["headshots"] == 1
    assert alice["headshot_pct"] == 100.0
    assert alice["opening_kills"] == 1
    assert alice["entry_attempts"] == 1
    assert alice["entry_difference"] == 1
    assert alice["entry_attempt_pct"] == 100.0
    assert alice["entry_success_pct"] == 100.0
    assert alice["clutch_attempts"] == 1
    assert alice["clutches_won"] == 1
    assert alice["clutch_losses"] == 0
    assert alice["clutch_success_pct"] == 100.0
    assert alice["dmg"] == 100.0
    assert alice["adr"] == 100.0
    assert alice["kast"] == 100.0
    assert pd.notna(alice["rating"])

    assert bob["deaths"] == 1
    assert bob["opening_deaths"] == 1
    assert bob["entry_attempts"] == 1
    assert bob["entry_difference"] == -1
    assert bob["entry_success_pct"] == 0.0
    assert bob["kast"] == 0.0


def test_empty_tables_return_stable_schema():
    out = player_stats(SimpleNamespace(), tables={})
    assert out.empty
    assert list(out.columns) == EXPECTED_COLUMNS


def test_parquet_storage_preserves_schema_and_identifier(tmp_path):
    stats = player_stats(SimpleNamespace(), ParserConfig(tickrate=64), _tables())
    save_all({"player_stats": stats}, str(tmp_path), "m1")
    out = pd.read_parquet(tmp_path / "m1" / "player_stats.parquet")
    assert list(out.columns) == ["match_id", *EXPECTED_COLUMNS]
    assert out.loc[out["name"] == "Alice", "steamid"].iloc[0] == "76561199015906221"


def test_world_deaths_count_and_flash_assists_stay_separate():
    tables = _tables()
    tables["ticks"] = pd.concat([
        tables["ticks"],
        pd.DataFrame([{
            "round_num": 1, "tick": 200, "steamid": "76561198000000003",
            "name": "Charlie", "side": "ct", "team_clan_name": "A",
            "is_alive": True, "health": 100,
        }]),
    ], ignore_index=True)
    tables["kills"] = pd.DataFrame([
        {
            "round_num": 1, "tick": 100,
            "attacker_name": "Alice", "attacker_steamid": "76561199015906221",
            "attacker_side": "ct", "attacker_team_clan_name": "A",
            "victim_name": "Bob", "victim_steamid": "76561198000000002",
            "victim_side": "t", "victim_team_clan_name": "B",
            "assister_name": "Charlie", "assister_steamid": "76561198000000003",
            "assister_side": "ct", "assistedflash": True,
            "weapon": "m4a1_silencer", "headshot": False,
        },
        {
            "round_num": 1, "tick": 150,
            "attacker_name": None, "attacker_steamid": None,
            "attacker_side": None, "attacker_team_clan_name": None,
            "victim_name": "Alice", "victim_steamid": "76561199015906221",
            "victim_side": "ct", "victim_team_clan_name": "A",
            "assister_name": None, "assister_steamid": None,
            "assister_side": None, "assistedflash": False,
            "weapon": "world", "headshot": False,
        },
    ])

    result = player_stats(SimpleNamespace(), ParserConfig(tickrate=64), tables)
    alice = result[(result["name"] == "Alice") & (result["side"] == "all")].iloc[0]
    charlie = result[(result["name"] == "Charlie") & (result["side"] == "all")].iloc[0]

    assert alice["deaths"] == 1
    assert charlie["assists"] == 0
    assert charlie["flash_assists"] == 1
