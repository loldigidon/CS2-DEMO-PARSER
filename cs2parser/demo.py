"""Wrapper around awpy.Demo for parsing and table extraction."""
from __future__ import annotations

from statistics import median
from typing import Any
import gc

import pandas as pd

from config import ParserConfig
from .normalize import normalize_table


BASE_TABLE_PROPS = [
    "rounds",
    "kills",
    "damages",
    "grenades",
    "smokes",
    "infernos",
    "bomb",
    "ticks",
]
OPTIONAL_TABLE_PROPS = {"shots": "skip_shots", "footsteps": "skip_footsteps"}
ALL_TABLE_PROPS = BASE_TABLE_PROPS + list(OPTIONAL_TABLE_PROPS)


def enabled_table_props(cfg: ParserConfig | None = None) -> list[str]:
    cfg = cfg or ParserConfig()
    props = list(BASE_TABLE_PROPS)
    for table_name, flag_name in OPTIONAL_TABLE_PROPS.items():
        if not getattr(cfg, flag_name, False):
            props.append(table_name)
    return props


def _to_pandas(obj: Any) -> pd.DataFrame:
    """Convert Polars/pandas/None without losing nullable UInt64 identifiers."""
    if obj is None:
        return pd.DataFrame()
    if isinstance(obj, pd.DataFrame):
        return normalize_table(obj)
    to_pandas = getattr(obj, "to_pandas", None)
    if callable(to_pandas):
        try:
            return normalize_table(to_pandas(use_pyarrow_extension_array=True))
        except TypeError:
            return normalize_table(to_pandas())
    try:
        return normalize_table(pd.DataFrame(obj))
    except Exception:
        return pd.DataFrame()


def _safe_prop(dem, name: str):
    try:
        return getattr(dem, name)
    except (KeyError, AttributeError) as exc:
        print(f"    [skip] table '{name}' unavailable for this demo: {exc}")
        return None
    except Exception as exc:
        print(f"    [warn] failed to read '{name}': {exc}")
        return None


def detect_tickrate(dem) -> int:
    """Infer server tickrate from exact `game_time` progression.

    CS2 demos expose `game_time` in seconds. The median `delta_tick / delta_time`
    is stable even when some ticks are absent from the parsed stream.
    """
    raw = _to_pandas(dem.parser.parse_ticks(wanted_props=["game_time"]))
    if raw.empty or len(raw) < 2 or not {"tick", "game_time"}.issubset(raw.columns):
        return 64
    pairs = raw[["tick", "game_time"]].dropna().drop_duplicates().sort_values("tick")
    dtick = pd.to_numeric(pairs["tick"], errors="coerce").diff()
    dtime = pd.to_numeric(pairs["game_time"], errors="coerce").diff()
    ratios = (dtick / dtime)[(dtick > 0) & (dtime > 0)]
    ratios = ratios[(ratios >= 16) & (ratios <= 256)]
    if ratios.empty:
        return 64
    estimate = int(round(float(median(ratios.tolist()))))
    # Snap tiny floating errors to common rates while still allowing custom rates.
    for common in (32, 64, 128):
        if abs(estimate - common) <= 2:
            return common
    return estimate


def _tickrate_from_state_ticks(state_ticks: Any) -> int:
    """Infer tickrate from a state-tick table already read during parsing."""
    raw = _to_pandas(state_ticks)
    if raw.empty or len(raw) < 2 or not {"tick", "game_time"}.issubset(raw.columns):
        return 64
    pairs = raw[["tick", "game_time"]].dropna().drop_duplicates().sort_values("tick")
    dtick = pd.to_numeric(pairs["tick"], errors="coerce").diff()
    dtime = pd.to_numeric(pairs["game_time"], errors="coerce").diff()
    ratios = (dtick / dtime)[(dtick > 0) & (dtime > 0)]
    ratios = ratios[(ratios >= 16) & (ratios <= 256)]
    if ratios.empty:
        return 64
    estimate = int(round(float(median(ratios.tolist()))))
    for common in (32, 64, 128):
        if abs(estimate - common) <= 2:
            return common
    return estimate


def parse_demo(path: str, cfg: ParserConfig | None = None):
    """Parse a demo locally; no network access or external enrichment is used.

    Awpy's convenience ``Demo.parse`` uses one player-property list for both
    sparse event rows and every player tick.  Requesting ``inventory`` that way
    makes a long match consume gigabytes.  This implementation performs the
    same stages explicitly: rich event properties, compact scalar tick
    properties, then grenades.  It is equivalent for downstream tables but far
    faster and substantially less memory hungry.
    """
    import polars as pl
    import awpy
    from awpy import Demo

    cfg = cfg or ParserConfig()
    initial_tickrate = cfg.tickrate or 64
    dem = Demo(path, verbose=cfg.verbose, tickrate=initial_tickrate)

    if cfg.events is not None:
        events = list(cfg.events)
    else:
        events = list(dem.default_events)
        if not cfg.skip_footsteps and "player_footstep" not in events:
            events.append("player_footstep")

    # 1) Rich properties only on sparse event rows.  In particular this keeps
    # victim_inventory available at death without storing inventory each tick.
    dem.events = dem.parse_events(events, player_props=cfg.event_player_props())
    dem.rounds = awpy.parsers.rounds.create_round_df(dem.events)

    # 2) Read compact match-state ticks once.  Reuse them for both in-play
    # filtering and tickrate detection instead of making a third full pass.
    state_ticks = dem.parse_ticks(other_props=[
        "game_time", "is_bomb_planted", "which_bomb_zone",
        "is_freeze_period", "is_warmup_period", "is_terrorist_timeout",
        "is_ct_timeout", "is_technical_timeout", "is_waiting_for_resume",
        "is_match_started", "game_phase",
    ])
    dem.in_play_ticks = awpy.parsers.ticks.get_valid_ticks(state_ticks)
    detected = int(cfg.tickrate) if cfg.tickrate is not None else _tickrate_from_state_ticks(state_ticks)
    dem.tickrate = detected
    del state_ticks
    gc.collect()

    # 3) Compact scalar player ticks.  Position fields are included only when
    # requested by the visualizer; list-valued inventory is never included.
    dem.ticks = dem.parse_ticks(player_props=cfg.tick_player_props())
    # Keep one unfiltered post-match row per player.  The last round MVP is
    # awarded after the final in-play tick, so filtering first would undercount
    # ``m_iMVPs`` for the final-round MVP.
    final_keys = [column for column in ("steamid", "name") if column in dem.ticks.columns]
    dem.final_player_totals = (
        dem.ticks.sort("tick").group_by(final_keys, maintain_order=True).last()
        if final_keys else pl.DataFrame()
    )
    dem.ticks = dem.ticks.filter(pl.col("tick").is_in(dem.in_play_ticks))
    dem.ticks = awpy.parsers.rounds.apply_round_num(
        df=dem.ticks, rounds_df=dem.rounds, tick_col="tick"
    ).filter(pl.col("round_num").is_not_null())
    dem.ticks = awpy.parsers.utils.fix_common_names(dem.ticks)

    # 4) Grenade trajectories, filtered to the actual match rounds.
    dem.grenades = dem.parse_grenades()
    dem.grenades = dem.grenades.filter(pl.col("tick").is_in(dem.in_play_ticks))
    dem.grenades = awpy.parsers.rounds.apply_round_num(
        df=dem.grenades, rounds_df=dem.rounds, tick_col="tick"
    ).filter(pl.col("round_num").is_not_null())

    dem.detected_tickrate = detected
    dem.requested_events = list(events)
    dem.parse_all_events = bool(cfg.all_events)
    return dem


def _direct_footsteps(dem) -> pd.DataFrame:
    events = getattr(dem, "events", {}) or {}
    source = events.get("player_footstep")
    if source is None:
        return pd.DataFrame()
    df = _to_pandas(source)
    if df.empty:
        return df
    rename = {
        "user_steamid": "steamid",
        "user_name": "name",
        "user_team_clan_name": "team_clan_name",
        "user_team_name": "side",
        "user_X": "X",
        "user_Y": "Y",
        "user_Z": "Z",
        "user_last_place_name": "place",
    }
    return normalize_table(df.rename(columns=rename))


def extract_tables(dem, cfg: ParserConfig | None = None) -> dict[str, pd.DataFrame]:
    cfg = cfg or ParserConfig()
    tables: dict[str, pd.DataFrame] = {}
    for name in enabled_table_props(cfg):
        if name == "footsteps":
            continue
        tables[name] = _to_pandas(_safe_prop(dem, name))

    # Awpy's `player_sound` convenience table is commonly absent in CS2.
    # Read the actual `player_footstep` event directly without a misleading warning.
    if not cfg.skip_footsteps:
        tables["footsteps"] = _direct_footsteps(dem)

    for name in BASE_TABLE_PROPS:
        tables.setdefault(name, pd.DataFrame())
    for name, flag_name in OPTIONAL_TABLE_PROPS.items():
        if not getattr(cfg, flag_name, False):
            tables.setdefault(name, pd.DataFrame())
        else:
            tables.pop(name, None)

    tables["final_player_totals"] = _to_pandas(getattr(dem, "final_player_totals", None))
    header = getattr(dem, "header", None)
    tables["header"] = pd.DataFrame([header]) if isinstance(header, dict) else _to_pandas(header)
    return tables
