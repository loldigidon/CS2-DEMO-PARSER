"""One canonical local processing flow shared by both CLIs."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pandas as pd

from config import ParserConfig
from .demo import extract_tables
from .derived import buy_outcomes, classify_buys, clutch_attempts, clutches, opening_kills, trades
from .events import assign_round_num, common_event_tables, extract_event_tables
from .players import player_stats
from .positions import enrich_tick_sides, positions_sampled, saved_ticks
from .rounds import build_rounds
from .sides import build_round_sides
from .teams import extract_teams
from .validation import validate_tables


def build_match_tables(dem, cfg: ParserConfig) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], ParserConfig]:
    """Build every local table from one already-parsed Awpy demo."""
    effective_cfg = replace(cfg, tickrate=int(getattr(dem, "detected_tickrate", dem.tickrate)))
    tables = extract_tables(dem, effective_cfg)

    event_tables: dict[str, pd.DataFrame] = {}
    if effective_cfg.all_events:
        event_tables, event_manifest = extract_event_tables(
            dem,
            tables.get("rounds", pd.DataFrame()),
            exhaustive=effective_cfg.exhaustive_events,
        )
        common = common_event_tables(event_tables)
        if effective_cfg.skip_footsteps:
            common.pop("footsteps", None)
        tables.update(common)
        tables["event_manifest"] = event_manifest
    else:
        tables["event_manifest"] = pd.DataFrame(columns=["event_name", "rows", "columns", "status", "error"])
        if not effective_cfg.skip_footsteps and not tables.get("footsteps", pd.DataFrame()).empty:
            steps = assign_round_num(tables["footsteps"], tables.get("rounds", pd.DataFrame()))
            if "round_num" in steps.columns:
                steps = steps[steps["round_num"].notna()].reset_index(drop=True)
            tables["footsteps"] = steps

    tables["rounds"] = build_rounds(tables)
    tables["round_sides"] = build_round_sides(tables)
    tables["ticks"] = enrich_tick_sides(tables.get("ticks", pd.DataFrame()), tables["round_sides"])
    # A second pass picks up side evidence from enriched ticks for quiet rounds.
    tables["round_sides"] = build_round_sides(tables)
    tables["ticks"] = enrich_tick_sides(tables.get("ticks", pd.DataFrame()), tables["round_sides"])

    tables["teams"] = extract_teams(tables.get("ticks", pd.DataFrame()))
    tables["buys"] = classify_buys(tables, effective_cfg)
    tables["trades"] = trades(tables, effective_cfg)
    tables["opening_kills"] = opening_kills(tables)
    tables["buy_outcomes"] = buy_outcomes(tables)
    tables["clutch_attempts"] = clutch_attempts(tables)
    tables["clutches"] = clutches(tables)

    if effective_cfg.with_positions:
        tables["positions_sampled"] = positions_sampled(
            tables.get("ticks", pd.DataFrame()),
            effective_cfg,
            tables["round_sides"],
        )

    if not effective_cfg.skip_validation_stats:
        tables["player_stats"] = player_stats(dem, effective_cfg, tables)

    tables["parse_metadata"] = pd.DataFrame([{
        "map_name": getattr(dem, "header", {}).get("map_name"),
        "tickrate": effective_cfg.tickrate,
        "all_events": effective_cfg.all_events,
        "exhaustive_events": effective_cfg.exhaustive_events,
        "detected_event_count": len(getattr(dem, "detected_events", [])),
        "parsed_event_count": len(event_tables) if effective_cfg.all_events else len(getattr(dem, "events", {})),
        "with_positions": effective_cfg.with_positions,
        "position_sample": effective_cfg.position_sample if effective_cfg.with_positions else None,
    }])

    # Derived stats use full tick rows; compact them only after all calculations.
    tables["ticks"] = saved_ticks(
        tables.get("ticks", pd.DataFrame()),
        rounds=tables.get("rounds", pd.DataFrame()),
        event_tables=[
            tables.get("kills", pd.DataFrame()),
            tables.get("damages", pd.DataFrame()),
            tables.get("bomb", pd.DataFrame()),
        ],
        sample_step=max(int(effective_cfg.tickrate), 1),
    )
    tables["validation"] = validate_tables(
        tables,
        with_positions=effective_cfg.with_positions,
        all_events=effective_cfg.all_events,
    )
    return tables, event_tables, effective_cfg
