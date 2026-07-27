"""Offline FACEIT-style dashboard generation for parsed CS2 demos.

The visualizer is intentionally static and local-only: it reads the project's
Parquet output, writes HTML/CSS/JS/JSON assets, and can serve them with Python's
standard-library HTTP server.  No external API or CDN is required.
"""
from __future__ import annotations

import base64
import functools
import html
import json
import math
import os
import shutil
import socket
import threading
import webbrowser
from collections import Counter, defaultdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from config import ParserConfig
from .positions import positions_sampled as sample_positions
from .round_swing import estimate_round_players


PACKAGE_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = PACKAGE_ROOT / "visualizer_assets"
BUNDLED_RADAR_ROOT = PACKAGE_ROOT / "radars"

# Source-engine overview transforms.  The Mirage transform is used by the
# supplied demo/radar.  Other common-map values provide sensible defaults; an
# unknown map falls back to an automatically fitted coordinate box.
MAP_TRANSFORMS: dict[str, dict[str, float]] = {
    "de_ancient": {"pos_x": -2953.0, "pos_y": 2164.0, "scale": 5.0},
    "de_anubis": {"pos_x": -2796.0, "pos_y": 3328.0, "scale": 5.22},
    "de_cache": {"pos_x": -2000.0, "pos_y": 3250.0, "scale": 5.5},
    "de_dust2": {"pos_x": -2476.0, "pos_y": 3239.0, "scale": 4.4},
    "de_inferno": {"pos_x": -2087.0, "pos_y": 3870.0, "scale": 4.9},
    "de_mirage": {"pos_x": -3230.0, "pos_y": 1713.0, "scale": 5.0},
    "de_nuke": {"pos_x": -3453.0, "pos_y": 2887.0, "scale": 7.0},
    "de_overpass": {"pos_x": -4831.0, "pos_y": 1781.0, "scale": 5.2},
    "de_train": {"pos_x": -2477.0, "pos_y": 2392.0, "scale": 4.7},
    "de_vertigo": {"pos_x": -3168.0, "pos_y": 1762.0, "scale": 4.0},
}

# Official-style vertical radar sections. Nuke's upper/default overview and
# lower overview share the same X/Y transform; entities are assigned by their
# world-space Z coordinate. The explicit split keeps playback deterministic.

MAP_NAME_ALIASES: dict[str, str] = {
    # Current FACEIT/GOTV demos can expose the updated Nuke workshop/internal
    # name while the overview assets keep the stable de_nuke filenames.
    "de_nuke2": "de_nuke",
}


def _canonical_map_name(map_name: str) -> str:
    normalized = str(map_name or "").strip().lower()
    return MAP_NAME_ALIASES.get(normalized, normalized)

MULTI_LEVEL_MAPS: dict[str, dict[str, Any]] = {
    "de_nuke": {
        "split_z": -500.0,
        "transition_z": 24.0,
        "levels": [
            {
                "id": "upper",
                "label": "Верхний этаж",
                "short_label": "Верх",
                "radar_section": "default",
                "min_z": -500.0,
                "max_z": None,
            },
            {
                "id": "lower",
                "label": "Нижний этаж",
                "short_label": "Низ",
                "radar_section": "lower",
                "min_z": None,
                "max_z": -500.0,
            },
        ],
    },
}

TABLE_NAMES = (
    "parse_metadata",
    "rounds",
    "round_sides",
    "teams",
    "ticks",
    "player_stats",
    "kills",
    "damages",
    "shots",
    "grenades",
    "flashes",
    "smokes",
    "infernos",
    "bomb",
    "positions_sampled",
    "buys",
    "trades",
    "opening_kills",
    "clutch_attempts",
    "clutches",
    "validation",
)


class VisualizationError(RuntimeError):
    """Raised when a parsed match cannot be visualized."""


def _read_table(match_dir: Path, name: str) -> pd.DataFrame:
    path = match_dir / f"{name}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, dtype_backend="pyarrow")
    except Exception as exc:  # pragma: no cover - depends on corrupt files
        raise VisualizationError(f"Не удалось прочитать {path.name}: {exc}") from exc


def load_match_tables(match_dir: str | Path) -> dict[str, pd.DataFrame]:
    """Load the Parquet layer required by the dashboard."""
    root = Path(match_dir).expanduser().resolve()
    if not root.is_dir():
        raise VisualizationError(f"Папка матча не найдена: {root}")
    if not (root / "parse_metadata.parquet").exists():
        raise VisualizationError(
            f"В {root} нет parse_metadata.parquet. Укажите папку конкретного распарсенного матча."
        )
    return {name: _read_table(root, name) for name in TABLE_NAMES}


def find_parsed_matches(root: str | Path) -> list[Path]:
    """Return match folders directly under *root* (or *root* itself)."""
    path = Path(root).expanduser().resolve()
    if (path / "parse_metadata.parquet").exists():
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        (child for child in path.iterdir() if child.is_dir() and (child / "parse_metadata.parquet").exists()),
        key=lambda item: item.name.lower(),
    )


def _value(value: Any) -> Any:
    """Convert pandas/numpy scalars to JSON-safe values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 4)
    if isinstance(value, (int, bool, str)):
        return value
    return str(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        if pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _identifier(steamid: Any, name: Any) -> str:
    sid = _value(steamid)
    if sid:
        return f"sid:{sid}"
    return f"name:{_value(name) or 'unknown'}"


def _player_rows(tables: dict[str, pd.DataFrame]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = tables["player_stats"].copy()
    if not stats.empty and "side" in stats.columns:
        stats = stats[stats["side"].astype(str).str.lower() == "all"].copy()

    if stats.empty:
        teams = tables["teams"].copy()
        stats = teams.assign(
            side="all", n_rounds=0, kills=0, deaths=0, assists=0, headshots=0,
            headshot_pct=0.0, opening_kills=0, opening_deaths=0, trade_kills=0,
            clutch_attempts=0, clutches_won=0, dmg=0.0, adr=0.0, kast_rounds=0,
            kast=0.0, impact=0.0, rating_hltv2=0.0, round_swing=0.0, rating=0.0,
            flash_assists=0, kills_per_round=0.0, deaths_per_round=0.0, assists_per_round=0.0,
            multi_kill_2k=0, multi_kill_3k=0, multi_kill_4k=0, multi_kill_5k=0, round_mvps=0,
            rws=0.0, shots=0, hits=0, accuracy=0.0,
            single_shot_accuracy=0.0, multi_kill_rounds=0, multi_kill_pct=0.0,
            rounds_survived=0, entry_attempts=0, entry_difference=0,
            entry_attempt_pct=0.0, entry_success_pct=0.0, deaths_traded=0,
            traded_entry_kills=0, traded_entry_deaths=0, clutch_losses=0,
            clutch_success_pct=0.0, clutch_1v1=0, clutch_1v2=0, clutch_1v3=0,
            clutch_1v4=0, clutch_1v5=0,
        )

    players: list[dict[str, Any]] = []
    for _, row in stats.iterrows():
        player_id = _identifier(row.get("steamid"), row.get("name"))
        kills = _int(row.get("kills"))
        deaths = _int(row.get("deaths"))
        rating = _float(row.get("rating"))
        players.append({
            "id": player_id,
            "steamid": _value(row.get("steamid")),
            "name": _value(row.get("name")) or "Unknown",
            "team": _value(row.get("team_clan_name")) or "Без команды",
            "rounds": _int(row.get("n_rounds")),
            "kills": kills,
            "deaths": deaths,
            "assists": _int(row.get("assists")),
            "flash_assists": _int(row.get("flash_assists")),
            "kd": round(kills / deaths, 2) if deaths else float(kills),
            "kills_per_round": round(_float(row.get("kills_per_round"), kills / max(_int(row.get("n_rounds")), 1)), 2),
            "deaths_per_round": round(_float(row.get("deaths_per_round"), deaths / max(_int(row.get("n_rounds")), 1)), 2),
            "assists_per_round": round(_float(row.get("assists_per_round")), 2),
            "headshots": _int(row.get("headshots")),
            "headshot_pct": round(_float(row.get("headshot_pct")), 1),
            "opening_kills": _int(row.get("opening_kills")),
            "opening_deaths": _int(row.get("opening_deaths")),
            "trade_kills": _int(row.get("trade_kills")),
            "clutch_attempts": _int(row.get("clutch_attempts")),
            "clutches_won": _int(row.get("clutches_won")),
            "multi_kill_2k": _int(row.get("multi_kill_2k")),
            "multi_kill_3k": _int(row.get("multi_kill_3k")),
            "multi_kill_4k": _int(row.get("multi_kill_4k")),
            "multi_kill_5k": _int(row.get("multi_kill_5k")),
            "round_mvps": _int(row.get("round_mvps")),
            "rws": round(_float(row.get("rws")), 2),
            "shots": _int(row.get("shots")),
            "hits": _int(row.get("hits")),
            "accuracy": round(_float(row.get("accuracy")), 1),
            "single_shot_accuracy": round(_float(row.get("single_shot_accuracy")), 1),
            "multi_kill_rounds": _int(row.get("multi_kill_rounds")),
            "multi_kill_pct": round(_float(row.get("multi_kill_pct")), 1),
            "rounds_survived": _int(row.get("rounds_survived")),
            "entry_attempts": _int(row.get("entry_attempts")),
            "entry_difference": _int(row.get("entry_difference")),
            "entry_attempt_pct": round(_float(row.get("entry_attempt_pct")), 1),
            "entry_success_pct": round(_float(row.get("entry_success_pct")), 1),
            "deaths_traded": _int(row.get("deaths_traded")),
            "traded_entry_kills": _int(row.get("traded_entry_kills")),
            "traded_entry_deaths": _int(row.get("traded_entry_deaths")),
            "clutch_losses": _int(row.get("clutch_losses")),
            "clutch_success_pct": round(_float(row.get("clutch_success_pct")), 1),
            "clutch_1v1": _int(row.get("clutch_1v1")),
            "clutch_1v2": _int(row.get("clutch_1v2")),
            "clutch_1v3": _int(row.get("clutch_1v3")),
            "clutch_1v4": _int(row.get("clutch_1v4")),
            "clutch_1v5": _int(row.get("clutch_1v5")),
            "damage": round(_float(row.get("dmg")), 1),
            "adr": round(_float(row.get("adr")), 1),
            "kast": round(_float(row.get("kast")), 1),
            "impact": round(_float(row.get("impact")), 2),
            "rating_hltv2": round(_float(row.get("rating_hltv2")), 2),
            "rating": round(rating, 2),
            "round_swing": round(_float(row.get("round_swing"), (rating - 1.1) * 12.5), 2),
            "rating_delta": round(_float(row.get("round_swing"), (rating - 1.1) * 12.5), 2),
        })
    players.sort(key=lambda p: (-p["rating"], -p["kills"], p["name"].lower()))
    return players, {player["id"]: index for index, player in enumerate(players)}


def _round_team_lookup(round_sides: pd.DataFrame) -> dict[tuple[int, str], str]:
    out: dict[tuple[int, str], str] = {}
    for _, row in round_sides.iterrows():
        rn = _int(row.get("round_num"), -1)
        side = str(_value(row.get("side")) or "").lower()
        team = _value(row.get("team_clan_name"))
        if rn >= 0 and side in {"ct", "t"} and team:
            out[(rn, side)] = str(team)
    return out


def _team_rows(
    players: list[dict[str, Any]],
    rounds: pd.DataFrame,
    round_sides: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[tuple[int, str], str]]:
    lookup = _round_team_lookup(round_sides)
    score: Counter[str] = Counter()
    for _, row in rounds.iterrows():
        rn = _int(row.get("round_num"), -1)
        winner = str(_value(row.get("winner")) or "").lower()
        team = lookup.get((rn, winner))
        if team:
            score[team] += 1

    team_names = {p["team"] for p in players}
    team_names.update(team for team in lookup.values())
    teams: list[dict[str, Any]] = []
    for team in team_names:
        team_players = [p for p in players if p["team"] == team]
        teams.append({
            "name": team,
            "score": int(score.get(team, 0)),
            "players": [p["id"] for p in team_players],
            "avg_rating": round(sum(p["rating"] for p in team_players) / len(team_players), 2) if team_players else 0.0,
        })
    teams.sort(key=lambda t: (-t["score"], -t["avg_rating"], t["name"].lower()))
    return teams, lookup


def _valid_kill(row: pd.Series) -> bool:
    weapon = str(_value(row.get("weapon")) or "").lower()
    if weapon in {"world", "worldspawn", "trigger_hurt", "fall"}:
        return False
    attacker = _identifier(row.get("attacker_steamid"), row.get("attacker_name"))
    victim = _identifier(row.get("victim_steamid"), row.get("victim_name"))
    return attacker != victim and bool(_value(row.get("attacker_name"))) and bool(_value(row.get("victim_name")))


def _duel_data(kills: pd.DataFrame, player_index: dict[str, int]) -> dict[str, Any]:
    pair_counts: Counter[tuple[int, int]] = Counter()
    total_by_attacker: Counter[int] = Counter()
    if not kills.empty:
        for _, row in kills.iterrows():
            if not _valid_kill(row):
                continue
            attacker_id = _identifier(row.get("attacker_steamid"), row.get("attacker_name"))
            victim_id = _identifier(row.get("victim_steamid"), row.get("victim_name"))
            if attacker_id not in player_index or victim_id not in player_index:
                continue
            a = player_index[attacker_id]
            v = player_index[victim_id]
            pair_counts[(a, v)] += 1
            total_by_attacker[a] += 1
    pairs = [[a, v, count] for (a, v), count in sorted(pair_counts.items())]
    return {
        "pairs": pairs,
        "totals": [[index, count] for index, count in sorted(total_by_attacker.items())],
        "max_pair": max(pair_counts.values(), default=0),
    }


def _grenade_kind(value: Any) -> str | None:
    text = str(_value(value) or "").lower()
    if "flash" in text:
        return "flash"
    if "smoke" in text:
        return "smoke"
    if "hegrenade" in text or "high explosive" in text:
        return "he"
    if "molotov" in text or "incendiary" in text:
        return "fire"
    if "decoy" in text:
        return "decoy"
    return None


def _thrown_grenades(grenades: pd.DataFrame) -> pd.DataFrame:
    """Return one row per grenade that was actually thrown.

    Awpy's ``grenades`` table is a trajectory table, not a throw-event table.
    It contains many ticks for the same entity and, depending on the demo,
    both the inventory grenade entity (``CFlashbang``, ``CSmokeGrenade`` ...)
    and its flying projectile (``CFlashbangProjectile`` ...).  Counting both
    classes inflates utility totals several times over.

    A projectile entity is created once for every real throw, including a
    molotov/incendiary that gets extinguished before an inferno starts.  We
    therefore keep projectile classes only and deduplicate their trajectories
    by round/entity/thrower.  Rows without an entity id use a conservative
    tick-based fallback so they are not all collapsed into one throw.
    """
    if grenades.empty or "grenade_type" not in grenades.columns:
        return grenades.iloc[0:0].copy()

    projectile_mask = (
        grenades["grenade_type"]
        .astype("string")
        .str.contains("projectile", case=False, na=False)
    )
    projectiles = grenades.loc[projectile_mask].copy()
    if projectiles.empty:
        return projectiles

    if "tick" in projectiles.columns:
        projectiles = projectiles.sort_values("tick", kind="stable")

    round_cols = ["round_num"] if "round_num" in projectiles.columns else []
    thrower_cols = [
        column for column in ("thrower_steamid", "thrower")
        if column in projectiles.columns
    ]

    if "entity_id" not in projectiles.columns:
        fallback = round_cols + thrower_cols + [
            column for column in ("grenade_type", "tick")
            if column in projectiles.columns
        ]
        return projectiles.drop_duplicates(fallback or None, keep="first")

    has_entity = projectiles["entity_id"].notna()
    identified = projectiles.loc[has_entity]
    anonymous = projectiles.loc[~has_entity]

    if not identified.empty:
        identity = round_cols + ["entity_id"] + thrower_cols[:1]
        identified = identified.drop_duplicates(identity, keep="first")

    if not anonymous.empty:
        fallback = round_cols + thrower_cols + [
            column for column in ("grenade_type", "tick")
            if column in anonymous.columns
        ]
        anonymous = anonymous.drop_duplicates(fallback or None, keep="first")

    return pd.concat([identified, anonymous], ignore_index=True)


def _death_inventory_unused(
    grenades: pd.DataFrame,
    kills: pd.DataFrame,
    player_index: dict[str, int],
    ticks: pd.DataFrame | None = None,
) -> dict[int, Counter[str]]:
    """Count utility still owned immediately before every death.

    New parses store Awpy's exact ``inventory`` player property.  It preserves
    stacked flashbangs and therefore matches FACEIT's unused-utility totals.
    Older parses fall back to grenade inventory entities (which can undercount
    a second flashbang but still give correct HE/smoke/fire values).
    """
    result: dict[int, Counter[str]] = defaultdict(Counter)
    if kills.empty:
        return result

    # New fast parses attach the exact pre-death inventory to the sparse
    # player_death event.  Prefer it over a per-tick inventory column: it keeps
    # stacked flashbangs while avoiding millions of list-valued tick cells.
    if "victim_inventory" in kills.columns:
        used_event_inventory = False
        for _, death in kills.iterrows():
            pid = _identifier(death.get("victim_steamid"), death.get("victim_name"))
            index = player_index.get(pid)
            if index is None:
                continue
            inventory = death.get("victim_inventory")
            if inventory is None:
                continue
            try:
                if pd.isna(inventory):
                    continue
            except (TypeError, ValueError):
                pass
            try:
                items = list(inventory)
            except TypeError:
                items = []
            if items:
                used_event_inventory = True
            for item in items:
                kind = _grenade_kind(item)
                if kind and kind != "decoy":
                    result[index][kind] += 1
        if used_event_inventory:
            return result

    ticks = ticks if ticks is not None else pd.DataFrame()
    if not ticks.empty and "inventory" in ticks.columns and {"round_num", "tick", "name"}.issubset(ticks.columns):
        ordered = ticks.sort_values(["round_num", "name", "tick"], kind="stable")
        for _, death in kills.iterrows():
            pid = _identifier(death.get("victim_steamid"), death.get("victim_name"))
            index = player_index.get(pid)
            if index is None:
                continue
            rn, tick, name = _int(death.get("round_num"), -1), _int(death.get("tick"), -1), death.get("victim_name")
            before = ordered[(ordered["round_num"] == rn) & (ordered["name"] == name) & (ordered["tick"] < tick)]
            if before.empty:
                continue
            inventory = before.iloc[-1].get("inventory")
            if inventory is None:
                continue
            try:
                items = list(inventory)
            except TypeError:
                items = []
            for item in items:
                kind = _grenade_kind(item)
                if kind and kind != "decoy":
                    result[index][kind] += 1
        return result

    if grenades.empty or "grenade_type" not in grenades.columns:
        return result
    inventory = grenades[~grenades["grenade_type"].astype(str).str.contains("projectile", case=False, na=False)].copy()
    required = {"round_num", "tick", "entity_id"}
    if inventory.empty or not required.issubset(inventory.columns):
        return result
    owner_col = "thrower_steamid" if "thrower_steamid" in inventory.columns else "thrower"
    for _, death in kills.iterrows():
        pid = _identifier(death.get("victim_steamid"), death.get("victim_name"))
        index = player_index.get(pid)
        if index is None:
            continue
        rn, tick = _int(death.get("round_num"), -1), _int(death.get("tick"), -1)
        owner = death.get("victim_steamid") if owner_col == "thrower_steamid" else death.get("victim_name")
        nearby = inventory[(inventory["round_num"] == rn) & (inventory[owner_col] == owner) & (inventory["tick"] <= tick) & (inventory["tick"] >= tick - 64)]
        if nearby.empty:
            continue
        last = nearby.sort_values("tick").groupby("entity_id", dropna=False).tail(1)
        last = last[last["tick"] >= tick - 2]
        for _, held in last.iterrows():
            kind = _grenade_kind(held.get("grenade_type"))
            if kind and kind != "decoy":
                result[index][kind] += 1
    return result

def _effective_flashes(flashes: pd.DataFrame, kills: pd.DataFrame) -> pd.DataFrame:
    """Filter blind events to living victims and meaningful (>=1s) flashes."""
    if flashes.empty:
        return flashes.copy()
    out = flashes.copy()
    duration = pd.to_numeric(out.get("blind_duration", 0), errors="coerce").fillna(0.0)
    out = out[duration >= 1.0].copy()
    if out.empty or kills.empty or not {"round_num", "tick", "victim_name"}.issubset(kills.columns):
        return out
    deaths = kills.dropna(subset=["victim_name"]).groupby(["round_num", "victim_name"])["tick"].min().to_dict()
    alive_mask = []
    for _, row in out.iterrows():
        death_tick = deaths.get((_int(row.get("round_num")), row.get("user_name")))
        alive_mask.append(death_tick is None or _int(row.get("tick")) < _int(death_tick))
    return out.loc[alive_mask].copy()


def _utility_data(
    tables: dict[str, pd.DataFrame],
    players: list[dict[str, Any]],
    player_index: dict[str, int],
) -> dict[str, Any]:
    kinds = ("flash", "smoke", "he", "fire", "decoy")
    template = {
        **{kind: 0 for kind in kinds},
        **{f"unused_{kind}": 0 for kind in kinds},
        "successful_grenades": 0,
        # FACEIT includes direct grenade-projectile impacts in outgoing total
        # utility damage, while the HE/Burner breakdown and received totals use
        # explosion/inferno damage only.
        "impact_damage": 0.0, "impact_team_damage": 0.0,
        "he_damage": 0.0, "he_damage_received": 0.0, "he_team_damage": 0.0, "he_team_received": 0.0,
        "successful_he": 0,
        "fire_damage": 0.0, "fire_damage_received": 0.0, "fire_team_damage": 0.0, "fire_team_received": 0.0,
        "successful_fire": 0,
        "successful_flash": 0, "flash_assists": 0, "blind_kills": 0,
        "enemies_flashed": 0, "enemy_flash_duration": 0.0,
        "self_flashed": 0, "self_flash_duration": 0.0,
        "flashed_by_enemy": 0, "flashed_by_enemy_duration": 0.0,
        "teammates_flashed": 0, "teammate_flash_duration": 0.0,
        "flashed_by_team": 0, "flashed_by_team_duration": 0.0,
        "fires_extinguished": 0,
    }
    values: dict[int, dict[str, float]] = {i: dict(template) for i in range(len(players))}

    grenades = tables.get("grenades", pd.DataFrame())
    thrown = _thrown_grenades(grenades)
    for _, row in thrown.iterrows():
        pid = _identifier(row.get("thrower_steamid"), row.get("thrower"))
        kind = _grenade_kind(row.get("grenade_type"))
        if pid in player_index and kind:
            values[player_index[pid]][kind] += 1

    kills = tables.get("kills", pd.DataFrame())
    unused = _death_inventory_unused(grenades, kills, player_index, tables.get("ticks", pd.DataFrame()))
    for index, counts in unused.items():
        for kind, count in counts.items():
            values[index][f"unused_{kind}"] += count

    damages = tables.get("damages", pd.DataFrame())
    dmg_col = "dmg_health_real" if "dmg_health_real" in damages.columns else "dmg_health"
    successful_he: set[tuple[Any, ...]] = set()
    fire_damage_events: list[pd.Series] = []
    if not damages.empty and dmg_col in damages.columns:
        for _, row in damages.iterrows():
            weapon = str(_value(row.get("weapon")) or "").lower()
            group = "he" if weapon == "hegrenade" else "fire" if weapon == "inferno" else None
            direct_impact = weapon in {"flashbang", "smokegrenade", "decoy", "molotov", "incgrenade"}
            if not group and not direct_impact:
                continue
            amount = max(0.0, _float(row.get(dmg_col)))
            attacker_id = _identifier(row.get("attacker_steamid"), row.get("attacker_name"))
            victim_id = _identifier(row.get("victim_steamid"), row.get("victim_name"))
            ai, vi = player_index.get(attacker_id), player_index.get(victim_id)
            attacker_team, victim_team = _value(row.get("attacker_team_clan_name")), _value(row.get("victim_team_clan_name"))
            same_player = attacker_id == victim_id
            teammate = bool(attacker_team and victim_team and attacker_team == victim_team and not same_player)
            enemy = bool(attacker_team and victim_team and attacker_team != victim_team)
            if ai is not None:
                if enemy:
                    if group:
                        values[ai][f"{group}_damage"] += amount
                        if group == "he" and amount > 0:
                            successful_he.add((ai, _int(row.get("round_num")), _int(row.get("tick"))))
                        if group == "fire" and amount > 0:
                            fire_damage_events.append(row)
                    elif direct_impact:
                        values[ai]["impact_damage"] += amount
                elif teammate:
                    if group:
                        values[ai][f"{group}_team_damage"] += amount
                    elif direct_impact:
                        values[ai]["impact_team_damage"] += amount
            # FACEIT's received and HE/Burner detail columns exclude direct
            # projectile impact damage (for example a 1 HP molotov hit).
            if vi is not None and group:
                if enemy:
                    values[vi][f"{group}_damage_received"] += amount
                elif teammate:
                    values[vi][f"{group}_team_received"] += amount
    for ai, _, _ in successful_he:
        values[ai]["successful_he"] += 1

    # A successful burner is one inferno entity that dealt enemy damage.
    infernos = tables.get("infernos", pd.DataFrame())
    successful_infernos: set[tuple[int, int, Any]] = set()
    if not infernos.empty and fire_damage_events:
        for _, inferno in infernos.iterrows():
            ai = player_index.get(_identifier(inferno.get("thrower_steamid"), inferno.get("thrower_name")))
            if ai is None:
                continue
            start, end, rn = _int(inferno.get("start_tick")), _int(inferno.get("end_tick")), _int(inferno.get("round_num"))
            attacker_team = players[ai]["team"]
            thrower_id = _identifier(inferno.get("thrower_steamid"), inferno.get("thrower_name"))
            hit = any(
                _int(event.get("round_num")) == rn and start <= _int(event.get("tick")) <= end
                and _identifier(event.get("attacker_steamid"), event.get("attacker_name")) == thrower_id
                and _value(event.get("attacker_team_clan_name")) == attacker_team
                and _value(event.get("victim_team_clan_name")) != attacker_team
                for event in fire_damage_events
            )
            if hit:
                entity = inferno.get("entity_id")
                token = entity if _value(entity) is not None else start
                successful_infernos.add((ai, rn, token))
    for ai, _, _ in successful_infernos:
        values[ai]["successful_fire"] += 1

    effective = _effective_flashes(tables.get("flashes", pd.DataFrame()), kills)
    successful_flash_entities: set[tuple[int, int, Any]] = set()
    for _, row in effective.iterrows():
        attacker_id = _identifier(row.get("attacker_steamid"), row.get("attacker_name"))
        victim_id = _identifier(row.get("user_steamid"), row.get("user_name"))
        ai, vi = player_index.get(attacker_id), player_index.get(victim_id)
        if ai is None or vi is None:
            continue
        duration = max(0.0, _float(row.get("blind_duration")))
        attacker_team, victim_team = players[ai]["team"], players[vi]["team"]
        if ai == vi:
            values[ai]["self_flashed"] += 1
            values[ai]["self_flash_duration"] += duration
        elif attacker_team == victim_team:
            values[ai]["teammates_flashed"] += 1
            values[ai]["teammate_flash_duration"] += duration
            values[vi]["flashed_by_team"] += 1
            values[vi]["flashed_by_team_duration"] += duration
        else:
            values[ai]["enemies_flashed"] += 1
            values[ai]["enemy_flash_duration"] += duration
            values[vi]["flashed_by_enemy"] += 1
            values[vi]["flashed_by_enemy_duration"] += duration
            entity = row.get("entityid")
            token = entity if _value(entity) is not None else _int(row.get("tick"))
            successful_flash_entities.add((ai, _int(row.get("round_num")), token))
    for ai, _, _ in successful_flash_entities:
        values[ai]["successful_flash"] += 1

    if not kills.empty:
        for _, row in kills.iterrows():
            assister = player_index.get(_identifier(row.get("assister_steamid"), row.get("assister_name")))
            attacker = player_index.get(_identifier(row.get("attacker_steamid"), row.get("attacker_name")))
            if assister is not None and bool(_value(row.get("assistedflash")) or False):
                values[assister]["flash_assists"] += 1
            if attacker is not None and bool(_value(row.get("attackerblind")) or False):
                values[attacker]["blind_kills"] += 1

    rows = []
    for index, player in enumerate(players):
        row = {"player": index, **values[index]}
        row["total"] = int(sum(row[k] for k in kinds))
        row["unused_total"] = int(sum(row[f"unused_{k}"] for k in kinds))
        row["successful_grenades"] = int(row["successful_he"] + row["successful_fire"] + row["successful_flash"])
        row["damage"] = round(row["he_damage"] + row["fire_damage"] + row["impact_damage"], 1)
        row["damage_received"] = round(row["he_damage_received"] + row["fire_damage_received"], 1)
        row["team_damage"] = round(row["he_team_damage"] + row["fire_team_damage"] + row["impact_team_damage"], 1)
        row["team_damage_received"] = round(row["he_team_received"] + row["fire_team_received"], 1)
        row["flash_duration"] = round(row["enemy_flash_duration"], 1)
        for key, value in list(row.items()):
            if isinstance(value, float):
                # Keep sub-second precision for blind durations.  The UI floors
                # the final total exactly like FACEIT's mm:ss presentation;
                # rounding here first can incorrectly add a whole second.
                row[key] = round(value, 3) if key.endswith("_duration") else round(value, 1)
        rows.append(row)
    rows.sort(key=lambda r: (-r["total"], -r["damage"], players[r["player"]]["name"].lower()))
    return {"players": rows, "max_total": max((r["total"] for r in rows), default=0)}

def _map_transform(map_name: str, positions: pd.DataFrame) -> dict[str, Any]:
    map_name = _canonical_map_name(map_name)
    if map_name in MAP_TRANSFORMS:
        return {"mode": "overview", **MAP_TRANSFORMS[map_name], "width": 1024, "height": 1024}
    if positions.empty or not {"X", "Y"}.issubset(positions.columns):
        return {"mode": "none", "width": 1024, "height": 1024}
    xs = pd.to_numeric(positions["X"], errors="coerce").dropna()
    ys = pd.to_numeric(positions["Y"], errors="coerce").dropna()
    if xs.empty or ys.empty:
        return {"mode": "none", "width": 1024, "height": 1024}
    x0, x1 = float(xs.quantile(0.005)), float(xs.quantile(0.995))
    y0, y1 = float(ys.quantile(0.005)), float(ys.quantile(0.995))
    padding = max((x1 - x0), (y1 - y0)) * 0.08
    return {
        "mode": "fit",
        "min_x": round(x0 - padding, 2),
        "max_x": round(x1 + padding, 2),
        "min_y": round(y0 - padding, 2),
        "max_y": round(y1 + padding, 2),
        "width": 1024,
        "height": 1024,
    }


def _map_levels(map_name: str) -> dict[str, Any]:
    """Describe radar image layers and their vertical world-space bounds."""
    definition = MULTI_LEVEL_MAPS.get(_canonical_map_name(map_name))
    if not definition:
        return {
            "mode": "single",
            "default": "upper",
            "split_z": None,
            "transition_z": 0.0,
            "levels": [{
                "id": "upper",
                "label": "Карта",
                "short_label": "Карта",
                "radar_section": "default",
                "min_z": None,
                "max_z": None,
            }],
        }
    return {
        "mode": "split",
        "default": "both",
        "split_z": float(definition["split_z"]),
        "transition_z": float(definition.get("transition_z", 0.0)),
        "levels": [dict(level) for level in definition["levels"]],
    }


def _position_frames(
    positions: pd.DataFrame,
    player_index: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    if positions.empty or not {"round_num", "tick", "X", "Y"}.issubset(positions.columns):
        return {}
    ordered = positions.sort_values(["round_num", "tick", "steamid"] if "steamid" in positions.columns else ["round_num", "tick"])
    by_round: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (rn, tick), frame in ordered.groupby(["round_num", "tick"], sort=True):
        payload = []
        for _, row in frame.iterrows():
            pid = _identifier(row.get("steamid"), row.get("name"))
            index = player_index.get(pid)
            if index is None:
                continue
            side = str(_value(row.get("side")) or "").lower()
            alive = bool(row.get("is_alive")) if _value(row.get("is_alive")) is not None else True
            payload.append([
                index,
                round(_float(row.get("X")), 1),
                round(_float(row.get("Y")), 1),
                round(_float(row.get("Z")), 1),
                round(_float(row.get("yaw")), 1),
                round(_float(row.get("pitch")), 1),
                1 if alive else 0,
                1 if side == "ct" else 0,
            ])
        if payload:
            by_round[str(_int(rn))].append({"tick": _int(tick), "p": payload})
    return dict(by_round)


def _trajectory_sample(frame: pd.DataFrame, limit: int = 42) -> list[list[Any]]:
    """Compact one projectile trajectory while retaining its shape."""
    if frame.empty:
        return []
    ordered = frame.sort_values("tick", kind="stable").copy()
    for column in ("tick", "X", "Y", "Z"):
        ordered[column] = pd.to_numeric(ordered[column], errors="coerce")
    ordered = ordered.dropna(subset=["tick", "X", "Y", "Z"])
    if ordered.empty:
        return []

    # Awpy keeps many projectile entities alive at a fixed landing coordinate.
    # Stop the visible flight at the last meaningful movement instead of
    # drawing hundreds of duplicate stationary points.
    delta = (
        ordered[["X", "Y", "Z"]]
        .diff()
        .pow(2)
        .sum(axis=1)
        .pow(0.5)
        .fillna(1.0)
    )
    moving_positions = [index for index, moving in enumerate(delta.gt(0.35).tolist()) if moving]
    if moving_positions:
        ordered = ordered.iloc[: moving_positions[-1] + 1]
    if len(ordered) > limit:
        indexes = sorted(set(int(round(v)) for v in pd.Series(range(limit)).map(
            lambda i: i * (len(ordered) - 1) / max(limit - 1, 1)
        )))
        ordered = ordered.iloc[indexes]
    return [
        [_int(row.get("tick")), round(_float(row.get("X")), 1), round(_float(row.get("Y")), 1), round(_float(row.get("Z")), 1)]
        for _, row in ordered.iterrows()
    ]


def _utility_events(
    tables: dict[str, pd.DataFrame],
    player_index: dict[str, int],
    tickrate: int,
) -> dict[str, list[dict[str, Any]]]:
    """Build compact per-round projectile/effect events for the radar."""
    grenades = tables.get("grenades", pd.DataFrame())
    if grenades.empty or not {"round_num", "tick", "grenade_type", "X", "Y"}.issubset(grenades.columns):
        return {}
    projectile = grenades[
        grenades["grenade_type"].astype("string").str.contains("projectile", case=False, na=False)
    ].copy()
    if projectile.empty:
        return {}

    smokes = tables.get("smokes", pd.DataFrame())
    infernos = tables.get("infernos", pd.DataFrame())
    flashes = tables.get("flashes", pd.DataFrame())
    damages = tables.get("damages", pd.DataFrame())
    damage_column = "dmg_health_real" if "dmg_health_real" in damages.columns else (
        "dmg_health" if "dmg_health" in damages.columns else None
    )

    smoke_lookup: dict[tuple[int, int], pd.Series] = {}
    if not smokes.empty and {"round_num", "entity_id"}.issubset(smokes.columns):
        smoke_lookup = {
            (_int(row.get("round_num")), _int(row.get("entity_id"), -1)): row
            for _, row in smokes.iterrows()
        }

    output: dict[str, list[dict[str, Any]]] = defaultdict(list)
    group_columns = ["round_num", "entity_id"] if "entity_id" in projectile.columns else ["round_num", "thrower", "grenade_type"]
    for group_key, frame in projectile.groupby(group_columns, dropna=False, sort=True):
        first = frame.sort_values("tick", kind="stable").iloc[0]
        round_num = _int(first.get("round_num"))
        entity_id = _int(first.get("entity_id"), -1)
        kind = _grenade_kind(first.get("grenade_type"))
        thrower_id = _identifier(first.get("thrower_steamid"), first.get("thrower"))
        thrower = player_index.get(thrower_id)
        if not kind or thrower is None:
            continue
        path = _trajectory_sample(frame)
        if not path:
            continue
        start_tick, land_tick = path[0][0], path[-1][0]
        land_x, land_y, land_z = path[-1][1:]
        effect_start = land_tick
        effect_end = land_tick + max(tickrate, 1) * {
            "flash": 3, "smoke": 18, "he": 2, "fire": 7, "decoy": 15,
        }.get(kind, 2)

        if kind == "smoke":
            smoke = smoke_lookup.get((round_num, entity_id))
            if smoke is not None:
                effect_start = _int(smoke.get("start_tick"), land_tick)
                effect_end = _int(smoke.get("end_tick"), effect_start + tickrate * 18)
                if effect_end <= effect_start:
                    effect_end = effect_start + tickrate * 18
                land_x, land_y, land_z = (
                    round(_float(smoke.get("X"), land_x), 1),
                    round(_float(smoke.get("Y"), land_y), 1),
                    round(_float(smoke.get("Z"), land_z), 1),
                )
        elif kind == "fire" and not infernos.empty:
            candidates = infernos[infernos.get("round_num", pd.Series(dtype="int64")) == round_num].copy()
            if "thrower_steamid" in candidates.columns:
                candidates = candidates[
                    candidates["thrower_steamid"].astype("string") == str(_value(first.get("thrower_steamid")))
                ]
            elif "thrower_name" in candidates.columns:
                candidates = candidates[candidates["thrower_name"].astype(str) == str(_value(first.get("thrower")))]
            if not candidates.empty and "start_tick" in candidates.columns:
                candidates["_gap"] = (pd.to_numeric(candidates["start_tick"], errors="coerce") - land_tick).abs()
                fire = candidates.sort_values("_gap").iloc[0]
                if _float(fire.get("_gap"), 9999) <= tickrate:
                    effect_start = _int(fire.get("start_tick"), land_tick)
                    effect_end = _int(fire.get("end_tick"), effect_start + tickrate * 7)
                    land_x, land_y, land_z = (
                        round(_float(fire.get("X"), land_x), 1),
                        round(_float(fire.get("Y"), land_y), 1),
                        round(_float(fire.get("Z"), land_z), 1),
                    )

        damage = 0.0
        if damage_column and not damages.empty and kind in {"he", "fire"}:
            weapon = damages.get("weapon", pd.Series(index=damages.index, dtype="object")).astype(str).str.lower()
            wanted = weapon.eq("hegrenade") if kind == "he" else weapon.eq("inferno")
            rows = damages[wanted & (damages.get("round_num", pd.Series(index=damages.index)) == round_num)].copy()
            if "attacker_steamid" in rows.columns:
                rows = rows[rows["attacker_steamid"].astype("string") == str(_value(first.get("thrower_steamid")))]
            if "tick" in rows.columns:
                rows = rows[pd.to_numeric(rows["tick"], errors="coerce").between(effect_start - 8, effect_end + 8)]
            if {"attacker_team_clan_name", "victim_team_clan_name"}.issubset(rows.columns):
                rows = rows[rows["attacker_team_clan_name"].astype(str) != rows["victim_team_clan_name"].astype(str)]
            damage = float(pd.to_numeric(rows[damage_column], errors="coerce").fillna(0).clip(lower=0).sum())

        blind_duration = 0.0
        flashed = 0
        if kind == "flash" and not flashes.empty:
            rows = flashes[flashes.get("round_num", pd.Series(index=flashes.index)) == round_num].copy()
            entity_column = "entityid" if "entityid" in rows.columns else "entity_id" if "entity_id" in rows.columns else None
            if entity_column:
                exact = rows[pd.to_numeric(rows[entity_column], errors="coerce") == entity_id]
                if not exact.empty:
                    rows = exact
            if "attacker_steamid" in rows.columns:
                rows = rows[rows["attacker_steamid"].astype("string") == str(_value(first.get("thrower_steamid")))]
            if "tick" in rows.columns:
                rows = rows[pd.to_numeric(rows["tick"], errors="coerce").between(land_tick - 12, land_tick + tickrate * 6)]
            if {"attacker_team_clan_name", "user_team_clan_name"}.issubset(rows.columns):
                rows = rows[rows["attacker_team_clan_name"].astype(str) != rows["user_team_clan_name"].astype(str)]
            blind_duration = float(pd.to_numeric(rows.get("blind_duration", 0), errors="coerce").fillna(0).sum())
            flashed = int(len(rows))

        output[str(round_num)].append({
            "id": f"{round_num}:{entity_id}:{start_tick}",
            "entity": entity_id,
            "player": thrower,
            "kind": kind,
            "start": start_tick,
            "land": land_tick,
            "effect_start": effect_start,
            "end": effect_end,
            "x": land_x,
            "y": land_y,
            "z": land_z,
            "damage": round(damage, 1),
            "flashed": flashed,
            "blind": round(blind_duration, 2),
            "path": path,
        })
    for events in output.values():
        events.sort(key=lambda event: (event["start"], event["entity"]))
    return dict(output)


def _kill_events(kills: pd.DataFrame, player_index: dict[str, int]) -> dict[str, list[list[Any]]]:
    out: dict[str, list[list[Any]]] = defaultdict(list)
    if kills.empty:
        return {}
    ordered = kills.sort_values([c for c in ["round_num", "tick"] if c in kills.columns])
    for _, row in ordered.iterrows():
        if not _valid_kill(row):
            continue
        a_id = _identifier(row.get("attacker_steamid"), row.get("attacker_name"))
        v_id = _identifier(row.get("victim_steamid"), row.get("victim_name"))
        if a_id not in player_index or v_id not in player_index:
            continue
        out[str(_int(row.get("round_num")))].append([
            _int(row.get("tick")),
            player_index[a_id],
            player_index[v_id],
            _value(row.get("weapon")) or "unknown",
            1 if bool(_value(row.get("headshot")) or False) else 0,
            1 if bool(_value(row.get("is_trade")) or False) else 0,
        ])
    return dict(out)



_ITEM_LABELS = {
    "ak47": "AK-47", "aug": "AUG", "awp": "AWP", "bizon": "PP-Bizon",
    "deagle": "Desert Eagle", "elite": "Dual Berettas", "famas": "FAMAS",
    "fiveseven": "Five-SeveN", "g3sg1": "G3SG1", "galilar": "Galil AR",
    "glock": "Glock-18", "hkp2000": "P2000", "m249": "M249", "m4a1": "M4A4",
    "m4a1_silencer": "M4A1-S", "m4a4": "M4A4", "mac10": "MAC-10",
    "mag7": "MAG-7", "mp5sd": "MP5-SD", "mp7": "MP7", "mp9": "MP9",
    "negev": "Negev", "nova": "Nova", "p250": "P250", "p90": "P90",
    "revolver": "R8 Revolver", "sawedoff": "Sawed-Off", "scar20": "SCAR-20",
    "sg556": "SG 553", "ssg08": "SSG 08", "taser": "Zeus x27", "tec9": "Tec-9",
    "ump45": "UMP-45", "usp_silencer": "USP-S", "xm1014": "XM1014",
    "flashbang": "Flashbang", "hegrenade": "HE Grenade", "smokegrenade": "Smoke Grenade",
    "molotov": "Molotov", "incgrenade": "Incendiary Grenade", "decoy": "Decoy Grenade",
    "vest": "Kevlar", "vesthelm": "Kevlar + Helmet", "defuser": "Defuse Kit",
}

_DISPLAY_ITEM_ALIASES = {
    "ak-47": "ak47", "awp": "awp", "desert eagle": "deagle", "dual berettas": "elite",
    "famas": "famas", "five-seven": "fiveseven", "galil ar": "galilar",
    "glock-18": "glock", "m4a1-s": "m4a1_silencer", "m4a4": "m4a4",
    "mp7": "mp7", "mp9": "mp9", "p250": "p250", "p90": "p90",
    "scar-20": "scar20", "ssg 08": "ssg08", "tec-9": "tec9", "usp-s": "usp_silencer",
    "xm1014": "xm1014", "zeus x27": "taser", "flashbang": "flashbang",
    "high explosive grenade": "hegrenade", "he grenade": "hegrenade",
    "smoke grenade": "smokegrenade", "molotov": "molotov",
    "incendiary grenade": "incgrenade", "decoy grenade": "decoy",
}

_PRIMARY_ITEMS = {
    "ak47", "aug", "awp", "bizon", "famas", "g3sg1", "galilar", "m249", "m4a1",
    "m4a1_silencer", "m4a4", "mac10", "mag7", "mp5sd", "mp7", "mp9", "negev",
    "nova", "p90", "sawedoff", "scar20", "sg556", "ssg08", "ump45", "xm1014",
}
_PISTOL_ITEMS = {"deagle", "elite", "fiveseven", "glock", "hkp2000", "p250", "revolver", "tec9", "usp_silencer"}
_GRENADE_ITEMS = {"flashbang", "hegrenade", "smokegrenade", "molotov", "incgrenade", "decoy"}
_GEAR_ITEMS = {"vest", "vesthelm", "defuser", "taser"}
_IGNORED_ITEMS = {"knife", "knife_t", "c4", "c4 explosive", "huntsman knife", "falchion knife"}
_MELEE_ITEM_HINTS = (
    "knife", "bayonet", "karambit", "dagger", "kukri", "stiletto", "talon",
    "ursus", "navaja", "paracord", "nomad", "bowie", "falchion", "huntsman",
)


def _normal_item_key(value: Any) -> str:
    raw = str(_value(value) or "").strip().lower().replace("weapon_", "")
    if raw in _DISPLAY_ITEM_ALIASES:
        return _DISPLAY_ITEM_ALIASES[raw]
    return raw.replace(" ", "_") if raw in _ITEM_LABELS else raw


def _economy_item(value: Any) -> tuple[str, str] | None:
    original = str(_value(value) or "").strip()
    if not original:
        return None
    lower = original.lower()
    if lower in _IGNORED_ITEMS or any(hint in lower for hint in _MELEE_ITEM_HINTS) or lower in {"c4", "c4_explosive"}:
        return None
    key = _DISPLAY_ITEM_ALIASES.get(lower, lower.replace("weapon_", ""))
    key = key.replace(" ", "_") if key in _ITEM_LABELS else key
    label = _ITEM_LABELS.get(key, original.replace("weapon_", "").replace("_", " ").strip().title())
    if key in _PRIMARY_ITEMS:
        kind = "primary"
    elif key in _PISTOL_ITEMS:
        kind = "pistol"
    elif key in _GRENADE_ITEMS:
        kind = "grenade"
    elif key in _GEAR_ITEMS:
        kind = "gear"
    else:
        # Unknown pickup names are still useful, but keep them out of the main
        # weapon columns unless they look like a real firearm.
        kind = "other"
    return label, kind


def _read_event_table(match_root: Path, name: str) -> pd.DataFrame:
    path = match_root / "events" / f"{name}.parquet"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, dtype_backend="pyarrow")
    except Exception:
        return pd.DataFrame()


def _economy_loadouts(
    match_root: Path,
    rounds: pd.DataFrame,
    player_index: dict[str, int],
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    pickups = _read_event_table(match_root, "item_pickup")
    required = {"round_num", "tick", "user_name", "user_steamid", "user_inventory", "item"}
    if pickups.empty or not required.issubset(pickups.columns):
        return {}

    bounds = {
        _int(row.get("round_num")): (_int(row.get("start")), _int(row.get("freeze_end")))
        for _, row in rounds.iterrows()
    }
    pickups = pickups.copy()
    pickups["_order"] = range(len(pickups))
    out: dict[tuple[int, int], list[dict[str, Any]]] = {}

    grouped: dict[tuple[int, int], list[pd.Series]] = defaultdict(list)
    for _, row in pickups.sort_values(["round_num", "tick", "_order"]).iterrows():
        rn = _int(row.get("round_num"), -1)
        if rn not in bounds:
            continue
        start, freeze_end = bounds[rn]
        tick = _int(row.get("tick"), -1)
        if tick < start or tick > freeze_end:
            continue
        pid = player_index.get(_identifier(row.get("user_steamid"), row.get("user_name")))
        if pid is None:
            continue
        grouped[(rn, pid)].append(row)

    for key, rows in grouped.items():
        last = rows[-1]
        inventory = last.get("user_inventory")
        if inventory is None or isinstance(inventory, str):
            values: list[Any] = []
        else:
            try:
                values = list(inventory)
            except TypeError:
                values = []
        # Awpy exposes inventory immediately before the pickup. A newly bought
        # firearm replaces the previous firearm in the same slot; grenades are
        # additive. Apply that replacement before adding the current item.
        current_item = last.get("item")
        current_parsed = _economy_item(current_item)
        if current_parsed is not None and current_parsed[1] in {"primary", "pistol"}:
            current_kind = current_parsed[1]
            values = [value for value in values if (_economy_item(value) or ("", ""))[1] != current_kind]
        values.append(current_item)
        # Gear is not represented in the weapon inventory. Preserve purchases
        # made earlier in the same buy phase as separate chips.
        values.extend(row.get("item") for row in rows if _normal_item_key(row.get("item")) in _GEAR_ITEMS)

        counts: Counter[tuple[str, str]] = Counter()
        for value in values:
            parsed = _economy_item(value)
            if parsed is not None:
                counts[parsed] += 1
        # A player can only carry one primary/pistol/kit/armor item. Flashbangs
        # are the only loadout item that can validly appear twice.
        for (name, kind), count in list(counts.items()):
            limit = 2 if name == "Flashbang" else 1
            counts[(name, kind)] = min(count, limit)
        items = [
            {"name": name, "type": kind, "count": count}
            for (name, kind), count in sorted(
                counts.items(),
                key=lambda item: ({"primary": 0, "pistol": 1, "gear": 2, "grenade": 3, "other": 4}.get(item[0][1], 5), item[0][0]),
            )
        ]
        out[key] = items
    return out


def _economy_data(
    match_root: Path,
    tables: dict[str, pd.DataFrame],
    players: list[dict[str, Any]],
    player_index: dict[str, int],
) -> dict[str, Any]:
    rounds = tables.get("rounds", pd.DataFrame())
    ticks = tables.get("ticks", pd.DataFrame())
    buys = tables.get("buys", pd.DataFrame())
    if rounds.empty or ticks.empty:
        return {"rounds": [], "summaries": []}

    loadouts = _economy_loadouts(match_root, rounds, player_index)
    buy_lookup: dict[tuple[int, str], dict[str, Any]] = {}
    if not buys.empty:
        for _, row in buys.iterrows():
            key = (_int(row.get("round_num")), str(_value(row.get("team_clan_name")) or ""))
            buy_lookup[key] = {
                "equip": _int(row.get("equip")),
                "buy_type": str(_value(row.get("buy_type")) or "unknown").lower(),
            }

    round_rows: list[dict[str, Any]] = []
    summary_acc: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "rounds": 0, "equip": 0, "money": 0, "pistol": 0, "eco": 0, "force": 0, "full": 0, "unknown": 0,
    })
    for _, round_row in rounds.sort_values("round_num").iterrows():
        rn = _int(round_row.get("round_num"))
        freeze_end = _int(round_row.get("freeze_end"))
        start = _int(round_row.get("start"))
        snapshot = ticks[(ticks["round_num"] == rn) & (ticks["tick"] == freeze_end)].copy()
        if snapshot.empty:
            candidates = ticks[(ticks["round_num"] == rn) & (ticks["tick"] >= start) & (ticks["tick"] <= freeze_end)].copy()
            if not candidates.empty:
                id_cols = [column for column in ("steamid", "name") if column in candidates.columns]
                snapshot = candidates.sort_values("tick").groupby(id_cols, dropna=False, as_index=False).tail(1)

        teams_payload: list[dict[str, Any]] = []
        if "team_clan_name" not in snapshot.columns:
            continue
        for team_name, team_rows in snapshot.groupby("team_clan_name", dropna=True):
            team = str(_value(team_name) or "Без команды")
            player_payload = []
            for _, row in team_rows.iterrows():
                pid = player_index.get(_identifier(row.get("steamid"), row.get("name")))
                if pid is None:
                    continue
                items = loadouts.get((rn, pid), [])
                primary = next((item["name"] for item in items if item["type"] == "primary"), "—")
                pistol = next((item["name"] for item in items if item["type"] == "pistol"), "—")
                player_payload.append({
                    "player": pid,
                    "side": str(_value(row.get("side")) or "").lower(),
                    "money": _int(row.get("balance")),
                    "equip": _int(row.get("current_equip_value")),
                    "primary": primary,
                    "pistol": pistol,
                    "items": items,
                })
            player_payload.sort(key=lambda item: (-item["equip"], -item["money"], players[item["player"]]["name"].lower()))
            money = sum(item["money"] for item in player_payload)
            player_equip = sum(item["equip"] for item in player_payload)
            buy = buy_lookup.get((rn, team), {})
            equip = _int(buy.get("equip"), player_equip)
            buy_type = str(buy.get("buy_type") or "unknown")
            side = next((item["side"] for item in player_payload if item["side"]), "")
            teams_payload.append({
                "name": team,
                "side": side,
                "equip": equip,
                "money": money,
                "buy_type": buy_type,
                "players": player_payload,
            })
            acc = summary_acc[team]
            acc["rounds"] += 1
            acc["equip"] += equip
            acc["money"] += money
            acc[buy_type if buy_type in {"pistol", "eco", "force", "full"} else "unknown"] += 1
        teams_payload.sort(key=lambda item: next((idx for idx, p in enumerate(players) if p["team"] == item["name"]), 999))
        round_rows.append({"number": rn, "teams": teams_payload})

    summaries = []
    for team, acc in summary_acc.items():
        count = max(_int(acc.get("rounds")), 1)
        summaries.append({
            "team": team,
            "avg_equip": round(acc["equip"] / count),
            "avg_money": round(acc["money"] / count),
            "pistol": acc["pistol"], "eco": acc["eco"], "force": acc["force"], "full": acc["full"],
        })
    summaries.sort(key=lambda item: next((idx for idx, p in enumerate(players) if p["team"] == item["team"]), 999))
    return {"rounds": round_rows, "summaries": summaries}

def _round_rows(
    rounds: pd.DataFrame,
    team_lookup: dict[tuple[int, str], str],
) -> list[dict[str, Any]]:
    output = []
    for _, row in rounds.sort_values("round_num").iterrows():
        rn = _int(row.get("round_num"))
        winner = str(_value(row.get("winner")) or "").lower()
        output.append({
            "number": rn,
            "start": _int(row.get("start")),
            "freeze_end": _int(row.get("freeze_end")),
            "end": _int(row.get("end")),
            "official_end": _int(row.get("official_end")),
            "winner_side": winner,
            "winner_team": team_lookup.get((rn, winner)),
            "reason": _value(row.get("reason")),
            "bomb_site": _value(row.get("bomb_site")),
            "bomb_plant": _value(row.get("bomb_plant")),
            "kills": _int(row.get("total_kills")),
            "damage": _int(row.get("total_damage")),
        })
    return output


def _awards(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not players:
        return []
    definitions = (
        ("MVP", "rating", "Лучший rating"),
        ("Фрагер", "kills", "Больше всего убийств"),
        ("Урон", "damage", "Максимальный урон"),
        ("Опенер", "opening_kills", "Первые убийства"),
        ("Клатчер", "clutches_won", "Выигранные клатчи"),
    )
    result = []
    for title, key, subtitle in definitions:
        player = max(players, key=lambda p: (p[key], p["rating"]))
        result.append({"title": title, "subtitle": subtitle, "player": players.index(player), "value": player[key]})
    return result


def build_dashboard_data(match_dir: str | Path) -> dict[str, Any]:
    """Build the compact JSON model consumed by the local dashboard."""
    root = Path(match_dir).expanduser().resolve()
    tables = load_match_tables(root)
    metadata = tables["parse_metadata"]
    meta_row = metadata.iloc[0] if not metadata.empty else pd.Series(dtype="object")
    map_name = str(_value(meta_row.get("map_name")) or "unknown")
    tickrate = max(_int(meta_row.get("tickrate"), 64), 1)

    players, player_index = _player_rows(tables)
    teams, team_lookup = _team_rows(players, tables["rounds"], tables["round_sides"])
    positions = tables["positions_sampled"]
    # Older outputs may contain full spatial columns in ticks but no dedicated
    # positions_sampled.parquet.  Recover playback data when possible instead
    # of silently showing an empty radar.
    if positions.empty:
        ticks = tables.get("ticks", pd.DataFrame())
        if {"tick", "X", "Y"}.issubset(ticks.columns):
            positions = sample_positions(
                ticks,
                ParserConfig(
                    with_positions=True,
                    position_sample=max(_int(meta_row.get("position_sample"), 16), 1),
                ),
                tables.get("round_sides", pd.DataFrame()),
            )

    validation = tables["validation"]
    validation_failures = []
    if not validation.empty and "status" in validation.columns:
        for _, row in validation[validation["status"].astype(str).str.lower() == "fail"].iterrows():
            validation_failures.append({
                "check": _value(row.get("check")),
                "severity": _value(row.get("severity")),
                "details": _value(row.get("details")),
            })

    round_players = estimate_round_players(tables, players, player_index, _canonical_map_name(map_name))
    utility_events = _utility_events(tables, player_index, tickrate)

    return {
        "version": 5,
        "match": {
            "id": root.name,
            "map": map_name,
            "tickrate": tickrate,
            "round_count": len(tables["rounds"]),
            "has_positions": not positions.empty,
            "position_sample": _int(meta_row.get("position_sample"), 0),
            "all_events": bool(meta_row.get("all_events")) if _value(meta_row.get("all_events")) is not None else False,
        },
        "teams": teams,
        "players": players,
        "awards": _awards(players),
        "duels": _duel_data(tables["kills"], player_index),
        "utility": _utility_data(tables, players, player_index),
        "economy": _economy_data(root, tables, players, player_index),
        "rounds": _round_rows(tables["rounds"], team_lookup),
        "round_players": round_players,
        "frames": _position_frames(positions, player_index),
        "kills": _kill_events(tables["kills"], player_index),
        "utility_events": utility_events,
        "map_transform": _map_transform(map_name, positions),
        "map_levels": _map_levels(map_name),
        "validation_failures": validation_failures,
    }


def _radar_candidates(
    map_name: str,
    roots: Iterable[Path],
    section: str = "default",
) -> list[Path]:
    map_name = _canonical_map_name(map_name)
    suffix = "" if section in {"", "default", "upper"} else f"_{section}"
    names = [
        f"{map_name}{suffix}_radar.png",
        f"{map_name}{suffix}.png",
        f"{map_name}{suffix}_radar.dds",
        f"{map_name}{suffix}.dds",
    ]
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for name in names:
            direct = root / name
            if direct.exists():
                found.append(direct)
            found.extend(root.rglob(name))
    return list(dict.fromkeys(path.resolve() for path in found))


def _write_placeholder_radar(target: Path, map_name: str, section: str = "default") -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1024, 1024), (7, 12, 20))
    draw = ImageDraw.Draw(image)
    for position in range(0, 1025, 64):
        draw.line((position, 0, position, 1024), fill=(20, 31, 45), width=1)
        draw.line((0, position, 1024, position), fill=(20, 31, 45), width=1)
    label = map_name if section == "default" else f"{map_name} / {section}"
    draw.text((40, 48), f"Radar not found: {label}", fill=(210, 220, 230))
    image.save(target, "PNG", optimize=True)


def _copy_radar(
    map_name: str,
    target: Path,
    radar_dir: str | Path | None = None,
    section: str = "default",
) -> bool:
    roots = []
    if radar_dir:
        roots.append(Path(radar_dir).expanduser().resolve())
    roots.append(BUNDLED_RADAR_ROOT)
    candidates = _radar_candidates(map_name, roots, section=section)
    if not candidates:
        _write_placeholder_radar(target, map_name, section=section)
        return False

    source = candidates[0]
    try:
        from PIL import Image

        with Image.open(source) as image:
            image.convert("RGBA").save(target, "PNG", optimize=True)
        return True
    except Exception as exc:
        raise VisualizationError(f"Не удалось конвертировать радар {source}: {exc}") from exc


def build_dashboard(
    match_dir: str | Path,
    destination: str | Path | None = None,
    radar_dir: str | Path | None = None,
) -> Path:
    """Generate a self-contained local dashboard and return ``index.html``."""
    match_root = Path(match_dir).expanduser().resolve()
    data = build_dashboard_data(match_root)
    output = Path(destination).expanduser().resolve() if destination else match_root / "visualization"
    output.mkdir(parents=True, exist_ok=True)

    for filename in ("index.html", "styles.css", "app.js"):
        source = STATIC_ROOT / filename
        if not source.exists():
            raise VisualizationError(f"В проекте отсутствует шаблон визуализатора: {source}")
        shutil.copy2(source, output / filename)

    radar_files: dict[str, Path] = {}
    radar_found: dict[str, bool] = {}
    for level in data["map_levels"]["levels"]:
        level_id = str(level["id"])
        filename = "radar.png" if level_id == "upper" else f"radar_{level_id}.png"
        target = output / filename
        found = _copy_radar(
            data["match"]["map"],
            target,
            radar_dir=radar_dir,
            section=str(level.get("radar_section") or "default"),
        )
        level["radar_file"] = filename
        radar_files[level_id] = target
        radar_found[level_id] = found
    data["match"]["radar_found"] = bool(radar_found.get("upper", False))
    data["match"]["radar_layers_found"] = radar_found
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with (output / "data.json").open("w", encoding="utf-8") as file:
        file.write(data_json)
    embedded_data_json = data_json.replace("</", "<\\/")
    radar_data_uris = {
        level_id: "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        for level_id, path in radar_files.items()
    }
    default_radar_uri = radar_data_uris.get("upper") or next(iter(radar_data_uris.values()))
    embedded_radar_json = json.dumps(default_radar_uri)
    embedded_radars_json = json.dumps(radar_data_uris)

    # A direct-open version is genuinely single-file: JSON, CSS, JavaScript
    # and the radar image are embedded. This keeps the map visible even when
    # standalone.html is copied by itself or opened directly from an archive.
    # The regular index.html remains preferred for the built-in local server.
    index_html = (output / "index.html").read_text(encoding="utf-8")
    css = (output / "styles.css").read_text(encoding="utf-8")
    javascript = (output / "app.js").read_text(encoding="utf-8")
    standalone = index_html.replace(
        '<link rel="stylesheet" href="styles.css">',
        f"<style>{css}</style>",
    ).replace(
        '<script src="app.js"></script>',
        (
            f"<script>window.__CS2_DATA__={embedded_data_json};"
            f"window.__CS2_RADAR__={embedded_radar_json};"
            f"window.__CS2_RADARS__={embedded_radars_json};</script>"
            f"<script>{javascript}</script>"
        ),
    )
    (output / "standalone.html").write_text(standalone, encoding="utf-8")
    # Make the primary entry point direct-open as well.  Users frequently open
    # index.html from Explorer/Finder or from an extracted archive; embedding
    # data and radar here avoids file:// fetch restrictions entirely.
    (output / "index.html").write_text(standalone, encoding="utf-8")
    return output / "index.html"


def build_dashboard_hub(
    dashboards: Iterable[str | Path],
    destination: str | Path | None = None,
) -> Path:
    """Create one offline entry page for a batch of generated dashboards."""
    paths = [Path(path).expanduser().resolve() for path in dashboards]
    if not paths:
        raise VisualizationError("Нельзя создать общую страницу без dashboard.")

    if destination is None:
        try:
            common_root = Path(os.path.commonpath([str(path.parent) for path in paths]))
        except ValueError as exc:
            raise VisualizationError(
                "Dashboard находятся на разных дисках; укажите destination для общей страницы."
            ) from exc
        output = common_root / "index.html"
    else:
        output = Path(destination).expanduser().resolve()
        if output.suffix.lower() != ".html":
            output = output / "index.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    cards: list[str] = []
    for path in paths:
        data_path = path.parent / "data.json"
        try:
            data = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        match = data.get("match", {})
        teams = data.get("teams", [])
        score = " — "
        if len(teams) >= 2:
            score = (
                f"{html.escape(str(teams[0].get('name') or 'Команда 1'))} "
                f"{_int(teams[0].get('score'))} : {_int(teams[1].get('score'))} "
                f"{html.escape(str(teams[1].get('name') or 'Команда 2'))}"
            )
        relative = os.path.relpath(path, output.parent).replace(os.sep, "/")
        cards.append(
            '<a class="match-card" href="'
            + html.escape(relative, quote=True)
            + '"><span class="map">'
            + html.escape(str(match.get("map") or "unknown"))
            + "</span><strong>"
            + score
            + "</strong><small>"
            + f"Раундов: {_int(match.get('round_count'))} · "
            + ("Радар готов" if match.get("radar_found") else "Радар без изображения")
            + "</small></a>"
        )

    document = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>CS2 Demo Parser — матчи</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; background: #080b11; color: #f4f7fb; }
    main { width: min(980px, calc(100% - 40px)); margin: 0 auto; padding: 64px 0; }
    h1 { margin: 0 0 8px; font-size: clamp(32px, 6vw, 56px); }
    p { margin: 0 0 32px; color: #91a0b5; }
    .matches { display: grid; gap: 14px; }
    .match-card { display: grid; grid-template-columns: 120px 1fr auto; gap: 18px;
      align-items: center; padding: 20px 22px; color: inherit; text-decoration: none;
      border: 1px solid #202a39; border-radius: 16px; background: #101620; }
    .match-card:hover { border-color: #6ea8ff; transform: translateY(-1px); }
    .map { color: #6ea8ff; font-weight: 800; text-transform: uppercase; }
    small { color: #91a0b5; }
    @media (max-width: 720px) { .match-card { grid-template-columns: 1fr; gap: 8px; } }
  </style>
</head>
<body><main><h1>Обработанные матчи</h1>
<p>Все демки из выбранного файла или папки готовы. Выберите матч.</p>
<section class="matches">""" + "".join(cards) + """</section></main></body></html>"""
    output.write_text(document, encoding="utf-8")
    return output


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def serve_dashboard(
    dashboard: str | Path,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> None:
    """Serve a generated dashboard until Ctrl+C."""
    path = Path(dashboard).expanduser().resolve()
    root = path.parent if path.is_file() else path
    if not (root / "index.html").exists():
        raise VisualizationError(f"index.html не найден в {root}")
    chosen_port = port or _free_port(host)

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer((host, chosen_port), handler)
    url = f"http://{host}:{chosen_port}/"
    print(f"[viz] Визуализация: {url}")
    print("[viz] Нажмите Ctrl+C, чтобы остановить локальный сервер.")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[viz] Сервер остановлен")
    finally:
        server.server_close()
