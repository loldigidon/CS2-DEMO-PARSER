"""Transparent demo-only approximation of FACEIT Round Swing.

FACEIT documents Round Swing as the change in a team's win probability after
meaningful actions.  The production model and its trained coefficients are not
public, so this module implements the same *shape* of calculation locally:

* estimate the pre-round win probability from side and equipment;
* update win probability after damage, kills, and bomb plants;
* attribute probability changes to the involved players;
* normalise each team's player contributions to the actual round result.

The output is intended for per-round radar explanations.  It is deterministic,
uses only demo tables, and deliberately exposes its assumptions instead of
pretending to be FACEIT's private model.
"""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Mapping

import pandas as pd

from .normalize import has_value, normalize_identifier, normalize_side

# Logit bias for the CT side at the beginning of an even-economy round.
# Values are intentionally small; equipment and in-round state dominate.
_MAP_CT_BIAS = {
    "de_ancient": 0.08,
    "de_cache": -0.02,
    "de_dust2": -0.09,
    "de_inferno": 0.05,
    "de_mirage": 0.00,
    "de_nuke": 0.14,
    "de_overpass": 0.08,
    "de_train": 0.12,
    "de_vertigo": 0.02,
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _player_key(steamid: Any, name: Any) -> str:
    sid = normalize_identifier(steamid)
    safe_name = str(name) if has_value(name) else "unknown"
    return f"sid:{sid}" if sid else f"name:{safe_name}"


def _truthy(value: Any) -> bool:
    try:
        return False if pd.isna(value) else bool(value)
    except (TypeError, ValueError):
        return bool(value)


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _enemy(row: Mapping[str, Any]) -> bool:
    a_team = row.get("attacker_team_clan_name")
    v_team = row.get("victim_team_clan_name")
    if has_value(a_team) and has_value(v_team):
        return str(a_team) != str(v_team)
    a_side = normalize_side(row.get("attacker_side"))
    v_side = normalize_side(row.get("victim_side"))
    return bool(a_side and v_side and a_side != v_side)


def _team_equip(buys: pd.DataFrame, round_num: int) -> dict[str, float]:
    if buys.empty or not {"round_num", "team_clan_name", "equip"}.issubset(buys.columns):
        return {}
    rows = buys[buys["round_num"] == round_num]
    return {
        str(row.get("team_clan_name")): max(_number(row.get("equip")), 0.0)
        for _, row in rows.iterrows()
        if has_value(row.get("team_clan_name"))
    }


def estimate_round_players(
    tables: Mapping[str, pd.DataFrame],
    players: list[dict[str, Any]],
    player_index: Mapping[str, int],
    map_name: str,
) -> dict[str, dict[str, Any]]:
    """Return per-round player K/D/A, damage, and estimated swing percentages."""
    rounds = tables.get("rounds", pd.DataFrame())
    round_sides = tables.get("round_sides", pd.DataFrame())
    kills = tables.get("kills", pd.DataFrame())
    damages = tables.get("damages", pd.DataFrame())
    bomb = tables.get("bomb", pd.DataFrame())
    buys = tables.get("buys", pd.DataFrame())
    if rounds.empty:
        return {}

    roster_by_team: dict[str, list[int]] = defaultdict(list)
    for index, player in enumerate(players):
        roster_by_team[str(player.get("team") or "")].append(index)

    output: dict[str, dict[str, Any]] = {}
    damage_column = "dmg_health_real" if "dmg_health_real" in damages.columns else (
        "dmg_health" if "dmg_health" in damages.columns else None
    )

    for _, round_row in rounds.sort_values("round_num").iterrows():
        round_num = _int(round_row.get("round_num"))
        side_to_team: dict[str, str] = {}
        if not round_sides.empty:
            for _, side_row in round_sides[round_sides["round_num"] == round_num].iterrows():
                side = normalize_side(side_row.get("side"))
                team = side_row.get("team_clan_name")
                if side and has_value(team):
                    side_to_team[side] = str(team)
        teams = [team for team in (side_to_team.get("ct"), side_to_team.get("t")) if team]
        if len(teams) != 2:
            teams = [team for team, roster in roster_by_team.items() if team and roster][:2]
        if len(teams) != 2:
            continue
        ct_team, t_team = side_to_team.get("ct", teams[0]), side_to_team.get("t", teams[1])
        teams = [ct_team, t_team]

        roster = {team: list(roster_by_team.get(team, [])) for team in teams}
        index_team = {index: team for team, members in roster.items() for index in members}
        health = {index: 100.0 for members in roster.values() for index in members}
        alive = {index: True for index in health}
        equip = _team_equip(buys, round_num)
        planted = False

        def probability(team: str) -> float:
            other = t_team if team == ct_team else ct_team
            alive_diff = sum(alive.get(i, False) for i in roster[team]) - sum(alive.get(i, False) for i in roster[other])
            health_diff = sum(health.get(i, 0.0) for i in roster[team]) - sum(health.get(i, 0.0) for i in roster[other])
            equip_ratio = math.log((equip.get(team, 5000.0) + 1000.0) / (equip.get(other, 5000.0) + 1000.0))
            ct_bias = _MAP_CT_BIAS.get(map_name, 0.04)
            side_bias = ct_bias if team == ct_team else -ct_bias
            bomb_bias = 0.0
            if planted:
                bomb_bias = -0.82 if team == ct_team else 0.82
            logit = 0.90 * alive_diff + 0.005 * health_diff + 0.35 * equip_ratio + side_bias + bomb_bias
            return _sigmoid(logit)

        start_probability = {team: probability(team) for team in teams}
        contribution = {index: 0.0 for index in health}
        metrics = {
            index: {"kills": 0, "deaths": 0, "assists": 0, "damage": 0.0}
            for index in health
        }

        events: list[tuple[int, int, str, pd.Series]] = []
        if not damages.empty and "round_num" in damages.columns and damage_column:
            for _, row in damages[damages["round_num"] == round_num].iterrows():
                events.append((_int(row.get("tick")), 0, "damage", row))
        if not kills.empty and "round_num" in kills.columns:
            for _, row in kills[kills["round_num"] == round_num].iterrows():
                events.append((_int(row.get("tick")), 1, "kill", row))
        if not bomb.empty and "round_num" in bomb.columns:
            for _, row in bomb[bomb["round_num"] == round_num].iterrows():
                event_name = str(row.get("event") or "").lower()
                if "plant" in event_name:
                    events.append((_int(row.get("tick")), 2, "plant", row))

        for _, _, event_type, row in sorted(events, key=lambda item: (item[0], item[1])):
            if event_type == "plant":
                actor = player_index.get(_player_key(row.get("steamid"), row.get("name")))
                if actor is None or actor not in index_team:
                    continue
                team = index_team[actor]
                before = probability(team)
                planted = True
                delta = max(0.0, probability(team) - before) * 100.0
                contribution[actor] += delta
                opponents = [i for i in roster[t_team if team == ct_team else ct_team] if alive.get(i)]
                for opponent in opponents:
                    contribution[opponent] -= delta / max(len(opponents), 1)
                continue

            attacker = player_index.get(_player_key(row.get("attacker_steamid"), row.get("attacker_name")))
            victim = player_index.get(_player_key(row.get("victim_steamid"), row.get("victim_name")))
            if attacker is None or victim is None or attacker not in index_team or victim not in index_team:
                continue
            if index_team[attacker] == index_team[victim] or not _enemy(row):
                continue
            attacker_team = index_team[attacker]

            if event_type == "damage":
                if not alive.get(victim, False):
                    continue
                amount = min(max(_number(row.get(damage_column)), 0.0), health[victim])
                if amount <= 0:
                    continue
                before = probability(attacker_team)
                health[victim] = max(0.0, health[victim] - amount)
                delta = max(0.0, probability(attacker_team) - before) * 100.0
                contribution[attacker] += delta
                contribution[victim] -= delta
                metrics[attacker]["damage"] += amount
                continue

            if not alive.get(victim, False):
                continue
            before = probability(attacker_team)
            alive[victim] = False
            health[victim] = 0.0
            delta = max(0.0, probability(attacker_team) - before) * 100.0

            assister = player_index.get(_player_key(row.get("assister_steamid"), row.get("assister_name")))
            assist_share = 0.0
            if assister is not None and assister not in {attacker, victim} and index_team.get(assister) == attacker_team:
                assist_share = 0.30 if _truthy(row.get("assistedflash")) else 0.18
                contribution[assister] += delta * assist_share
                metrics[assister]["assists"] += 1
            contribution[attacker] += delta * (1.0 - assist_share)
            contribution[victim] -= delta
            metrics[attacker]["kills"] += 1
            metrics[victim]["deaths"] += 1

        winner_side = normalize_side(round_row.get("winner"))
        winner_team = side_to_team.get(winner_side or "")
        team_targets: dict[str, float] = {}
        for team in teams:
            team_targets[team] = (
                100.0 * (1.0 - start_probability[team])
                if team == winner_team
                else -100.0 * start_probability[team]
            )
            members = roster[team]
            if not members:
                continue
            raw_total = sum(contribution[i] for i in members)
            correction = (team_targets[team] - raw_total) / len(members)
            for index in members:
                contribution[index] += correction
            # The private model spreads credit over damage share, utility, and
            # trades.  Blend extreme direct-event values toward the team mean
            # to emulate that shared attribution without hidden coefficients.
            mean = team_targets[team] / len(members)
            for index in members:
                contribution[index] = 0.65 * contribution[index] + 0.35 * mean

        player_rows = []
        for team in teams:
            for index in roster[team]:
                row = metrics[index]
                player_rows.append({
                    "player": index,
                    "team": team,
                    "side": "ct" if team == ct_team else "t",
                    "swing": round(contribution[index], 2),
                    "damage": int(round(row["damage"])),
                    "kills": int(row["kills"]),
                    "deaths": int(row["deaths"]),
                    "assists": int(row["assists"]),
                })

        output[str(round_num)] = {
            "players": player_rows,
            "start_probability": {team: round(start_probability[team] * 100.0, 2) for team in teams},
            "team_swing": {team: round(team_targets[team], 2) for team in teams},
            "model": "demo-win-probability-v1",
        }
    return output
