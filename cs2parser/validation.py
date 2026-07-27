"""Output validation: a successful parse must mean usable core data."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .normalize import identifier_columns


VALIDATION_COLUMNS = ["check", "status", "severity", "details"]


def _row(check: str, passed: bool, details: str, *, severity: str = "error") -> dict[str, Any]:
    return {
        "check": check,
        "status": "pass" if passed else "fail",
        "severity": "info" if passed else severity,
        "details": details,
    }


def validate_tables(
    tables: dict[str, pd.DataFrame],
    *,
    with_positions: bool = False,
    all_events: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    rounds = tables.get("rounds", pd.DataFrame())
    round_ok = not rounds.empty and "round_num" in rounds.columns
    rows.append(_row("rounds_present", round_ok, f"rows={len(rounds)}"))
    if round_ok:
        nums = pd.to_numeric(rounds["round_num"], errors="coerce").dropna().astype(int)
        unique = nums.nunique() == len(nums)
        contiguous = sorted(nums.tolist()) == list(range(int(nums.min()), int(nums.max()) + 1))
        rows.append(_row("rounds_unique", unique, f"unique={nums.nunique()} rows={len(nums)}"))
        rows.append(_row("rounds_contiguous", contiguous, f"range={nums.min()}..{nums.max()}"))

    ticks = tables.get("ticks", pd.DataFrame())
    rows.append(_row("ticks_present", not ticks.empty, f"rows={len(ticks)}"))

    teams = tables.get("teams", pd.DataFrame())
    team_count = teams.get("team_clan_name", pd.Series(dtype="object")).dropna().nunique()
    player_count = teams.get("steamid", pd.Series(dtype="object")).dropna().nunique()
    rows.append(_row("two_teams", team_count >= 2, f"teams={team_count}"))
    rows.append(_row("players_present", player_count >= 2, f"players={player_count}"))

    float_id_columns: list[str] = []
    for name, df in tables.items():
        if not isinstance(df, pd.DataFrame):
            continue
        for col in identifier_columns(df):
            if pd.api.types.is_float_dtype(df[col].dtype):
                float_id_columns.append(f"{name}.{col}")
    rows.append(_row(
        "identifier_precision",
        not float_id_columns,
        "no float identifier columns" if not float_id_columns else ", ".join(float_id_columns),
    ))

    if with_positions:
        positions = tables.get("positions_sampled", pd.DataFrame())
        rows.append(_row("positions_present", not positions.empty, f"rows={len(positions)}"))
        if not positions.empty and "side" in positions.columns:
            missing = float(positions["side"].isna().mean())
            rows.append(_row(
                "position_sides",
                missing < 0.01,
                f"missing_ratio={missing:.4f}",
                severity="warning",
            ))

    if all_events:
        manifest = tables.get("event_manifest", pd.DataFrame())
        rows.append(_row("event_manifest_present", not manifest.empty, f"events={len(manifest)}"))
        if not manifest.empty and "status" in manifest.columns:
            failures = int((manifest["status"].astype(str).str.lower() == "error").sum())
            rows.append(_row(
                "all_events_parsed",
                failures == 0,
                f"failed_events={failures}",
                severity="warning",
            ))

    return pd.DataFrame(rows, columns=VALIDATION_COLUMNS)


def has_validation_errors(validation: pd.DataFrame) -> bool:
    if validation.empty:
        return True
    return bool(((validation["status"] == "fail") & (validation["severity"] == "error")).any())
