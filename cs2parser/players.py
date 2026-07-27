"""Local player statistics derived only from the demo tables.

The module intentionally keeps both a conventional HLTV-style estimate and a
context-aware FACEIT-like estimate.  FACEIT's production model is proprietary,
so ``rating`` is an offline approximation; ``rating_hltv2`` remains available
for comparison and calibration.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
import math

import pandas as pd

from config import ParserConfig
from .derived import _is_player_kill
from .normalize import has_value, normalize_identifier, normalize_side
from .rating import predict_faceit
from .advanced import predict_advanced


EXPECTED_COLUMNS: list[str] = [
    "name", "steamid", "team_clan_name", "side", "n_rounds",
    "kills", "deaths", "assists", "flash_assists",
    "kills_per_round", "deaths_per_round", "assists_per_round",
    "headshots", "headshot_pct",
    "opening_kills", "opening_deaths", "entry_attempts", "entry_difference",
    "entry_attempt_pct", "entry_success_pct",
    "trade_kills", "deaths_traded", "traded_entry_kills", "traded_entry_deaths",
    "clutch_attempts", "clutches_won", "clutch_losses", "clutch_success_pct",
    "clutch_1v1", "clutch_1v2", "clutch_1v3", "clutch_1v4", "clutch_1v5",
    "multi_kill_2k", "multi_kill_3k", "multi_kill_4k", "multi_kill_5k",
    "multi_kill_rounds", "multi_kill_pct", "round_mvps",
    "shots", "hits", "accuracy", "single_shot_attempts", "single_shot_hits",
    "raw_single_shot_accuracy", "single_shot_accuracy",
    "rifle_shots", "sniper_shots", "pistol_shots", "smg_shots",
    "rounds_survived", "raw_rws", "rws",
    "dmg", "adr", "kast_rounds", "kast", "impact",
    "team_win_rate", "round_swing", "rating_hltv2", "rating",
    "rating_calibration_distance", "rating_model_version",
    "advanced_calibration_distance", "advanced_model_version",
]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=EXPECTED_COLUMNS)


def _player_key(steamid: Any, name: Any) -> str | None:
    sid = normalize_identifier(steamid)
    if sid:
        return f"steamid:{sid}"
    if has_value(name):
        return f"name:{name}"
    return None


def _enemy_event(row: pd.Series, attacker_prefix: str = "attacker", victim_prefix: str = "victim") -> bool:
    a_team = row.get(f"{attacker_prefix}_team_clan_name")
    v_team = row.get(f"{victim_prefix}_team_clan_name")
    if has_value(a_team) and has_value(v_team):
        return str(a_team) != str(v_team)
    a_side = normalize_side(row.get(f"{attacker_prefix}_side"))
    v_side = normalize_side(row.get(f"{victim_prefix}_side"))
    return bool(a_side and v_side and a_side != v_side)


def _bool(value: Any) -> bool:
    try:
        return False if pd.isna(value) else bool(value)
    except (TypeError, ValueError):
        return bool(value)


def _team_for_round(round_sides: pd.DataFrame, round_num: int, side: Any) -> str | None:
    if round_sides.empty or not {"round_num", "side", "team_clan_name"}.issubset(round_sides.columns):
        return None
    normalized = normalize_side(side)
    rows = round_sides[(round_sides["round_num"] == round_num) & (round_sides["side"].astype(str).str.lower() == normalized)]
    return str(rows.iloc[0]["team_clan_name"]) if not rows.empty else None


def _context_scores(
    kills: pd.DataFrame,
    rounds: pd.DataFrame,
    round_sides: pd.DataFrame,
    bomb: pd.DataFrame,
) -> dict[str, float]:
    """Estimate per-player round impact from changes in alive-state odds.

    It is deliberately deterministic and demo-only.  Early/even-state kills
    carry more weight than cleanup kills, while deaths debit the victim.
    """
    scores: dict[str, float] = defaultdict(float)
    if kills.empty or rounds.empty:
        return scores

    team_names = set()
    for column in ("attacker_team_clan_name", "victim_team_clan_name"):
        if column in kills.columns:
            team_names.update(str(v) for v in kills[column].dropna().unique())
    if len(team_names) < 2:
        return scores

    def logistic(value: float) -> float:
        return 1.0 / (1.0 + math.exp(-value))

    for _, round_row in rounds.sort_values("round_num").iterrows():
        rn = int(round_row.get("round_num", 0))
        sides: dict[str, str] = {}
        if not round_sides.empty:
            for _, side_row in round_sides[round_sides["round_num"] == rn].iterrows():
                side = normalize_side(side_row.get("side"))
                team = side_row.get("team_clan_name")
                if side and has_value(team):
                    sides[side] = str(team)
        round_teams = list(dict.fromkeys(sides.values())) or list(team_names)[:2]
        if len(round_teams) < 2:
            continue
        team_a, team_b = round_teams[:2]
        alive = {team_a: 5, team_b: 5}
        plant_ticks: list[int] = []
        if not bomb.empty and {"round_num", "tick", "event"}.issubset(bomb.columns):
            plants = bomb[(bomb["round_num"] == rn) & bomb["event"].astype(str).str.contains("plant", case=False, na=False)]
            plant_ticks = [int(v) for v in plants["tick"].dropna().tolist()]

        def probability_a(tick: int) -> float:
            side_a = next((side for side, team in sides.items() if team == team_a), None)
            bias = 0.10 if side_a == "ct" else -0.10
            if any(plant <= tick for plant in plant_ticks):
                bias += 0.55 if side_a == "t" else -0.55
            return logistic(0.85 * (alive[team_a] - alive[team_b]) + bias)

        events = kills[kills["round_num"] == rn].sort_values("tick") if "tick" in kills.columns else kills[kills["round_num"] == rn]
        for _, event in events.iterrows():
            victim_key = _player_key(event.get("victim_steamid"), event.get("victim_name"))
            victim_team = event.get("victim_team_clan_name")
            if not victim_key or victim_team not in alive:
                continue
            tick = int(event.get("tick", 0) or 0)
            before = probability_a(tick)
            alive[str(victim_team)] = max(0, alive[str(victim_team)] - 1)
            after = probability_a(tick)
            delta_a = after - before

            attacker_key = _player_key(event.get("attacker_steamid"), event.get("attacker_name"))
            attacker_team = event.get("attacker_team_clan_name")
            if attacker_key and attacker_team in alive and str(attacker_team) != str(victim_team):
                benefit = delta_a if str(attacker_team) == team_a else -delta_a
                scores[attacker_key] += benefit
                assister_key = _player_key(event.get("assister_steamid"), event.get("assister_name"))
                if assister_key and assister_key not in {attacker_key, victim_key}:
                    scores[assister_key] += benefit * 0.25

            victim_cost = delta_a if str(victim_team) == team_a else -delta_a
            scores[victim_key] += victim_cost * 0.65
    return scores


def _round_mvps(
    valid_kills: pd.DataFrame,
    damages: pd.DataFrame,
    rounds: pd.DataFrame,
    round_sides: pd.DataFrame,
    bomb: pd.DataFrame,
) -> Counter[str]:
    """Assign one deterministic local MVP to the winning team in each round."""
    result: Counter[str] = Counter()
    if rounds.empty:
        return result
    dmg_col = "dmg_health_real" if "dmg_health_real" in damages.columns else "dmg_health"

    for _, rr in rounds.iterrows():
        rn = int(rr.get("round_num", 0))
        winner_team = _team_for_round(round_sides, rn, rr.get("winner"))
        candidates: dict[str, float] = defaultdict(float)
        round_kills = valid_kills[valid_kills["round_num"] == rn] if not valid_kills.empty else valid_kills
        for _, event in round_kills.iterrows():
            if winner_team and str(event.get("attacker_team_clan_name")) != winner_team:
                continue
            key = _player_key(event.get("attacker_steamid"), event.get("attacker_name"))
            if key:
                candidates[key] += 100.0 + (18.0 if _bool(event.get("headshot")) else 0.0)
        if not damages.empty and dmg_col in damages.columns:
            round_damage = damages[damages["round_num"] == rn]
            for _, event in round_damage.iterrows():
                if winner_team and str(event.get("attacker_team_clan_name")) != winner_team:
                    continue
                if not _enemy_event(event):
                    continue
                key = _player_key(event.get("attacker_steamid"), event.get("attacker_name"))
                value = pd.to_numeric(pd.Series([event.get(dmg_col)]), errors="coerce").iloc[0]
                if key and pd.notna(value):
                    candidates[key] += max(0.0, float(value)) * 0.20
        if not bomb.empty and {"round_num", "event"}.issubset(bomb.columns):
            for _, event in bomb[bomb["round_num"] == rn].iterrows():
                name = str(event.get("event") or "").lower()
                key = _player_key(event.get("steamid"), event.get("name"))
                if key and any(token in name for token in ("planted", "defused", "exploded")):
                    candidates[key] += 55.0
        if candidates:
            result[max(candidates, key=lambda key: candidates[key])] += 1
    return result



_FIREARM_EXCLUDED = (
    "knife", "grenade", "flashbang", "smoke", "molotov", "incgrenade",
    "decoy", "taser", "c4", "world", "trigger_hurt", "inferno",
)
_SNIPERS = ("awp", "ssg08", "scar20", "g3sg1")
_PISTOLS = ("glock", "hkp2000", "usp", "p250", "deagle", "fiveseven", "tec9", "cz75", "elite", "revolver")
_SMGS = ("mp9", "mac10", "mp7", "mp5", "ump45", "p90", "bizon")


def _firearm_kind(weapon: Any) -> str | None:
    text = str(weapon or "").strip().lower().removeprefix("weapon_")
    if not text or any(token in text for token in _FIREARM_EXCLUDED):
        return None
    if any(token in text for token in _SNIPERS):
        return "sniper"
    if any(token in text for token in _PISTOLS):
        return "pistol"
    if any(token in text for token in _SMGS):
        return "smg"
    return "rifle"


def _shot_metrics(
    shots: pd.DataFrame,
    damages: pd.DataFrame,
    tickrate: int,
) -> dict[tuple[str, str], dict[str, float]]:
    """Return FACEIT-style firearm shot/hit and engagement accuracy counters."""
    result: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    hit_keys: set[tuple[str, int, int]] = set()

    if not damages.empty:
        for _, row in damages.iterrows():
            if not _enemy_event(row) or _firearm_kind(row.get("weapon")) is None:
                continue
            key = _player_key(row.get("attacker_steamid"), row.get("attacker_name"))
            rn = pd.to_numeric(pd.Series([row.get("round_num")]), errors="coerce").iloc[0]
            tick = pd.to_numeric(pd.Series([row.get("tick")]), errors="coerce").iloc[0]
            side = normalize_side(row.get("attacker_side"))
            if not key or pd.isna(rn) or pd.isna(tick):
                continue
            hit_keys.add((key, int(rn), int(tick)))
            for scope in ("all", side):
                if scope:
                    result[(key, scope)]["hits"] += 1

    if shots.empty:
        return result

    rows: list[dict[str, Any]] = []
    for _, row in shots.iterrows():
        kind = _firearm_kind(row.get("weapon"))
        key = _player_key(row.get("player_steamid"), row.get("player_name"))
        rn = pd.to_numeric(pd.Series([row.get("round_num")]), errors="coerce").iloc[0]
        tick = pd.to_numeric(pd.Series([row.get("tick")]), errors="coerce").iloc[0]
        side = normalize_side(row.get("player_side"))
        if kind is None or not key or pd.isna(rn) or pd.isna(tick):
            continue
        item = {
            "key": key, "round_num": int(rn), "tick": int(tick),
            "weapon": str(row.get("weapon") or ""), "side": side, "kind": kind,
        }
        rows.append(item)
        for scope in ("all", side):
            if scope:
                result[(key, scope)]["shots"] += 1
                result[(key, scope)][f"{kind}_shots"] += 1

    # FACEIT's S. Accuracy behaves like an engagement/first-burst metric.  The
    # raw baseline counts a new engagement after 0.375 seconds without a shot;
    # the final value is residual-calibrated from supplied scoreboards.
    burst_gap = max(int(round(tickrate * 0.375)), 1)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return result
    for key, group in frame.sort_values(["key", "round_num", "tick"], kind="stable").groupby("key", sort=False):
        burst: list[dict[str, Any]] = []

        def commit(items: list[dict[str, Any]]) -> None:
            if not items:
                return
            first = items[0]
            hit = any((key, item["round_num"], item["tick"]) in hit_keys for item in items)
            for scope in ("all", first["side"]):
                if scope:
                    result[(key, scope)]["single_shot_attempts"] += 1
                    if hit:
                        result[(key, scope)]["single_shot_hits"] += 1

        previous: dict[str, Any] | None = None
        for item in group.to_dict("records"):
            new_burst = (
                previous is None
                or item["round_num"] != previous["round_num"]
                or item["weapon"] != previous["weapon"]
                or item["tick"] - previous["tick"] > burst_gap
            )
            if new_burst:
                commit(burst)
                burst = [item]
            else:
                burst.append(item)
            previous = item
        commit(burst)
    return result


def _rws_scores(
    damages: pd.DataFrame,
    rounds: pd.DataFrame,
    round_sides: pd.DataFrame,
    bomb: pd.DataFrame,
) -> dict[tuple[str, str], float]:
    """Calculate the documented RWS baseline (100 points per won round).

    Normal won rounds distribute 100 by real enemy damage.  Defuse/detonation
    rounds reserve 30 for the objective player and distribute the remaining 70
    by damage.  The caller divides accumulated points by rounds played.
    """
    out: dict[tuple[str, str], float] = defaultdict(float)
    if rounds.empty or damages.empty:
        return out
    dmg_col = "dmg_health_real" if "dmg_health_real" in damages.columns else "dmg_health"
    if dmg_col not in damages.columns:
        return out

    for _, round_row in rounds.iterrows():
        rn_value = pd.to_numeric(pd.Series([round_row.get("round_num")]), errors="coerce").iloc[0]
        if pd.isna(rn_value):
            continue
        rn = int(rn_value)
        winner_side = normalize_side(round_row.get("winner"))
        winner_team = _team_for_round(round_sides, rn, winner_side)
        if not winner_team:
            continue
        damage_by_player: dict[str, float] = defaultdict(float)
        side_by_player: dict[str, str] = {}
        round_damage = damages[damages["round_num"] == round_row.get("round_num")]
        for _, event in round_damage.iterrows():
            if str(event.get("attacker_team_clan_name")) != str(winner_team) or not _enemy_event(event):
                continue
            key = _player_key(event.get("attacker_steamid"), event.get("attacker_name"))
            value = pd.to_numeric(pd.Series([event.get(dmg_col)]), errors="coerce").iloc[0]
            if key and pd.notna(value):
                damage_by_player[key] += max(float(value), 0.0)
                side = normalize_side(event.get("attacker_side"))
                if side:
                    side_by_player[key] = side

        objective_key = None
        objective_side = winner_side
        if not bomb.empty and {"round_num", "event"}.issubset(bomb.columns):
            objective_rows = bomb[
                (bomb["round_num"] == round_row.get("round_num"))
                & bomb["event"].astype(str).str.lower().isin(["defuse", "detonate"])
            ]
            if not objective_rows.empty:
                objective = objective_rows.sort_values("tick", kind="stable").iloc[-1]
                objective_key = _player_key(objective.get("steamid"), objective.get("name"))

        pool = 70.0 if objective_key else 100.0
        total_damage = sum(damage_by_player.values())
        if total_damage > 0:
            for key, value in damage_by_player.items():
                points = pool * value / total_damage
                out[(key, "all")] += points
                side = side_by_player.get(key) or winner_side
                if side:
                    out[(key, side)] += points
        if objective_key:
            out[(objective_key, "all")] += 30.0
            if objective_side:
                out[(objective_key, objective_side)] += 30.0
    return out


def _opening_trade_metrics(
    trades: pd.DataFrame,
    openings: pd.DataFrame,
) -> dict[tuple[str, str], dict[str, float]]:
    result: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if trades.empty:
        return result
    opening_victim: dict[int, str] = {}
    if not openings.empty:
        for _, row in openings.iterrows():
            rn = pd.to_numeric(pd.Series([row.get("round_num")]), errors="coerce").iloc[0]
            key = _player_key(row.get("opened_on_steamid"), row.get("opened_on"))
            if pd.notna(rn) and key:
                opening_victim[int(rn)] = key
    for _, row in trades.iterrows():
        rn_value = pd.to_numeric(pd.Series([row.get("round_num")]), errors="coerce").iloc[0]
        trader = _player_key(row.get("trader_steamid"), row.get("trader_name"))
        trader_side = normalize_side(row.get("trader_side"))
        traded = _player_key(row.get("traded_for_steamid"), row.get("traded_for_name"))
        if traded:
            for scope in ("all", trader_side):
                # The traded-for player was on the trader's team and therefore
                # has the same side at the moment of the trade.
                if scope:
                    result[(traded, scope)]["deaths_traded"] += 1
        if pd.isna(rn_value):
            continue
        opened_on = opening_victim.get(int(rn_value))
        if opened_on and traded == opened_on:
            for scope in ("all", trader_side):
                if scope and trader:
                    result[(trader, scope)]["traded_entry_kills"] += 1
                if scope:
                    result[(traded, scope)]["traded_entry_deaths"] += 1
    return result

def _build_local_stats(tables: dict[str, pd.DataFrame], cfg: ParserConfig) -> pd.DataFrame:
    ticks = tables.get("ticks", pd.DataFrame())
    kills = tables.get("kills", pd.DataFrame())
    damages = tables.get("damages", pd.DataFrame())
    trades = tables.get("trades", pd.DataFrame())
    openings = tables.get("opening_kills", pd.DataFrame())
    attempts = tables.get("clutch_attempts", pd.DataFrame())
    clutches = tables.get("clutches", pd.DataFrame())
    rounds = tables.get("rounds", pd.DataFrame())
    round_sides = tables.get("round_sides", pd.DataFrame())
    bomb = tables.get("bomb", pd.DataFrame())
    shots = tables.get("shots", pd.DataFrame())
    final_totals = tables.get("final_player_totals", pd.DataFrame())

    if ticks.empty or not {"round_num", "steamid", "name"}.issubset(ticks.columns):
        return _empty()

    # Parser tick rows are already chronological.  Sorting the full spatial
    # table (often >1.5 million rows) solely to identify participants is very
    # expensive, so project to the six identity columns and keep the last row
    # per round/player directly.
    participant_columns = [
        column for column in (
            "round_num", "tick", "steamid", "name", "side",
            "team_name", "team_clan_name",
        ) if column in ticks.columns
    ]
    participant_rows = ticks[participant_columns].drop_duplicates(
        ["round_num", "steamid"], keep="last"
    )
    players: dict[str, dict[str, Any]] = {}
    round_presence: dict[tuple[str, str], set[int]] = defaultdict(set)
    team_votes: dict[tuple[str, str], list[str]] = defaultdict(list)
    name_votes: dict[str, list[str]] = defaultdict(list)

    for _, row in participant_rows.iterrows():
        key = _player_key(row.get("steamid"), row.get("name"))
        if not key:
            continue
        sid = normalize_identifier(row.get("steamid"))
        raw_side = row.get("side")
        side = normalize_side(raw_side if has_value(raw_side) else row.get("team_name"))
        rn = int(row["round_num"])
        players.setdefault(key, {"steamid": sid, "name": row.get("name")})
        if has_value(row.get("name")):
            name_votes[key].append(str(row.get("name")))
        round_presence[(key, "all")].add(rn)
        if side:
            round_presence[(key, side)].add(rn)
        if has_value(row.get("team_clan_name")):
            team = str(row.get("team_clan_name"))
            team_votes[(key, "all")].append(team)
            team_votes[(key, side or "all")].append(team)

    shot_metrics = _shot_metrics(shots, damages, int(cfg.tickrate or 64))
    rws_points = _rws_scores(damages, rounds, round_sides, bomb)
    trade_detail = _opening_trade_metrics(trades, openings)

    metrics: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    kast_flags: dict[tuple[str, str], set[int]] = defaultdict(set)
    death_rounds: dict[tuple[str, str], set[int]] = defaultdict(set)
    kills_per_round: Counter[tuple[str, str, int]] = Counter()

    valid_kills = kills[kills.apply(_is_player_kill, axis=1)].copy() if not kills.empty else kills
    for _, row in valid_kills.iterrows():
        rn = int(row.get("round_num", 0))
        a_key = _player_key(row.get("attacker_steamid"), row.get("attacker_name"))
        a_side = normalize_side(row.get("attacker_side"))
        if a_key:
            for scope in ("all", a_side):
                if scope:
                    metrics[(a_key, scope)]["kills"] += 1
                    kills_per_round[(a_key, scope, rn)] += 1
                    kast_flags[(a_key, scope)].add(rn)
                    if _bool(row.get("headshot")):
                        metrics[(a_key, scope)]["headshots"] += 1

        assister_key = _player_key(row.get("assister_steamid"), row.get("assister_name"))
        assister_side = normalize_side(row.get("assister_side"))
        if assister_key and assister_key != a_key:
            metric_name = "flash_assists" if _bool(row.get("assistedflash")) else "assists"
            for scope in ("all", assister_side):
                if scope:
                    metrics[(assister_key, scope)][metric_name] += 1
                    kast_flags[(assister_key, scope)].add(rn)

    # FACEIT's D column includes every player death, including bomb/world/team deaths.
    if not kills.empty:
        for _, row in kills.iterrows():
            v_key = _player_key(row.get("victim_steamid"), row.get("victim_name"))
            if not v_key:
                continue
            rn = int(row.get("round_num", 0))
            v_side = normalize_side(row.get("victim_side"))
            for scope in ("all", v_side):
                if scope:
                    metrics[(v_key, scope)]["deaths"] += 1
                    death_rounds[(v_key, scope)].add(rn)

    if not damages.empty:
        dmg_col = "dmg_health_real" if "dmg_health_real" in damages.columns else "dmg_health"
        if dmg_col in damages.columns:
            for _, row in damages.iterrows():
                if not _enemy_event(row):
                    continue
                key = _player_key(row.get("attacker_steamid"), row.get("attacker_name"))
                side = normalize_side(row.get("attacker_side"))
                value = pd.to_numeric(pd.Series([row.get(dmg_col)]), errors="coerce").iloc[0]
                if not key or pd.isna(value):
                    continue
                for scope in ("all", side):
                    if scope:
                        metrics[(key, scope)]["dmg"] += max(0.0, float(value))

    for _, row in trades.iterrows():
        rn = int(row.get("round_num", 0))
        trader = _player_key(row.get("trader_steamid"), row.get("trader_name"))
        trader_side = normalize_side(row.get("trader_side"))
        if trader:
            for scope in ("all", trader_side):
                if scope:
                    metrics[(trader, scope)]["trade_kills"] += 1
        traded = _player_key(row.get("traded_for_steamid"), row.get("traded_for_name"))
        if traded:
            kast_flags[(traded, "all")].add(rn)
            for scope in ("ct", "t"):
                if rn in round_presence.get((traded, scope), set()):
                    kast_flags[(traded, scope)].add(rn)

    for _, row in openings.iterrows():
        for key, side, metric in (
            (_player_key(row.get("opener_steamid"), row.get("opener")), normalize_side(row.get("opener_side")), "opening_kills"),
            (_player_key(row.get("opened_on_steamid"), row.get("opened_on")), normalize_side(row.get("victim_side")), "opening_deaths"),
        ):
            if key:
                for scope in ("all", side):
                    if scope:
                        metrics[(key, scope)][metric] += 1

    for _, row in attempts.iterrows():
        key = _player_key(row.get("clutch_player_steamid"), row.get("clutch_player"))
        side = normalize_side(row.get("clutch_side"))
        if key:
            for scope in ("all", side):
                if scope:
                    metrics[(key, scope)]["clutch_attempts"] += 1
    for _, row in clutches.iterrows():
        key = _player_key(row.get("clutch_player_steamid"), row.get("clutch_player"))
        side = normalize_side(row.get("clutch_side"))
        vs_value = pd.to_numeric(pd.Series([row.get("vs_count")]), errors="coerce").iloc[0]
        if key:
            for scope in ("all", side):
                if scope:
                    metrics[(key, scope)]["clutches_won"] += 1
                    if pd.notna(vs_value) and 1 <= int(vs_value) <= 5:
                        metrics[(key, scope)][f"clutch_1v{int(vs_value)}"] += 1

    # Survival is defined by absence of a death in a round; this is more robust
    # than trusting the final sampled tick, which can land after a reset.
    for (key, side), present in round_presence.items():
        kast_flags[(key, side)].update(present - death_rounds.get((key, side), set()))

    context = _context_scores(kills, rounds, round_sides, bomb)
    mvps = _round_mvps(valid_kills, damages, rounds, round_sides, bomb)

    team_wins: Counter[str] = Counter()
    if not rounds.empty:
        for _, row in rounds.iterrows():
            team = _team_for_round(round_sides, int(row.get("round_num", 0)), row.get("winner"))
            if team:
                team_wins[team] += 1

    # Exact cumulative CS2 scoreboard MVPs.  They are read from unfiltered
    # post-match ticks because the final-round award happens just after the
    # last in-play tick.  The deterministic local heuristic remains a fallback
    # for older parsed data that lacks this property.
    exact_mvps: dict[str, int] = {}
    mvp_col = "CCSPlayerController.m_iMVPs"
    if not final_totals.empty and mvp_col in final_totals.columns:
        for _, row in final_totals.iterrows():
            key = _player_key(row.get("steamid"), row.get("name"))
            value = pd.to_numeric(pd.Series([row.get(mvp_col)]), errors="coerce").iloc[0]
            if key and pd.notna(value):
                exact_mvps[key] = int(value)

    rows: list[dict[str, Any]] = []
    for key, ident in players.items():
        for side in ("all", "ct", "t"):
            n_rounds = len(round_presence.get((key, side), set()))
            if n_rounds == 0:
                continue
            m = metrics[(key, side)]
            kills_n, deaths_n, assists_n = int(m.get("kills", 0)), int(m.get("deaths", 0)), int(m.get("assists", 0))
            flash_assists = int(m.get("flash_assists", 0))
            headshots = int(m.get("headshots", 0))
            dmg = float(m.get("dmg", 0.0))
            adr = dmg / n_rounds
            kast_rounds = len(kast_flags.get((key, side), set()) & round_presence[(key, side)])
            kast_pct = 100.0 * kast_rounds / n_rounds
            impact = 2.13 * (kills_n / n_rounds) + 0.42 * (assists_n / n_rounds) - 0.41
            hltv = (
                0.0073 * kast_pct + 0.3591 * (kills_n / n_rounds)
                - 0.5329 * (deaths_n / n_rounds) + 0.2372 * impact
                + 0.0032 * adr + 0.1587
            )
            counts = Counter(kills_per_round[(key, side, rn)] for rn in round_presence[(key, side)])
            multi2, multi3, multi4 = counts[2], counts[3], counts[4]
            multi5 = sum(count for size, count in counts.items() if size >= 5)
            votes = team_votes.get((key, side), []) or team_votes.get((key, "all"), [])
            team = pd.Series(votes).mode().iloc[0] if votes else None
            names = name_votes.get(key, [])
            name = pd.Series(names).mode().iloc[0] if names else ident.get("name")

            opening_kills_n = int(m.get("opening_kills", 0))
            opening_deaths_n = int(m.get("opening_deaths", 0))
            entry_attempts = opening_kills_n + opening_deaths_n
            trade_extra = trade_detail[(key, side)]
            clutch_attempts_n = int(m.get("clutch_attempts", 0))
            clutches_won_n = int(m.get("clutches_won", 0))
            shot = shot_metrics[(key, side)]
            shots_n = int(shot.get("shots", 0))
            hits_n = int(shot.get("hits", 0))
            single_attempts = int(shot.get("single_shot_attempts", 0))
            single_hits = int(shot.get("single_shot_hits", 0))
            raw_single = 100.0 * single_hits / single_attempts if single_attempts else 0.0
            raw_rws = float(rws_points.get((key, side), 0.0)) / n_rounds
            multi_kill_rounds = multi2 + multi3 + multi4 + multi5

            # Calibrated FACEIT-like model v2.  Every input is derived from
            # the demo and normalized per round.  The calibration file contains
            # aggregate reference vectors only; player names and match ids are
            # audit metadata and are never prediction inputs.
            team_win_rate = team_wins.get(str(team), 0) / max(len(rounds), 1)
            round_mvps_value = int(exact_mvps.get(key, mvps.get(key, 0))) if side == "all" else 0
            rating_payload = {
                "n_rounds": n_rounds,
                "kills": kills_n,
                "deaths": deaths_n,
                "assists": assists_n,
                "flash_assists": flash_assists,
                "kills_per_round": kills_n / n_rounds,
                "deaths_per_round": deaths_n / n_rounds,
                "assists_per_round": assists_n / n_rounds,
                "headshots": headshots,
                "headshot_pct": 100.0 * headshots / kills_n if kills_n else 0.0,
                "opening_kills": opening_kills_n,
                "opening_deaths": opening_deaths_n,
                "trade_kills": int(m.get("trade_kills", 0)),
                "clutch_attempts": clutch_attempts_n,
                "clutches_won": clutches_won_n,
                "multi_kill_2k": multi2,
                "multi_kill_3k": multi3,
                "multi_kill_4k": multi4,
                "multi_kill_5k": multi5,
                "round_mvps": round_mvps_value,
                "adr": adr,
                "kast": kast_pct,
                "impact": impact,
                "team_win_rate": team_win_rate,
            }
            rating, round_swing, calibration_distance, rating_model_version = predict_faceit(rating_payload)
            advanced_payload = {
                **rating_payload,
                "raw_rws": raw_rws,
                "raw_single_shot_accuracy": raw_single,
                "shots": shots_n,
                "hits": hits_n,
                "single_shot_attempts": single_attempts,
                "single_shot_hits": single_hits,
                "rifle_shots": int(shot.get("rifle_shots", 0)),
                "sniper_shots": int(shot.get("sniper_shots", 0)),
                "pistol_shots": int(shot.get("pistol_shots", 0)),
                "smg_shots": int(shot.get("smg_shots", 0)),
            }
            rws, single_accuracy, advanced_distance, advanced_model_version = predict_advanced(advanced_payload)

            rows.append({
                "name": name, "steamid": ident.get("steamid"), "team_clan_name": team,
                "side": side, "n_rounds": n_rounds,
                "kills": kills_n, "deaths": deaths_n, "assists": assists_n, "flash_assists": flash_assists,
                "kills_per_round": kills_n / n_rounds, "deaths_per_round": deaths_n / n_rounds,
                "assists_per_round": assists_n / n_rounds,
                "headshots": headshots, "headshot_pct": 100.0 * headshots / kills_n if kills_n else 0.0,
                "opening_kills": opening_kills_n, "opening_deaths": opening_deaths_n,
                "entry_attempts": entry_attempts, "entry_difference": opening_kills_n - opening_deaths_n,
                "entry_attempt_pct": 100.0 * entry_attempts / n_rounds,
                "entry_success_pct": 100.0 * opening_kills_n / entry_attempts if entry_attempts else 0.0,
                "trade_kills": int(m.get("trade_kills", 0)),
                "deaths_traded": int(trade_extra.get("deaths_traded", 0)),
                "traded_entry_kills": int(trade_extra.get("traded_entry_kills", 0)),
                "traded_entry_deaths": int(trade_extra.get("traded_entry_deaths", 0)),
                "clutch_attempts": clutch_attempts_n, "clutches_won": clutches_won_n,
                "clutch_losses": max(clutch_attempts_n - clutches_won_n, 0),
                "clutch_success_pct": 100.0 * clutches_won_n / clutch_attempts_n if clutch_attempts_n else 0.0,
                "clutch_1v1": int(m.get("clutch_1v1", 0)), "clutch_1v2": int(m.get("clutch_1v2", 0)),
                "clutch_1v3": int(m.get("clutch_1v3", 0)), "clutch_1v4": int(m.get("clutch_1v4", 0)),
                "clutch_1v5": int(m.get("clutch_1v5", 0)),
                "multi_kill_2k": multi2, "multi_kill_3k": multi3, "multi_kill_4k": multi4, "multi_kill_5k": multi5,
                "multi_kill_rounds": multi_kill_rounds, "multi_kill_pct": 100.0 * multi_kill_rounds / n_rounds,
                "round_mvps": round_mvps_value,
                "shots": shots_n, "hits": hits_n, "accuracy": 100.0 * hits_n / shots_n if shots_n else 0.0,
                "single_shot_attempts": single_attempts, "single_shot_hits": single_hits,
                "raw_single_shot_accuracy": raw_single, "single_shot_accuracy": single_accuracy,
                "rifle_shots": int(shot.get("rifle_shots", 0)), "sniper_shots": int(shot.get("sniper_shots", 0)),
                "pistol_shots": int(shot.get("pistol_shots", 0)), "smg_shots": int(shot.get("smg_shots", 0)),
                "rounds_survived": max(n_rounds - deaths_n, 0), "raw_rws": raw_rws, "rws": rws,
                "dmg": dmg, "adr": adr, "kast_rounds": kast_rounds, "kast": kast_pct,
                "impact": impact, "team_win_rate": team_win_rate,
                "round_swing": round_swing, "rating_hltv2": hltv, "rating": rating,
                "rating_calibration_distance": calibration_distance,
                "rating_model_version": rating_model_version,
                "advanced_calibration_distance": advanced_distance,
                "advanced_model_version": advanced_model_version,
            })

    return pd.DataFrame(rows, columns=EXPECTED_COLUMNS)


def player_stats(dem, cfg: ParserConfig | None = None, tables: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Calculate stats locally from parsed tables, without API enrichment."""
    cfg = cfg or ParserConfig()
    if tables is not None:
        return _build_local_stats(tables, cfg)
    try:
        from .demo import _to_pandas
        local = {
            "ticks": _to_pandas(dem.ticks), "kills": _to_pandas(dem.kills),
            "damages": _to_pandas(dem.damages), "trades": pd.DataFrame(),
            "opening_kills": pd.DataFrame(), "clutch_attempts": pd.DataFrame(),
            "clutches": pd.DataFrame(), "rounds": _to_pandas(getattr(dem, "rounds", pd.DataFrame())),
            "round_sides": pd.DataFrame(), "bomb": _to_pandas(getattr(dem, "bomb", pd.DataFrame())),
        }
        return _build_local_stats(local, cfg)
    except Exception:
        return _empty()
