"""Производные метрики, которые awpy не отдаёт готовыми.

Сюда входят тип закупки, опенинг-киллы, трейды, исходы закупок и клатчи.
Функции намеренно возвращают DataFrame со стабильной схемой даже на пустом
входе: это защищает batch-парсинг и SQLite append от дрейфа колонок.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from config import ParserConfig
from .normalize import WORLD_WEAPONS, has_value, normalize_side, same_identifier


BUY_COLUMNS = ["round_num", "team_clan_name", "equip", "buy_type"]
TEAM_SIDE_COLUMNS = ["round_num", "team_clan_name", "side"]
OPENING_KILLS_COLUMNS = [
    "round_num", "tick", "opener", "opener_steamid", "opener_side",
    "opener_team_clan_name", "opened_on", "opened_on_steamid",
    "victim_side", "victim_team_clan_name", "weapon", "opener_won",
]
TRADES_COLUMNS = [
    "round_num", "tick", "trader_name", "trader_steamid", "trader_side",
    "trader_team_clan_name", "traded_for_name", "traded_for_steamid",
    "victim_name", "victim_steamid", "victim_side", "victim_team_clan_name",
    "weapon", "trade_ticks",
]
BUY_OUTCOMES_COLUMNS = ["round_num", "team_clan_name", "side", "equip", "buy_type", "won"]
CLUTCH_COLUMNS = [
    "round_num", "clutch_player", "clutch_player_steamid", "clutch_side",
    "clutch_team_clan_name", "vs_count", "won", "survive",
    "start_tick", "end_tick",
]

def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _has_value(value: Any) -> bool:
    return has_value(value)


def _as_int(value: Any) -> int | None:
    if not _has_value(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if not _has_value(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_side(value: Any) -> str | None:
    return normalize_side(value)


def _same_id(left: Any, right: Any) -> bool:
    return same_identifier(left, right)


def _team_for_side(row: pd.Series, side: str | None) -> Any:
    if side == "ct":
        return row.get("ct_team_clan_name")
    if side == "t":
        return row.get("t_team_clan_name")
    return None


def _side_from_row(row: pd.Series, prefix: str) -> str | None:
    side = row.get(f"{prefix}_side")
    if _has_value(side):
        return _normalize_side(side)
    return _normalize_side(row.get(f"{prefix}_team_name"))


def _team_from_row(row: pd.Series, prefix: str) -> Any:
    team = row.get(f"{prefix}_team_clan_name")
    if _has_value(team):
        return team
    return _team_for_side(row, _side_from_row(row, prefix))


def _is_player_kill(row: pd.Series) -> bool:
    weapon = row.get("weapon")
    if _has_value(weapon) and str(weapon).strip().lower() in WORLD_WEAPONS:
        return False
    if _same_id(row.get("attacker_steamid"), row.get("victim_steamid")):
        return False
    attacker_known = _has_value(row.get("attacker_steamid")) or _has_value(row.get("attacker_name"))
    victim_known = _has_value(row.get("victim_steamid")) or _has_value(row.get("victim_name"))
    if not (attacker_known and victim_known):
        return False
    attacker_team = _team_from_row(row, "attacker")
    victim_team = _team_from_row(row, "victim")
    if _has_value(attacker_team) and _has_value(victim_team) and str(attacker_team) == str(victim_team):
        return False
    return True


def _round_winners(rounds: pd.DataFrame) -> dict[int, str]:
    if rounds.empty or not {"round_num", "winner"}.issubset(rounds.columns):
        return {}
    winners: dict[int, str] = {}
    for _, row in rounds.iterrows():
        n = _as_int(row.get("round_num"))
        winner = _normalize_side(row.get("winner"))
        if n is not None and winner:
            winners[n] = winner
    return winners


def _round_nums(rounds: pd.DataFrame) -> set[int] | None:
    if rounds.empty or "round_num" not in rounds.columns:
        return None
    nums = {_as_int(v) for v in rounds["round_num"]}
    return {n for n in nums if n is not None}


def classify_buys(tables: dict[str, pd.DataFrame], cfg: ParserConfig | None = None) -> pd.DataFrame:
    """Classify team equipment at freeze end.

    A low-equipment round is marked ``pistol`` when it is the team's first
    observed round or the team has just switched side. Remaining buckets use
    configurable team-total equipment thresholds.
    """
    cfg = cfg or ParserConfig()
    rounds = tables.get("rounds", pd.DataFrame())
    ticks = tables.get("ticks", pd.DataFrame())
    round_sides = tables.get("round_sides", pd.DataFrame())
    if rounds.empty or ticks.empty:
        return _empty(BUY_COLUMNS)
    if not {"round_num", "freeze_end"}.issubset(rounds.columns):
        return _empty(BUY_COLUMNS)
    if not {"round_num", "tick", "team_clan_name"}.issubset(ticks.columns):
        return _empty(BUY_COLUMNS)

    value_col = "current_equip_value" if "current_equip_value" in ticks.columns else "balance"
    if value_col not in ticks.columns:
        return _empty(BUY_COLUMNS)

    side_lookup: dict[tuple[int, str], str] = {}
    if not round_sides.empty:
        for row in round_sides.itertuples(index=False):
            rn = _as_int(getattr(row, "round_num", None))
            team = getattr(row, "team_clan_name", None)
            side = _normalize_side(getattr(row, "side", None))
            if rn is not None and _has_value(team) and side:
                side_lookup[(rn, str(team))] = side

    rows = []
    previous_side: dict[str, str] = {}
    for _, r in rounds.sort_values("round_num").iterrows():
        n = _as_int(r.get("round_num"))
        freeze = r.get("freeze_end")
        if n is None or not _has_value(freeze):
            continue
        rt = ticks[ticks["round_num"] == r.get("round_num")]
        snap = rt[rt["tick"] == freeze]
        if snap.empty:
            tick_num = pd.to_numeric(rt.get("tick"), errors="coerce")
            freeze_num = _as_float(freeze)
            cand = rt[tick_num >= freeze_num] if freeze_num is not None else rt
            if cand.empty:
                continue
            first_tick = pd.to_numeric(cand["tick"], errors="coerce").min()
            snap = cand[pd.to_numeric(cand["tick"], errors="coerce") == first_tick]

        for team, group in snap.groupby("team_clan_name", dropna=True):
            if not _has_value(team):
                continue
            team_key = str(team)
            equip = float(pd.to_numeric(group[value_col], errors="coerce").fillna(0).sum())
            side = side_lookup.get((n, team_key))
            side_switched = side is not None and previous_side.get(team_key) not in {None, side}
            first_seen = team_key not in previous_side
            if equip < cfg.eco_max and (first_seen or side_switched):
                buy = "pistol"
            elif equip < cfg.eco_max:
                buy = "eco"
            elif equip < cfg.force_max:
                buy = "force"
            else:
                buy = "full"
            rows.append({"round_num": n, "team_clan_name": team, "equip": equip, "buy_type": buy})
            if side is not None:
                previous_side[team_key] = side
    return pd.DataFrame(rows, columns=BUY_COLUMNS)

def _team_side_per_round(kills_df: pd.DataFrame, rounds_df: pd.DataFrame) -> pd.DataFrame:
    """Возвращает [round_num, team_clan_name, side] для раундов с kills.

    Основной источник - `ct_team_clan_name` и `t_team_clan_name` в kills.
    Если этих колонок нет, используется пара `*_team_clan_name` + `*_side`.
    """
    if kills_df.empty or "round_num" not in kills_df.columns:
        return _empty(TEAM_SIDE_COLUMNS)

    valid_rounds = _round_nums(rounds_df)
    mapping: dict[tuple[int, str], dict[str, Any]] = {}

    sort_cols = [c for c in ["round_num", "tick"] if c in kills_df.columns]
    iterable = kills_df.sort_values(sort_cols) if sort_cols else kills_df
    for _, row in iterable.iterrows():
        n = _as_int(row.get("round_num"))
        if n is None or (valid_rounds is not None and n not in valid_rounds):
            continue

        for side, team in (("ct", row.get("ct_team_clan_name")), ("t", row.get("t_team_clan_name"))):
            if _has_value(team):
                mapping.setdefault((n, str(team)), {
                    "round_num": n,
                    "team_clan_name": team,
                    "side": side,
                })

        for prefix in ("attacker", "victim"):
            side = _side_from_row(row, prefix)
            team = _team_from_row(row, prefix)
            if side and _has_value(team):
                mapping.setdefault((n, str(team)), {
                    "round_num": n,
                    "team_clan_name": team,
                    "side": side,
                })

    rows = list(mapping.values())
    if not rows:
        return _empty(TEAM_SIDE_COLUMNS)
    return pd.DataFrame(rows, columns=TEAM_SIDE_COLUMNS).sort_values(
        ["round_num", "side", "team_clan_name"]
    ).reset_index(drop=True)


def opening_kills(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Первое валидное убийство каждого раунда с исходом раунда для опенера."""
    kills = tables.get("kills", pd.DataFrame())
    rounds = tables.get("rounds", pd.DataFrame())
    if kills.empty or not {"round_num", "tick"}.issubset(kills.columns):
        return _empty(OPENING_KILLS_COLUMNS)

    valid = kills[kills.apply(_is_player_kill, axis=1)].copy()
    if valid.empty:
        return _empty(OPENING_KILLS_COLUMNS)

    winners = _round_winners(rounds)
    idx = valid.sort_values(["round_num", "tick"]).groupby("round_num", sort=False).head(1).index
    rows = []
    for _, row in kills.loc[idx].sort_values(["round_num", "tick"]).iterrows():
        n = _as_int(row.get("round_num"))
        opener_side = _side_from_row(row, "attacker")
        victim_side = _side_from_row(row, "victim")
        winner = winners.get(n) if n is not None else None
        opener_won = opener_side == winner if opener_side and winner else pd.NA
        rows.append({
            "round_num": n,
            "tick": _as_int(row.get("tick")),
            "opener": row.get("attacker_name"),
            "opener_steamid": row.get("attacker_steamid"),
            "opener_side": opener_side,
            "opener_team_clan_name": _team_from_row(row, "attacker"),
            "opened_on": row.get("victim_name"),
            "opened_on_steamid": row.get("victim_steamid"),
            "victim_side": victim_side,
            "victim_team_clan_name": _team_from_row(row, "victim"),
            "weapon": row.get("weapon"),
            "opener_won": opener_won,
        })
    return pd.DataFrame(rows, columns=OPENING_KILLS_COLUMNS)


def _player_matches(left: pd.Series, left_prefix: str, right: pd.Series, right_prefix: str) -> bool:
    left_id = left.get(f"{left_prefix}_steamid")
    right_id = right.get(f"{right_prefix}_steamid")
    if _has_value(left_id) and _has_value(right_id):
        return _same_id(left_id, right_id)
    left_name = left.get(f"{left_prefix}_name")
    right_name = right.get(f"{right_prefix}_name")
    return _has_value(left_name) and _has_value(right_name) and str(left_name) == str(right_name)


def trades(tables: dict[str, pd.DataFrame], cfg: ParserConfig | None = None) -> pd.DataFrame:
    """Mark strict revenge trades and return one row per traded kill.

    A current kill is a trade only when the current victim is the attacker who
    made an earlier kill, and the current attacker is a teammate of that earlier
    victim. Every earlier kill inside the time window is considered; an
    unrelated intervening kill therefore does not hide a valid trade.
    """
    cfg = cfg or ParserConfig()
    kills = tables.get("kills", pd.DataFrame())
    if not isinstance(kills, pd.DataFrame):
        return _empty(TRADES_COLUMNS)
    if "is_trade" not in kills.columns:
        kills["is_trade"] = False
    else:
        kills.loc[:, "is_trade"] = False
    if kills.empty or not {"round_num", "tick"}.issubset(kills.columns):
        return _empty(TRADES_COLUMNS)

    tickrate = cfg.tickrate or 64
    window = float(cfg.trade_seconds * tickrate)
    rows = []
    ordered = kills.sort_values(["round_num", "tick"], kind="stable")
    for _, round_kills in ordered.groupby("round_num", sort=False):
        valid_history: list[tuple[Any, pd.Series]] = []
        for idx, current in round_kills.iterrows():
            if not _is_player_kill(current) or not _has_value(current.get("tick")):
                continue
            current_tick = _as_float(current.get("tick"))
            current_attacker_team = _team_from_row(current, "attacker")
            current_victim_team = _team_from_row(current, "victim")
            if current_tick is None:
                continue

            valid_history = [
                item for item in valid_history
                if (lambda t: t is not None and 0 <= current_tick - t <= window)(_as_float(item[1].get("tick")))
            ]

            matched: pd.Series | None = None
            for _, previous in reversed(valid_history):
                previous_victim_team = _team_from_row(previous, "victim")
                previous_attacker_team = _team_from_row(previous, "attacker")
                teammate_revenge = (
                    _has_value(current_attacker_team)
                    and _has_value(previous_victim_team)
                    and str(current_attacker_team) == str(previous_victim_team)
                )
                killed_original_killer = _player_matches(current, "victim", previous, "attacker")
                enemy_kill = not (
                    _has_value(current_attacker_team)
                    and _has_value(current_victim_team)
                    and str(current_attacker_team) == str(current_victim_team)
                )
                team_alignment = (
                    not _has_value(previous_attacker_team)
                    or not _has_value(current_victim_team)
                    or str(previous_attacker_team) == str(current_victim_team)
                )
                if teammate_revenge and killed_original_killer and enemy_kill and team_alignment:
                    matched = previous
                    break

            if matched is not None:
                previous_tick = _as_float(matched.get("tick"))
                delta = current_tick - previous_tick if previous_tick is not None else None
                kills.loc[idx, "is_trade"] = True
                rows.append({
                    "round_num": _as_int(current.get("round_num")),
                    "tick": _as_int(current.get("tick")),
                    "trader_name": current.get("attacker_name"),
                    "trader_steamid": current.get("attacker_steamid"),
                    "trader_side": _side_from_row(current, "attacker"),
                    "trader_team_clan_name": current_attacker_team,
                    "traded_for_name": matched.get("victim_name"),
                    "traded_for_steamid": matched.get("victim_steamid"),
                    "victim_name": current.get("victim_name"),
                    "victim_steamid": current.get("victim_steamid"),
                    "victim_side": _side_from_row(current, "victim"),
                    "victim_team_clan_name": current_victim_team,
                    "weapon": current.get("weapon"),
                    "trade_ticks": int(delta) if delta is not None and float(delta).is_integer() else delta,
                })
            valid_history.append((idx, current))
    return pd.DataFrame(rows, columns=TRADES_COLUMNS)

def buy_outcomes(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Добавляет к закупкам сторону команды и исход раунда."""
    buys = tables.get("buys", pd.DataFrame())
    kills = tables.get("kills", pd.DataFrame())
    rounds = tables.get("rounds", pd.DataFrame())
    if buys.empty or not {"round_num", "team_clan_name"}.issubset(buys.columns):
        return _empty(BUY_OUTCOMES_COLUMNS)

    side_df = _team_side_per_round(kills, rounds)
    side_by_team_round = {
        (_as_int(r.round_num), str(r.team_clan_name)): r.side
        for r in side_df.itertuples()
    }
    winners = _round_winners(rounds)

    rows = []
    for _, row in buys.iterrows():
        n = _as_int(row.get("round_num"))
        team = row.get("team_clan_name")
        if n is None or not _has_value(team):
            continue
        side = side_by_team_round.get((n, str(team)))
        winner = winners.get(n)
        won = side == winner if side and winner else pd.NA
        rows.append({
            "round_num": n,
            "team_clan_name": team,
            "side": side,
            "equip": row.get("equip"),
            "buy_type": row.get("buy_type"),
            "won": won,
        })
    return pd.DataFrame(rows, columns=BUY_OUTCOMES_COLUMNS)


def _truthy(value: Any) -> bool:
    if not _has_value(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _snapshot_at(ticks: pd.DataFrame, round_num: int, target_tick: Any, *, prefer: str) -> pd.DataFrame:
    if ticks.empty or not {"round_num", "tick"}.issubset(ticks.columns):
        return pd.DataFrame()
    rt = ticks[ticks["round_num"] == round_num].copy()
    if rt.empty:
        return pd.DataFrame()
    rt["_tick_num"] = pd.to_numeric(rt["tick"], errors="coerce")
    target = _as_float(target_tick)
    if target is None:
        snap_tick = rt["_tick_num"].min() if prefer == "after" else rt["_tick_num"].max()
    elif prefer == "after":
        cand = rt[rt["_tick_num"] >= target]
        snap_tick = cand["_tick_num"].min() if not cand.empty else rt["_tick_num"].max()
    else:
        cand = rt[rt["_tick_num"] <= target]
        snap_tick = cand["_tick_num"].max() if not cand.empty else rt["_tick_num"].min()
    snap = rt[rt["_tick_num"] == snap_tick].drop(columns=["_tick_num"])
    return snap


def _tick_side(row: pd.Series) -> str | None:
    side = row.get("side")
    return _normalize_side(side if _has_value(side) else row.get("team_name"))


def _alive_mask(df: pd.DataFrame) -> pd.Series:
    if "is_alive" in df.columns:
        return df["is_alive"].apply(_truthy)
    if "health" in df.columns:
        return pd.to_numeric(df["health"], errors="coerce").fillna(0) > 0
    return pd.Series([False] * len(df), index=df.index)


def _alive_key(row: pd.Series) -> str | None:
    steamid = row.get("steamid")
    if _has_value(steamid):
        return f"steamid:{steamid}"
    name = row.get("name")
    if _has_value(name):
        return f"name:{name}"
    return None


def _info_from_tick_rows(rows: pd.DataFrame) -> dict[str, Any] | None:
    if rows.empty:
        return None
    seen: dict[str, pd.Series] = {}
    for _, row in rows.iterrows():
        key = _alive_key(row)
        if key:
            seen.setdefault(key, row)
    if len(seen) != 1:
        return None
    row = next(iter(seen.values()))
    return {
        "name": row.get("name"),
        "steamid": row.get("steamid"),
        "team_clan_name": row.get("team_clan_name"),
    }


def _round_started_5v5(ticks: pd.DataFrame, round_num: int, start_tick: Any) -> bool:
    snap = _snapshot_at(ticks, round_num, start_tick, prefer="after")
    if snap.empty:
        return True
    if not {"is_alive", "steamid"}.intersection(snap.columns):
        return True
    alive = snap[_alive_mask(snap)].copy()
    if alive.empty:
        return True
    alive["_side"] = alive.apply(_tick_side, axis=1)
    counts = {}
    for side in ("ct", "t"):
        side_rows = alive[alive["_side"] == side]
        keys = {k for k in side_rows.apply(_alive_key, axis=1) if k}
        counts[side] = len(keys)
    return counts == {"ct": 5, "t": 5}


def _round_participants(rk: pd.DataFrame) -> dict[str, dict[str, dict[str, Any]]]:
    players: dict[str, dict[str, dict[str, Any]]] = {"ct": {}, "t": {}}
    for _, row in rk.iterrows():
        for prefix in ("attacker", "victim"):
            side = _side_from_row(row, prefix)
            if side not in players:
                continue
            steamid = row.get(f"{prefix}_steamid")
            name = row.get(f"{prefix}_name")
            key = f"steamid:{steamid}" if _has_value(steamid) else (
                f"name:{name}" if _has_value(name) else None
            )
            if not key:
                continue
            players[side].setdefault(key, {
                "name": name,
                "steamid": steamid,
                "team_clan_name": _team_from_row(row, prefix),
            })
    return players


def _remove_victim(alive_players: dict[str, dict[str, dict[str, Any]]], row: pd.Series) -> None:
    side = _side_from_row(row, "victim")
    if side not in alive_players:
        return
    steamid = row.get("victim_steamid")
    name = row.get("victim_name")
    keys = []
    if _has_value(steamid):
        keys.append(f"steamid:{steamid}")
    if _has_value(name):
        keys.append(f"name:{name}")
    for key in keys:
        alive_players[side].pop(key, None)


def _clutch_player_info(
    ticks: pd.DataFrame,
    round_num: int,
    start_tick: Any,
    side: str,
    alive_players: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    snap = _snapshot_at(ticks, round_num, start_tick, prefer="before")
    if not snap.empty:
        alive = snap[_alive_mask(snap)].copy()
        if not alive.empty:
            alive["_side"] = alive.apply(_tick_side, axis=1)
            info = _info_from_tick_rows(alive[alive["_side"] == side])
            if info:
                return info
    side_players = alive_players.get(side, {})
    if len(side_players) == 1:
        return next(iter(side_players.values()))
    return None


def _survive_from_ticks(
    ticks: pd.DataFrame,
    round_num: int,
    end_tick: Any,
    side: str,
    player: dict[str, Any] | None,
) -> bool | pd._libs.missing.NAType:
    if player is None:
        return pd.NA
    snap = _snapshot_at(ticks, round_num, end_tick, prefer="before")
    if snap.empty:
        return pd.NA
    alive = snap.copy()
    alive["_side"] = alive.apply(_tick_side, axis=1)
    alive = alive[alive["_side"] == side]
    if _has_value(player.get("steamid")) and "steamid" in alive.columns:
        alive = alive[alive["steamid"].astype(str) == str(player["steamid"])]
    elif _has_value(player.get("name")) and "name" in alive.columns:
        alive = alive[alive["name"].astype(str) == str(player["name"])]
    else:
        return pd.NA
    if alive.empty:
        return pd.NA
    return bool(_alive_mask(alive).iloc[0])


def _team_for_round_side(side_df: pd.DataFrame, round_num: int, side: str) -> Any:
    if side_df.empty:
        return None
    rows = side_df[(side_df["round_num"] == round_num) & (side_df["side"] == side)]
    if rows.empty:
        return None
    return rows.iloc[0]["team_clan_name"]


def _clutch_rows(tables: dict[str, pd.DataFrame], *, won_only: bool) -> pd.DataFrame:
    """Return all FACEIT-style clutch attempts, including both players in 1v1.

    A lone player starts an attempt as soon as the state becomes 1vN.  In a
    1v1 both players receive an attempt: one win and one loss.  This matters
    because a match may therefore contain more clutch attempts than rounds.
    """
    kills = tables.get("kills", pd.DataFrame())
    rounds = tables.get("rounds", pd.DataFrame())
    ticks = tables.get("ticks", pd.DataFrame())
    if kills.empty or rounds.empty:
        return _empty(CLUTCH_COLUMNS)
    if not {"round_num", "tick", "victim_side"}.issubset(kills.columns):
        return _empty(CLUTCH_COLUMNS)
    if "round_num" not in rounds.columns:
        return _empty(CLUTCH_COLUMNS)

    winners = _round_winners(rounds)
    side_df = _team_side_per_round(kills, rounds)
    results: list[dict[str, Any]] = []

    for _, round_row in rounds.iterrows():
        n = _as_int(round_row.get("round_num"))
        if n is None:
            continue
        rk = kills[kills["round_num"] == round_row.get("round_num")].sort_values("tick", kind="stable")
        rk = rk[rk.apply(_is_player_kill, axis=1)]
        if rk.empty or not _round_started_5v5(ticks, n, round_row.get("start")):
            continue

        alive_counts = {"ct": 5, "t": 5}
        alive_players = _round_participants(rk)
        active: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        last_tick = None

        def start_attempt(side: str, tick: int | None) -> None:
            if side in seen or alive_counts.get(side) != 1:
                return
            opponent = "t" if side == "ct" else "ct"
            if alive_counts.get(opponent, 0) < 1:
                return
            player = _clutch_player_info(ticks, n, tick, side, alive_players)
            active[side] = {
                "side": side,
                "vs_count": int(alive_counts[opponent]),
                "start_tick": tick,
                "player": player,
                "joined_existing_1v1": bool(active and alive_counts[opponent] == 1),
            }
            seen.add(side)

        for _, kill in rk.iterrows():
            tick = _as_int(kill.get("tick"))
            if tick is not None:
                last_tick = tick
            victim_side = _side_from_row(kill, "victim")
            if victim_side not in alive_counts:
                continue
            alive_counts[victim_side] = max(alive_counts[victim_side] - 1, 0)
            _remove_victim(alive_players, kill)

            # The just-reduced side is the first candidate.  When the state is
            # 1v1, the opposing lone player receives an attempt as well.
            start_attempt(victim_side, tick)
            other = "t" if victim_side == "ct" else "ct"
            if alive_counts["ct"] == 1 and alive_counts["t"] == 1:
                start_attempt(other, tick)

            # Preserve player identity when the clutch player is the victim of
            # the current kill and sparse ticks cannot resolve the snapshot.
            if victim_side in active and active[victim_side]["player"] is None:
                active[victim_side]["player"] = {
                    "name": kill.get("victim_name"),
                    "steamid": kill.get("victim_steamid"),
                    "team_clan_name": _team_from_row(kill, "victim"),
                }

        winner = winners.get(n)
        reason = str(round_row.get("reason") or "").lower()
        end_tick = _as_int(round_row.get("end")) or last_tick
        for side, attempt in active.items():
            won = side == winner if winner else pd.NA
            # FACEIT does not award the second participant of a 1v1 when the
            # round ends by bomb detonation with both players still alive.  The
            # original lone player's lost attempt is still counted.
            if won is True and attempt.get("joined_existing_1v1") and reason == "bomb_exploded":
                continue
            if won_only and won is not True:
                continue
            player = attempt["player"]
            survive = (
                _survive_from_ticks(ticks, n, end_tick, side, player)
                if won is True else False
            )
            results.append({
                "round_num": n,
                "clutch_player": player.get("name") if player else None,
                "clutch_player_steamid": player.get("steamid") if player else None,
                "clutch_side": side,
                "clutch_team_clan_name": (
                    player.get("team_clan_name") if player and _has_value(player.get("team_clan_name"))
                    else _team_for_round_side(side_df, n, side)
                ),
                "vs_count": attempt["vs_count"],
                "won": won,
                "survive": survive,
                "start_tick": attempt["start_tick"],
                "end_tick": end_tick,
            })

    return pd.DataFrame(results, columns=CLUTCH_COLUMNS)


def clutch_attempts(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Определяет все 1v1–1v5 попытки клатча: выигранные и проигранные."""
    return _clutch_rows(tables, won_only=False)


def clutches(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Определяет только успешные клатчи 1v1–1v5 (`won=True`)."""
    return _clutch_rows(tables, won_only=True)
