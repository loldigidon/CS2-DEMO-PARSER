"""Extraction and persistence helpers for every event detected in a demo."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .normalize import normalize_table


EVENT_MANIFEST_COLUMNS = ["event_name", "rows", "columns", "status", "error"]


def _to_pandas(obj: Any) -> pd.DataFrame:
    if obj is None:
        return pd.DataFrame()
    if isinstance(obj, pd.DataFrame):
        return normalize_table(obj)
    fn = getattr(obj, "to_pandas", None)
    if callable(fn):
        try:
            return normalize_table(fn(use_pyarrow_extension_array=True))
        except TypeError:
            return normalize_table(fn())
    return normalize_table(pd.DataFrame(obj))


def assign_round_num(df: pd.DataFrame, rounds: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "tick" not in df.columns or rounds.empty:
        return df
    required = {"round_num", "start"}
    if not required.issubset(rounds.columns):
        return df

    ordered = rounds.sort_values("start").copy()
    starts = pd.to_numeric(ordered["start"], errors="coerce").dropna().astype("int64").to_numpy()
    if len(starts) == 0:
        return df
    round_nums = ordered.loc[pd.to_numeric(ordered["start"], errors="coerce").notna(), "round_num"].astype("int64").to_numpy()
    end_col = "official_end" if "official_end" in ordered.columns else "end"
    ends = pd.to_numeric(
        ordered.loc[pd.to_numeric(ordered["start"], errors="coerce").notna(), end_col],
        errors="coerce",
    ).fillna(np.iinfo(np.int64).max).astype("int64").to_numpy()

    out = df.copy()
    ticks = pd.to_numeric(out["tick"], errors="coerce")
    idx = np.searchsorted(starts, ticks.fillna(-1).astype("int64").to_numpy(), side="right") - 1
    values = pd.Series(pd.NA, index=out.index, dtype="Int64")
    valid = (idx >= 0) & ticks.notna().to_numpy()
    valid_positions = np.flatnonzero(valid)
    if len(valid_positions):
        candidate_idx = idx[valid]
        candidate_ticks = ticks.iloc[valid_positions].astype("int64").to_numpy()
        within = candidate_ticks <= ends[candidate_idx]
        good_positions = valid_positions[within]
        values.iloc[good_positions] = round_nums[candidate_idx[within]]
    out["round_num"] = values
    return out


def extract_event_tables(
    dem,
    rounds: pd.DataFrame,
    *,
    exhaustive: bool = False,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Return event tables without an unnecessary second full demo pass.

    ``parse_demo`` has already parsed Awpy's rich event set.  Those tables are
    sufficient for every derived metric and dashboard panel.  Re-reading every
    engine event can take minutes and several gigabytes on long demos, so it is
    opt-in through ``exhaustive=True``.
    """
    event_tables: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    detected = sorted(set(getattr(dem, "detected_events", []) or []))

    # Rich event tables parsed during the main pass are the canonical source.
    raw_events = dict(getattr(dem, "events", {}) or {})

    # These three sparse events power the support/flash and shooting panels but
    # are not part of Awpy's default event set on every version.  Parsing only
    # them is cheap and avoids the pathological all-engine-event pass.
    focused_names = [
        name for name in ("player_blind", "weapon_reload", "fire_bullets")
        if name in detected
    ]
    if focused_names:
        try:
            focused = dem.parser.parse_events(
                focused_names,
                player=["X", "Y", "Z", "team_name", "team_clan_name", "last_place_name"],
            )
            raw_events.update(dict(focused))
        except Exception as exc:  # pragma: no cover - parser-version dependent
            errors["__focused_events__"] = f"{type(exc).__name__}: {exc}"

    if exhaustive and detected:
        try:
            raw_items = dem.parser.parse_events(detected)
            # Keep the richer main-pass versions when both are available.
            raw_events = {**dict(raw_items), **raw_events}
        except Exception as exc:  # pragma: no cover - rare parser-level failure
            errors["__all_events__"] = f"{type(exc).__name__}: {exc}"
            for name in detected:
                if name in raw_events:
                    continue
                try:
                    raw_events[name] = dem.parser.parse_event(name)
                except Exception as item_exc:
                    errors[name] = f"{type(item_exc).__name__}: {item_exc}"

    manifest: list[dict[str, Any]] = []
    event_names = sorted(raw_events)
    for name in event_names:
        try:
            df = assign_round_num(_to_pandas(raw_events.get(name)), rounds)
            event_tables[name] = df
            manifest.append({
                "event_name": name,
                "rows": len(df),
                "columns": len(df.columns),
                "status": "ok" if name not in errors else "error",
                "error": errors.get(name),
            })
        except Exception as exc:
            event_tables[name] = pd.DataFrame()
            manifest.append({
                "event_name": name,
                "rows": 0,
                "columns": 0,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })

    for synthetic_name, error in errors.items():
        if synthetic_name.startswith("__"):
            manifest.append({
                "event_name": synthetic_name,
                "rows": 0,
                "columns": 0,
                "status": "error",
                "error": error,
            })

    # Record engine events that were intentionally not reparsed.  This makes
    # the manifest explicit instead of silently pretending they do not exist.
    if not exhaustive:
        for name in sorted(set(detected) - set(raw_events)):
            manifest.append({
                "event_name": name,
                "rows": 0,
                "columns": 0,
                "status": "not_requested",
                "error": None,
            })

    return event_tables, pd.DataFrame(manifest, columns=EVENT_MANIFEST_COLUMNS)


def save_event_tables(event_tables: dict[str, pd.DataFrame], match_dir: str | Path) -> None:
    event_dir = Path(match_dir) / "events"
    if event_dir.exists():
        for path in event_dir.glob("*.parquet"):
            path.unlink()
    event_dir.mkdir(parents=True, exist_ok=True)

    for name, df in sorted(event_tables.items()):
        safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in name)
        df.to_parquet(event_dir / f"{safe_name}.parquet", index=False, compression="zstd")

    (event_dir / "manifest.json").write_text(
        json.dumps(
            {
                "events": {
                    name: {"rows": int(len(df)), "columns": list(df.columns)}
                    for name, df in sorted(event_tables.items())
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _active_round_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Keep match-round rows for convenient root tables; raw events stay complete."""
    if df.empty or "round_num" not in df.columns:
        return df.copy()
    return df[df["round_num"].notna()].copy().reset_index(drop=True)


def common_event_tables(event_tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Expose common in-match events at root while preserving raw events in `events/`."""
    out: dict[str, pd.DataFrame] = {
        "flashes": _active_round_rows(event_tables.get("player_blind", pd.DataFrame())),
        "reloads": _active_round_rows(event_tables.get("weapon_reload", pd.DataFrame())),
        "fire_bullets": _active_round_rows(event_tables.get("fire_bullets", pd.DataFrame())),
    }
    footsteps = _active_round_rows(event_tables.get("player_footstep", pd.DataFrame()))
    if not footsteps.empty:
        footsteps = footsteps.rename(columns={
            "user_steamid": "steamid",
            "user_name": "name",
            "user_team_clan_name": "team_clan_name",
            "user_team_name": "side",
            "user_X": "X",
            "user_Y": "Y",
            "user_Z": "Z",
            "user_last_place_name": "place",
        })
        footsteps = normalize_table(footsteps)
        out["footsteps"] = footsteps
    return out
