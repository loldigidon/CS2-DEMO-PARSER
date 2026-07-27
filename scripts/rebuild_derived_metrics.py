"""Rebuild local derived metrics from existing Parquet outputs.

This script performs no network calls and does not create AI/enrichment data.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import ParserConfig  # noqa: E402
from cs2parser import (  # noqa: E402
    buy_outcomes,
    clutch_attempts,
    clutches,
    opening_kills,
    trades,
    player_stats,
)
from cs2parser.storage import write_parquet_table  # noqa: E402


REQUIRED_SOURCE_TABLES = ("kills", "rounds", "ticks", "buys")
OPTIONAL_SOURCE_TABLES = ("damages", "shots", "bomb", "round_sides", "final_player_totals")
SOURCE_TABLES = REQUIRED_SOURCE_TABLES + OPTIONAL_SOURCE_TABLES
DERIVED_TABLES = (
    "kills",
    "trades",
    "opening_kills",
    "buy_outcomes",
    "clutch_attempts",
    "clutches",
    "player_stats",
)


def _load_parquet(path: Path) -> pd.DataFrame:
    # ``inventory`` is stored as an Arrow large-list column in new parses.
    # Keeping Arrow-backed dtypes avoids pandas trying to coerce nested values
    # into an unsupported NumPy extension array.
    return pd.read_parquet(path, dtype_backend="pyarrow")


def _stored_tickrate(match_dir: Path) -> int | None:
    path = match_dir / "parse_metadata.parquet"
    if not path.exists():
        return None
    try:
        metadata = pd.read_parquet(path, columns=["tickrate"])
        if metadata.empty:
            return None
        value = pd.to_numeric(metadata.iloc[0]["tickrate"], errors="coerce")
        return int(value) if pd.notna(value) and int(value) > 0 else None
    except Exception:
        return None


def effective_config(match_dir: Path, cfg: ParserConfig) -> ParserConfig:
    """Use an explicit tickrate or the exact rate stored by the original parse."""
    tickrate = cfg.tickrate or _stored_tickrate(match_dir)
    if tickrate is None:
        raise ValueError(
            "tickrate is missing: keep parse_metadata.parquet or pass --tickrate explicitly"
        )
    return replace(cfg, tickrate=int(tickrate))


def write_selected_to_parquet(
    tables: dict[str, pd.DataFrame],
    match_dir: Path,
    match_id: str,
    table_names: tuple[str, ...] = DERIVED_TABLES,
) -> None:
    """Atomically replace only selected derived tables inside one match directory."""
    match_dir.mkdir(parents=True, exist_ok=True)
    for name in table_names:
        df = tables.get(name)
        if df is None:
            continue
        out = df.copy()
        if "match_id" not in out.columns:
            out.insert(0, "match_id", match_id)
        path = match_dir / f"{name}.parquet"
        tmp = match_dir / f".{name}.parquet.tmp"
        write_parquet_table(out, tmp)
        tmp.replace(path)


def discover_matches(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        return []
    return [
        path
        for path in sorted(output_dir.iterdir())
        if path.is_dir() and all((path / f"{name}.parquet").exists() for name in REQUIRED_SOURCE_TABLES)
    ]


def rebuild_match(match_dir: Path, cfg: ParserConfig, *, write_parquet: bool) -> dict[str, pd.DataFrame]:
    local_cfg = effective_config(match_dir, cfg)
    tables = {
        name: _load_parquet(match_dir / f"{name}.parquet")
        if (match_dir / f"{name}.parquet").exists() else pd.DataFrame()
        for name in SOURCE_TABLES
    }
    tables["trades"] = trades(tables, local_cfg)
    tables["opening_kills"] = opening_kills(tables)
    tables["buy_outcomes"] = buy_outcomes(tables)
    tables["clutch_attempts"] = clutch_attempts(tables)
    tables["clutches"] = clutches(tables)
    tables["player_stats"] = player_stats(None, local_cfg, tables)

    if write_parquet:
        write_selected_to_parquet(tables, match_dir, match_dir.name)
    return {name: tables[name] for name in DERIVED_TABLES}


def summarize(match_id: str, tables: dict[str, pd.DataFrame]) -> str:
    kills = tables["kills"]
    trades_df = tables["trades"]
    attempts = tables["clutch_attempts"]
    clutches_df = tables["clutches"]
    is_trade = int(kills["is_trade"].fillna(False).sum()) if "is_trade" in kills.columns else 0
    attempts_won = (
        attempts["won"].value_counts(dropna=False).to_dict()
        if not attempts.empty and "won" in attempts.columns else {}
    )
    return (
        f"{match_id}: kills={len(kills):,}, is_trade={is_trade:,}, "
        f"trades={len(trades_df):,}, opening={len(tables['opening_kills']):,}, "
        f"buy_outcomes={len(tables['buy_outcomes']):,}, "
        f"clutch_attempts={len(attempts):,} {attempts_won}, "
        f"clutches={len(clutches_df):,}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild local derived metrics from Parquet")
    parser.add_argument("--output", default=str(ROOT / "output"), help="Folder with match directories")
    parser.add_argument("--match", action="append", default=None, help="Selected match_id; repeatable")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of matches")
    parser.add_argument(
        "--tickrate",
        type=int,
        default=None,
        help="Override tickrate; default reads each match's parse_metadata.parquet",
    )
    parser.add_argument("--trade-seconds", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true", help="Compute summaries without writing")
    args = parser.parse_args(argv)

    output_dir = Path(args.output)
    if not output_dir.exists():
        print(f"[!] output not found: {output_dir}", file=sys.stderr)
        return 1

    matches = discover_matches(output_dir)
    if args.match:
        wanted = set(args.match)
        matches = [path for path in matches if path.name in wanted]
        missing = sorted(wanted - {path.name for path in matches})
        for name in missing:
            print(f"[!] match not found or incomplete: {name}", file=sys.stderr)
    if args.limit is not None:
        matches = matches[:args.limit]
    if not matches:
        print("[!] No matches to process", file=sys.stderr)
        return 1

    cfg = ParserConfig(tickrate=args.tickrate, trade_seconds=args.trade_seconds)
    print(f"== Rebuild derived metrics: {len(matches)} match(es) ==")
    print(f"output:  {output_dir}")
    print(f"dry-run: {args.dry_run}")

    ok = failed = 0
    for index, match_dir in enumerate(matches, 1):
        match_id = match_dir.name
        print(f"[{index}/{len(matches)}] {match_id}")
        try:
            result = rebuild_match(match_dir, cfg, write_parquet=not args.dry_run)
            print("    " + summarize(match_id, result))
            ok += 1
        except Exception as exc:
            failed += 1
            print(f"    [!] Error: {exc}", file=sys.stderr)

    print(f"== Done: ok={ok}, failed={failed} ==")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
