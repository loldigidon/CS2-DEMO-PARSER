"""Tests for Parquet-only storage semantics."""
from __future__ import annotations

import pandas as pd

from cs2parser.storage import KNOWN_TABLES, parquet_table_path, save_all


def test_known_tables_is_complete_set():
    expected = {
        "rounds", "round_sides", "kills", "damages", "shots", "fire_bullets",
        "grenades", "smokes", "infernos", "bomb", "footsteps", "flashes",
        "reloads", "ticks", "positions_sampled", "teams", "buys",
        "opening_kills", "clutch_attempts", "clutches", "player_stats",
        "trades", "buy_outcomes", "event_manifest", "parse_metadata",
        "validation", "header",
    }
    assert set(KNOWN_TABLES) == expected


def test_save_all_writes_parquet_files(sample_tables, output_root):
    save_all(sample_tables, str(output_root), "m1")

    rounds = pd.read_parquet(parquet_table_path(output_root, "m1", "rounds"))
    teams = pd.read_parquet(parquet_table_path(output_root, "m1", "teams"))

    assert rounds["match_id"].tolist() == ["m1", "m1", "m1"]
    assert rounds["total_damage"].tolist() == [100, 200, 300]
    assert teams["match_id"].tolist() == ["m1", "m1"]
    assert teams["name"].tolist() == ["playerA", "playerB"]


def test_save_all_overwrites_existing_match_data(sample_tables, output_root):
    save_all(sample_tables, str(output_root), "m1")

    updated = {
        "rounds": pd.DataFrame([(1, 200), (2, 400), (3, 600)], columns=["round_num", "total_damage"]),
        "teams": sample_tables["teams"],
    }
    save_all(updated, str(output_root), "m1")

    rounds = pd.read_parquet(parquet_table_path(output_root, "m1", "rounds"))
    assert rounds["total_damage"].tolist() == [200, 400, 600]


def test_matches_are_isolated(sample_tables, output_root):
    save_all(sample_tables, str(output_root), "m1")
    save_all(sample_tables, str(output_root), "m2")

    other = {
        "rounds": pd.DataFrame([(1, 999)], columns=["round_num", "total_damage"]),
        "teams": sample_tables["teams"],
    }
    save_all(other, str(output_root), "m1")

    m1 = pd.read_parquet(parquet_table_path(output_root, "m1", "rounds"))
    m2 = pd.read_parquet(parquet_table_path(output_root, "m2", "rounds"))
    assert m1["total_damage"].tolist() == [999]
    assert m2["total_damage"].tolist() == [100, 200, 300]


def test_none_table_removes_stale_file(sample_tables, output_root):
    full = dict(sample_tables)
    full["footsteps"] = pd.DataFrame([("76561198000000001", 1)], columns=["steamid", "tick"])
    save_all(full, str(output_root), "m1")

    path = parquet_table_path(output_root, "m1", "footsteps")
    assert path.exists()

    save_all({**sample_tables, "footsteps": None}, str(output_root), "m1")

    assert not path.exists()


def test_missing_known_table_removes_stale_file(sample_tables, output_root):
    full = dict(sample_tables)
    full["shots"] = pd.DataFrame({"tick": [100]})
    save_all(full, str(output_root), "m1")
    path = parquet_table_path(output_root, "m1", "shots")
    assert path.exists()

    save_all(sample_tables, str(output_root), "m1")
    assert not path.exists()
