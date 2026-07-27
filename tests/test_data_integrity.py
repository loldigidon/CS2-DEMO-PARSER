"""Regression tests for exact IDs, tickrate, bomb plants, and event filtering."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import polars as pl

from cs2parser.demo import _to_pandas, detect_tickrate
from cs2parser.events import common_event_tables
from cs2parser.rounds import fix_round_bomb_sites


def test_nullable_uint64_steamid_is_never_converted_to_float():
    exact = 76561199015906221
    source = pl.DataFrame({
        "attacker_steamid": pl.Series([exact, None], dtype=pl.UInt64),
        "victim_steamid": pl.Series([None, exact], dtype=pl.UInt64),
    })
    out = _to_pandas(source)
    assert out["attacker_steamid"].tolist() == [str(exact), pd.NA]
    assert out["victim_steamid"].tolist() == [pd.NA, str(exact)]
    assert str(out["attacker_steamid"].dtype) == "string"


def test_tickrate_is_detected_from_game_time():
    class Parser:
        def parse_ticks(self, wanted_props):
            assert wanted_props == ["game_time"]
            return pl.DataFrame({"tick": [0, 64, 128, 192], "game_time": [0.0, 1.0, 2.0, 3.0]})

    assert detect_tickrate(SimpleNamespace(parser=Parser())) == 64


def test_common_event_tables_remove_warmup_but_raw_can_stay_complete():
    events = {
        "player_footstep": pd.DataFrame({
            "tick": [10, 100], "round_num": pd.Series([pd.NA, 1], dtype="Int64"),
            "user_steamid": ["1", "2"], "user_name": ["warmup", "match"],
        }),
        "player_blind": pd.DataFrame({
            "tick": [20, 120], "round_num": pd.Series([pd.NA, 1], dtype="Int64"),
        }),
    }
    root = common_event_tables(events)
    assert len(events["player_footstep"]) == 2
    assert root["footsteps"]["name"].tolist() == ["match"]
    assert root["flashes"]["tick"].tolist() == [120]


def test_bomb_site_uses_active_plant_and_counts_postround_events():
    rounds = pd.DataFrame([{
        "round_num": 1, "freeze_end": 100, "end": 500, "official_end": 550,
        "bomb_site": "bombsite_b",
    }])
    bomb = pd.DataFrame([
        {"round_num": 1, "tick": 400, "event": "plant", "bombsite": "A"},
        {"round_num": 1, "tick": 525, "event": "plant", "bombsite": "B"},
    ])
    out = fix_round_bomb_sites(rounds, bomb)
    assert out.iloc[0]["bomb_site"] == "bombsite_a"
    assert out.iloc[0]["bomb_plant"] == 400
    assert str(out["bomb_plant"].dtype) == "Int64"
    assert out.iloc[0]["postround_plant_count"] == 1


def test_dem_zst_materialization_roundtrip(tmp_path):
    import zstandard as zstd
    from cs2parser.input import demo_match_id, find_demo_files, materialized_demo

    payload = b"HL2DEMO" + bytes(range(64))
    compressed = tmp_path / "example.dem.zst"
    compressed.write_bytes(zstd.ZstdCompressor().compress(payload))

    assert demo_match_id(compressed) == "example"
    assert find_demo_files(tmp_path) == [compressed]
    with materialized_demo(compressed) as materialized:
        assert materialized.name == "example.dem"
        assert materialized.read_bytes() == payload
        temp_parent = materialized.parent
    assert not temp_parent.exists()
