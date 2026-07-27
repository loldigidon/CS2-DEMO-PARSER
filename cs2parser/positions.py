"""Helpers for optional spatial exports and slim tick persistence."""
from __future__ import annotations

import pandas as pd

from config import CORE_TICK_PROPS, ParserConfig
from .normalize import normalize_table
from .sides import apply_round_sides


SAVED_TICK_COLUMNS = ["round_num", "tick", *CORE_TICK_PROPS, "side"]
POSITIONS_COLUMNS = [
    "round_num",
    "tick",
    "steamid",
    "name",
    "side",
    "team_clan_name",
    "X",
    "Y",
    "Z",
    "yaw",
    "pitch",
    "is_alive",
]


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def enrich_tick_sides(ticks: pd.DataFrame, round_sides: pd.DataFrame) -> pd.DataFrame:
    if ticks.empty:
        return ticks.copy()
    return normalize_table(apply_round_sides(ticks, round_sides))


def saved_ticks(
    ticks: pd.DataFrame,
    rounds: pd.DataFrame | None = None,
    event_tables: list[pd.DataFrame] | None = None,
    sample_step: int = 64,
) -> pd.DataFrame:
    """Persist a compact state layer sufficient for deterministic rebuilds.

    Raw demos contain one row per player per server tick.  Copying and writing
    that entire multi-million-row table after all derived calculations wastes
    minutes and large amounts of RAM.  Keep one one-second snapshot plus exact
    round boundaries and combat/objective event ticks.  The separately saved
    ``positions_sampled`` table remains the source for smooth radar playback.
    """
    if ticks.empty:
        return _empty(SAVED_TICK_COLUMNS)
    if "tick" not in ticks.columns:
        columns = [col for col in SAVED_TICK_COLUMNS if col in ticks.columns]
        return normalize_table(ticks[columns].copy()).reindex(columns=SAVED_TICK_COLUMNS)

    tick_num = pd.to_numeric(ticks["tick"], errors="coerce")
    step = max(int(sample_step), 1)
    keep = tick_num.notna() & ((tick_num.fillna(-1).astype("int64") % step) == 0)

    key_ticks: set[int] = set()
    rounds = rounds if rounds is not None else pd.DataFrame()
    if not rounds.empty:
        for column in ("start", "freeze_end", "end", "official_end"):
            if column in rounds.columns:
                key_ticks.update(
                    pd.to_numeric(rounds[column], errors="coerce").dropna().astype("int64").tolist()
                )
    for frame in event_tables or []:
        if frame is not None and not frame.empty and "tick" in frame.columns:
            key_ticks.update(
                pd.to_numeric(frame["tick"], errors="coerce").dropna().astype("int64").tolist()
            )
    if key_ticks:
        keep |= tick_num.isin(key_ticks)

    sampled = ticks.loc[keep].copy()
    # Always retain a participant/final-state row for every round/player.
    if {"round_num", "steamid"}.issubset(ticks.columns):
        final_rows = ticks.drop_duplicates(["round_num", "steamid"], keep="last")
        sampled = pd.concat([sampled, final_rows], ignore_index=True)
        dedup = [column for column in ("round_num", "tick", "steamid") if column in sampled.columns]
        sampled = sampled.drop_duplicates(dedup, keep="last")

    columns = [col for col in SAVED_TICK_COLUMNS if col in sampled.columns]
    return normalize_table(sampled[columns].copy()).reindex(columns=SAVED_TICK_COLUMNS)


def positions_sampled(
    ticks: pd.DataFrame,
    cfg: ParserConfig | None = None,
    round_sides: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build sampled spatial rows with reconstructed CT/T sides."""
    cfg = cfg or ParserConfig()
    if not cfg.with_positions or ticks.empty or "tick" not in ticks.columns:
        return _empty(POSITIONS_COLUMNS)

    # Sample first.  Enriching 1–2 million full tick rows merely to discard
    # 15/16 of them is both slow and memory intensive.  ``build_match_tables``
    # normally passes ticks that already have reconstructed sides, so avoid an
    # unnecessary third full-table merge as well.
    tick_num = pd.to_numeric(ticks["tick"], errors="coerce")
    valid = tick_num.notna()
    step = max(int(cfg.position_sample), 1)
    sampled = ticks.loc[valid & ((tick_num.fillna(-1).astype("int64") % step) == 0)].copy()
    if sampled.empty:
        return _empty(POSITIONS_COLUMNS)

    side_missing = "side" not in sampled.columns or sampled["side"].isna().all()
    if side_missing:
        sampled = enrich_tick_sides(sampled, round_sides if round_sides is not None else pd.DataFrame())

    columns = [col for col in POSITIONS_COLUMNS if col in sampled.columns]
    return normalize_table(sampled[columns].copy()).reindex(columns=POSITIONS_COLUMNS)
