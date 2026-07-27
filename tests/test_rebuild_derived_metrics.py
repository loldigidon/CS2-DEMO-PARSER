"""Tests for Parquet backfill helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rebuild_derived_metrics import discover_matches, write_selected_to_parquet  # noqa: E402


def test_write_selected_to_parquet_preserves_source_tables(tmp_path):
    match_dir = tmp_path / "m1"
    match_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"round_num": [1]}).to_parquet(match_dir / "rounds.parquet", index=False)

    tables = {
        "kills": pd.DataFrame({"round_num": [1], "tick": [100], "is_trade": [False]}),
        "trades": pd.DataFrame({"round_num": [1], "tick": [120]}),
    }

    write_selected_to_parquet(tables, match_dir, "m1", table_names=("kills", "trades"))

    rounds = pd.read_parquet(match_dir / "rounds.parquet")
    kills = pd.read_parquet(match_dir / "kills.parquet")
    trades = pd.read_parquet(match_dir / "trades.parquet")

    assert rounds.to_dict("records") == [{"round_num": 1}]
    assert kills.to_dict("records") == [{"match_id": "m1", "round_num": 1, "tick": 100, "is_trade": False}]
    assert trades.to_dict("records") == [{"match_id": "m1", "round_num": 1, "tick": 120}]


def test_write_selected_to_parquet_overwrites_existing_old_table(tmp_path):
    match_dir = tmp_path / "m1"
    match_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"winner": ["ct"], "winner_deaths": [4]}).to_parquet(
        match_dir / "clutches.parquet",
        index=False,
    )

    tables = {
        "clutches": pd.DataFrame({
            "round_num": [1],
            "clutch_player": ["xertioN"],
            "clutch_player_steamid": ["765"],
            "clutch_side": ["ct"],
            "clutch_team_clan_name": ["MOUZ"],
            "vs_count": [2],
            "won": [True],
            "survive": [True],
            "start_tick": [100],
            "end_tick": [200],
        })
    }

    write_selected_to_parquet(tables, match_dir, "m1", table_names=("clutches",))

    out = pd.read_parquet(match_dir / "clutches.parquet")
    assert "winner_deaths" not in out.columns
    assert out.iloc[0]["clutch_player"] == "xertioN"
    assert bool(out.iloc[0]["won"]) is True


def test_write_selected_to_parquet_keeps_uint64_ids(tmp_path):
    match_dir = tmp_path / "m1"
    tables = {
        "kills": pd.DataFrame({
            "round_num": [1],
            "tick": [100],
            "attacker_steamid": pd.Series([18446744073709551615], dtype="uint64"),
            "victim_steamid": pd.Series([76561198000000001], dtype="uint64"),
            "is_trade": [False],
        })
    }

    write_selected_to_parquet(tables, match_dir, "m1", table_names=("kills",))

    out = pd.read_parquet(match_dir / "kills.parquet")
    assert int(out.iloc[0]["attacker_steamid"]) == 18446744073709551615
    assert int(out.iloc[0]["victim_steamid"]) == 76561198000000001


def test_discover_matches_accepts_legacy_outputs_without_optional_tables(tmp_path):
    match_dir = tmp_path / "legacy"
    match_dir.mkdir()
    for name in ("kills", "rounds", "ticks", "buys"):
        pd.DataFrame({"round_num": [1]}).to_parquet(match_dir / f"{name}.parquet", index=False)
    assert discover_matches(tmp_path) == [match_dir]
