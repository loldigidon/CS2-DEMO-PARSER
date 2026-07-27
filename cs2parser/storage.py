"""Storage helpers for local Parquet-only persistence."""
from __future__ import annotations

from pathlib import Path

import pandas as pd



KNOWN_TABLES: tuple[str, ...] = (
    "rounds",
    "round_sides",
    "kills",
    "damages",
    "shots",
    "fire_bullets",
    "grenades",
    "smokes",
    "infernos",
    "bomb",
    "footsteps",
    "flashes",
    "reloads",
    "ticks",
    "positions_sampled",
    "teams",
    "buys",
    "opening_kills",
    "clutch_attempts",
    "clutches",
    "player_stats",
    "trades",
    "buy_outcomes",
    "event_manifest",
    "parse_metadata",
    "validation",
    "header",
)

PARQUET_COMPRESSION = "zstd"


def parquet_table_path(root: str | Path, match_id: str, table_name: str) -> Path:
    return Path(root) / match_id / f"{table_name}.parquet"


def write_parquet_table(df: pd.DataFrame, path: str | Path) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False, compression=PARQUET_COMPRESSION)


def save_all(tables: dict[str, pd.DataFrame], out_dir: str, match_id: str) -> None:
    """Atomically replace the known Parquet layer for one match.

    Known stale tables not produced by the current run are removed. Temporary
    files are renamed only after successful writes, so interrupted writes do not
    replace previously valid files.
    """
    out = Path(out_dir) / match_id
    out.mkdir(parents=True, exist_ok=True)

    for name in KNOWN_TABLES:
        if name not in tables or tables[name] is None:
            stale = out / f"{name}.parquet"
            if stale.exists():
                stale.unlink()

    for name, df in tables.items():
        if not isinstance(df, pd.DataFrame):
            continue
        path = out / f"{name}.parquet"
        tmp = out / f".{name}.parquet.tmp"
        out_df = df.copy()
        if "match_id" not in out_df.columns:
            out_df.insert(0, "match_id", match_id)
        write_parquet_table(out_df, tmp)
        tmp.replace(path)
