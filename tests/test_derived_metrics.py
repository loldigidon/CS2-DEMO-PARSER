"""Тесты производных метрик раунда: clutches, trades, opening_kills, buy_outcomes.

Все тесты строят синтетические kills/rounds/ticks/buys DataFrames — awpy не нужен.
Имена колонок соответствуют реальной схеме awpy (см.
docs/superpowers/specs/2026-06-22-derived-stats-fix-design.md).
"""
from __future__ import annotations

import pandas as pd
import pytest

from cs2parser.derived import (
    _team_side_per_round,
    clutches,
    trades,
    opening_kills,
    buy_outcomes,
    clutch_attempts,
)
from config import ParserConfig


# ---------------------------------------------------------------------------
# Фабрики синтетических таблиц
# ---------------------------------------------------------------------------

def _kills_df(rows: list[dict]) -> pd.DataFrame:
    """Минимальный kills с колонками, нужными производным метрикам."""
    base_cols = [
        "round_num", "tick",
        "attacker_name", "attacker_steamid", "attacker_side", "attacker_team_clan_name",
        "victim_name", "victim_steamid", "victim_side", "victim_team_clan_name",
        "ct_team_clan_name", "t_team_clan_name",
        "weapon",
    ]
    df = pd.DataFrame(rows)
    for c in base_cols:
        if c not in df.columns:
            df[c] = None
    return df[base_cols]


def _rounds_df(rows: list[dict]) -> pd.DataFrame:
    base_cols = ["round_num", "start", "freeze_end", "end", "winner", "reason"]
    df = pd.DataFrame(rows)
    for c in base_cols:
        if c not in df.columns:
            df[c] = None
    return df[base_cols]


def _ticks_df(rows: list[dict]) -> pd.DataFrame:
    base_cols = ["round_num", "tick", "steamid", "name", "side", "team_clan_name",
                 "is_alive", "health"]
    df = pd.DataFrame(rows)
    for c in base_cols:
        if c not in df.columns:
            df[c] = None
    return df[base_cols]


# ---------------------------------------------------------------------------
# Task 2: _team_side_per_round
# ---------------------------------------------------------------------------

def test_team_side_per_round_basic():
    """Каждая команда получает side по kills.ct_team_clan_name / t_team_clan_name."""
    kills = _kills_df([
        {"round_num": 1, "tick": 100,
         "attacker_name": "A1", "attacker_side": "ct",
         "attacker_team_clan_name": "TM1",
         "victim_name": "B1", "victim_side": "t",
         "victim_team_clan_name": "TM2",
         "ct_team_clan_name": "TM1", "t_team_clan_name": "TM2"},
    ])
    rounds = _rounds_df([{"round_num": 1, "winner": "ct"}])
    out = _team_side_per_round(kills, rounds)
    mapping = {(r.round_num, r.team_clan_name): r.side for r in out.itertuples()}
    assert mapping == {(1, "TM1"): "ct", (1, "TM2"): "t"}


def test_team_side_per_round_halftime_switch():
    """Смена сторон на halftime: TM1 в r1 на CT, в r13 на T."""
    kills = _kills_df([
        {"round_num": 1, "tick": 100,
         "attacker_team_clan_name": "TM1", "attacker_side": "ct",
         "victim_team_clan_name": "TM2", "victim_side": "t",
         "ct_team_clan_name": "TM1", "t_team_clan_name": "TM2"},
        {"round_num": 13, "tick": 100,
         "attacker_team_clan_name": "TM1", "attacker_side": "t",
         "victim_team_clan_name": "TM2", "victim_side": "ct",
         "ct_team_clan_name": "TM2", "t_team_clan_name": "TM1"},
    ])
    rounds = _rounds_df([{"round_num": 1, "winner": "ct"},
                         {"round_num": 13, "winner": "t"}])
    out = _team_side_per_round(kills, rounds)
    mapping = {(r.round_num, r.team_clan_name): r.side for r in out.itertuples()}
    assert mapping == {(1, "TM1"): "ct", (1, "TM2"): "t",
                       (13, "TM1"): "t", (13, "TM2"): "ct"}


def test_team_side_per_round_round_without_kills_skipped():
    """Раунд без убийств — нет данных о сторонах → строк для него нет."""
    kills = _kills_df([{"round_num": 1, "tick": 100,
                        "attacker_team_clan_name": "TM1", "attacker_side": "ct",
                        "victim_team_clan_name": "TM2", "victim_side": "t",
                        "ct_team_clan_name": "TM1", "t_team_clan_name": "TM2"}])
    rounds = _rounds_df([{"round_num": 1, "winner": "ct"},
                         {"round_num": 2, "winner": "t"}])  # r2: no kills
    out = _team_side_per_round(kills, rounds)
    assert set(out["round_num"]) == {1}  # r2 absent


def test_team_side_per_round_empty_input():
    """Пустые kills/rounds → пустой DataFrame с правильными колонками."""
    out = _team_side_per_round(pd.DataFrame(), pd.DataFrame())
    assert list(out.columns) == ["round_num", "team_clan_name", "side"]
    assert out.empty


def test_trades_marks_trade_kill_and_returns_row():
    kills = _kills_df([
        {"round_num": 1, "tick": 100,
         "attacker_name": "T1", "attacker_steamid": "t1", "attacker_side": "t",
         "attacker_team_clan_name": "TM_T",
         "victim_name": "CT1", "victim_steamid": "ct1", "victim_side": "ct",
         "victim_team_clan_name": "TM_CT",
         "ct_team_clan_name": "TM_CT", "t_team_clan_name": "TM_T",
         "weapon": "ak47"},
        {"round_num": 1, "tick": 190,
         "attacker_name": "CT2", "attacker_steamid": "ct2", "attacker_side": "ct",
         "attacker_team_clan_name": "TM_CT",
         "victim_name": "T1", "victim_steamid": "t1", "victim_side": "t",
         "victim_team_clan_name": "TM_T",
         "ct_team_clan_name": "TM_CT", "t_team_clan_name": "TM_T",
         "weapon": "m4a1_silencer"},
    ])
    tables = {"kills": kills}

    out = trades(tables, ParserConfig(tickrate=128, trade_seconds=3.0))

    assert len(out) == 1
    row = out.iloc[0]
    assert row["trader_name"] == "CT2"
    assert row["traded_for_name"] == "CT1"
    assert row["trade_ticks"] == 90
    assert kills["is_trade"].tolist() == [False, True]


def test_opening_kills_includes_identity_and_round_outcome():
    kills = _kills_df([
        {"round_num": 1, "tick": 100,
         "attacker_name": "A", "attacker_steamid": "a", "attacker_side": "ct",
         "attacker_team_clan_name": "TM_CT",
         "victim_name": "B", "victim_steamid": "b", "victim_side": "t",
         "victim_team_clan_name": "TM_T",
         "ct_team_clan_name": "TM_CT", "t_team_clan_name": "TM_T",
         "weapon": "usp_silencer"},
    ])
    rounds = _rounds_df([{"round_num": 1, "winner": "ct"}])

    out = opening_kills({"kills": kills, "rounds": rounds})

    assert list(out.columns) == [
        "round_num", "tick", "opener", "opener_steamid", "opener_side",
        "opener_team_clan_name", "opened_on", "opened_on_steamid",
        "victim_side", "victim_team_clan_name", "weapon", "opener_won",
    ]
    row = out.iloc[0]
    assert row["opener"] == "A"
    assert row["opener_steamid"] == "a"
    assert row["opener_team_clan_name"] == "TM_CT"
    assert bool(row["opener_won"]) is True


def test_buy_outcomes_joins_buy_type_to_round_winner():
    kills = _kills_df([
        {"round_num": 1, "tick": 100,
         "attacker_team_clan_name": "TM_CT", "attacker_side": "ct",
         "victim_team_clan_name": "TM_T", "victim_side": "t",
         "ct_team_clan_name": "TM_CT", "t_team_clan_name": "TM_T"},
    ])
    rounds = _rounds_df([{"round_num": 1, "winner": "ct"}])
    buys = pd.DataFrame([
        {"round_num": 1, "team_clan_name": "TM_CT", "equip": 25000, "buy_type": "full"},
        {"round_num": 1, "team_clan_name": "TM_T", "equip": 4000, "buy_type": "eco"},
    ])

    out = buy_outcomes({"kills": kills, "rounds": rounds, "buys": buys})
    by_team = {r.team_clan_name: r for r in out.itertuples()}

    assert by_team["TM_CT"].side == "ct"
    assert bool(by_team["TM_CT"].won) is True
    assert by_team["TM_T"].side == "t"
    assert bool(by_team["TM_T"].won) is False


def test_clutches_identifies_1v5_win_from_ticks():
    kills = _kills_df([
        {"round_num": 1, "tick": 100, "attacker_name": "CT1", "attacker_steamid": "ct1",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T1", "victim_steamid": "t1", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
        {"round_num": 1, "tick": 110, "attacker_name": "CT2", "attacker_steamid": "ct2",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T2", "victim_steamid": "t2", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
        {"round_num": 1, "tick": 120, "attacker_name": "CT3", "attacker_steamid": "ct3",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T3", "victim_steamid": "t3", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
        {"round_num": 1, "tick": 130, "attacker_name": "CT4", "attacker_steamid": "ct4",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T4", "victim_steamid": "t4", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
    ])
    rounds = _rounds_df([{"round_num": 1, "start": 0, "end": 500, "winner": "t"}])
    start_rows = []
    for side, team, prefix in [("ct", "TM_CT", "ct"), ("t", "TM_T", "t")]:
        for i in range(1, 6):
            start_rows.append({
                "round_num": 1, "tick": 0, "steamid": f"{prefix}{i}",
                "name": f"{prefix.upper()}{i}", "side": side,
                "team_clan_name": team, "is_alive": True, "health": 100,
            })
    ticks = _ticks_df(start_rows + [
        {"round_num": 1, "tick": 130, "steamid": "t5", "name": "T5",
         "side": "t", "team_clan_name": "TM_T", "is_alive": True, "health": 100},
        {"round_num": 1, "tick": 500, "steamid": "t5", "name": "T5",
         "side": "t", "team_clan_name": "TM_T", "is_alive": True, "health": 100},
    ])

    out = clutches({"kills": kills, "rounds": rounds, "ticks": ticks})

    assert len(out) == 1
    row = out.iloc[0]
    assert row["clutch_player"] == "T5"
    assert row["clutch_player_steamid"] == "t5"
    assert row["clutch_side"] == "t"
    assert row["clutch_team_clan_name"] == "TM_T"
    assert row["vs_count"] == 5
    assert bool(row["won"]) is True
    assert bool(row["survive"]) is True


def test_clutches_omits_lost_1vn_attempts():
    kills = _kills_df([
        {"round_num": 1, "tick": 100, "attacker_name": "CT1", "attacker_steamid": "ct1",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T1", "victim_steamid": "t1", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
        {"round_num": 1, "tick": 110, "attacker_name": "CT2", "attacker_steamid": "ct2",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T2", "victim_steamid": "t2", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
        {"round_num": 1, "tick": 120, "attacker_name": "CT3", "attacker_steamid": "ct3",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T3", "victim_steamid": "t3", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
        {"round_num": 1, "tick": 130, "attacker_name": "CT4", "attacker_steamid": "ct4",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T4", "victim_steamid": "t4", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
        {"round_num": 1, "tick": 200, "attacker_name": "CT5", "attacker_steamid": "ct5",
         "attacker_side": "ct", "attacker_team_clan_name": "TM_CT",
         "victim_name": "T5", "victim_steamid": "t5", "victim_side": "t",
         "victim_team_clan_name": "TM_T", "ct_team_clan_name": "TM_CT",
         "t_team_clan_name": "TM_T", "weapon": "m4a1_silencer"},
    ])
    rounds = _rounds_df([{"round_num": 1, "start": 0, "end": 250, "winner": "ct"}])
    ticks = _ticks_df([])

    attempts = clutch_attempts({"kills": kills, "rounds": rounds, "ticks": ticks})
    out = clutches({"kills": kills, "rounds": rounds, "ticks": ticks})

    assert list(attempts.columns) == [
        "round_num", "clutch_player", "clutch_player_steamid", "clutch_side",
        "clutch_team_clan_name", "vs_count", "won", "survive",
        "start_tick", "end_tick",
    ]
    assert len(attempts) == 1
    assert bool(attempts.iloc[0]["won"]) is False
    assert out.empty


def test_trades_rejects_unrelated_revenge_kill():
    """A teammate kill is not a trade unless the original killer is the victim."""
    kills = _kills_df([
        {
            "round_num": 1, "tick": 100,
            "attacker_name": "A", "attacker_steamid": "a", "attacker_side": "ct", "attacker_team_clan_name": "CT",
            "victim_name": "B", "victim_steamid": "b", "victim_side": "t", "victim_team_clan_name": "T",
            "ct_team_clan_name": "CT", "t_team_clan_name": "T", "weapon": "m4a1_silencer",
        },
        {
            "round_num": 1, "tick": 150,
            "attacker_name": "B2", "attacker_steamid": "b2", "attacker_side": "t", "attacker_team_clan_name": "T",
            "victim_name": "C", "victim_steamid": "c", "victim_side": "ct", "victim_team_clan_name": "CT",
            "ct_team_clan_name": "CT", "t_team_clan_name": "T", "weapon": "ak47",
        },
    ])
    out = trades({"kills": kills}, ParserConfig(tickrate=64, trade_seconds=3.0))
    assert out.empty
    assert kills["is_trade"].tolist() == [False, False]


def test_trades_finds_trade_with_intervening_kill():
    """An unrelated kill between opening death and refrag must not hide the trade."""
    kills = _kills_df([
        {
            "round_num": 1, "tick": 100,
            "attacker_name": "A", "attacker_steamid": "a", "attacker_side": "ct", "attacker_team_clan_name": "CT",
            "victim_name": "B", "victim_steamid": "b", "victim_side": "t", "victim_team_clan_name": "T",
            "ct_team_clan_name": "CT", "t_team_clan_name": "T", "weapon": "m4a1_silencer",
        },
        {
            "round_num": 1, "tick": 120,
            "attacker_name": "X", "attacker_steamid": "x", "attacker_side": "ct", "attacker_team_clan_name": "CT",
            "victim_name": "Y", "victim_steamid": "y", "victim_side": "t", "victim_team_clan_name": "T",
            "ct_team_clan_name": "CT", "t_team_clan_name": "T", "weapon": "m4a1_silencer",
        },
        {
            "round_num": 1, "tick": 180,
            "attacker_name": "B2", "attacker_steamid": "b2", "attacker_side": "t", "attacker_team_clan_name": "T",
            "victim_name": "A", "victim_steamid": "a", "victim_side": "ct", "victim_team_clan_name": "CT",
            "ct_team_clan_name": "CT", "t_team_clan_name": "T", "weapon": "ak47",
        },
    ])
    out = trades({"kills": kills}, ParserConfig(tickrate=64, trade_seconds=3.0))
    assert len(out) == 1
    assert out.iloc[0]["trader_name"] == "B2"
    assert out.iloc[0]["traded_for_name"] == "B"
    assert out.iloc[0]["victim_name"] == "A"
    assert kills["is_trade"].tolist() == [False, False, True]
