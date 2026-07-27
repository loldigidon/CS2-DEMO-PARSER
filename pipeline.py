"""Batch pipeline: manifest -> temp -> verify -> parse -> cleanup."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import pandas as pd

from config import ParserConfig
from main import _run_parse_worker
from cs2parser import (
    __version__,
    demo_match_id,
    find_demo_files,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_TOURNAMENT_ROOT = ROOT


def load_manifest(manifest_path: Path) -> dict:
    import json

    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def pick_matches(manifest: dict, limit: int | None = None) -> list[dict]:
    demos = manifest.get("demos", {})
    items = list(demos.items())
    if limit is not None:
        items = items[:limit]
    return [
        {
            "demoId": demo_id,
            "matchId": value["matchId"],
            "corePath": value["corePath"],
            "sizeBytes": int(value["sizeBytes"]),
            "teams": value.get("teams") or value.get("canonicalTeams") or [],
            "maps": value.get("maps", []),
            "stage": value.get("stage"),
        }
        for demo_id, value in items
    ]


def fetch_demo_files(
    demos_root: Path,
    temp_dir: Path,
    core_path: str,
    expected_bytes: int,
) -> list[Path] | None:
    src_dir = demos_root / core_path
    if not src_dir.exists():
        print(f"    [!] Match folder not found: {src_dir}")
        return None

    dem_files = find_demo_files(src_dir)
    if not dem_files:
        print(f"    [!] No .dem or .dem.zst files in: {src_dir}")
        return None

    temp_dir.mkdir(parents=True, exist_ok=True)
    local_dems: list[Path] = []

    for src in dem_files:
        dst = temp_dir / src.name
        if dst.exists():
            dst.unlink()
        print(f"    [->] Copying {src.name} ({src.stat().st_size:,} bytes)...")
        shutil.copy2(src, dst)
        local_dems.append(dst)

    total = sum(path.stat().st_size for path in local_dems)
    if total != expected_bytes:
        print(
            f"    [!] SIZE MISMATCH: local {total:,} != manifest {expected_bytes:,}. Skipping."
        )
        for path in local_dems:
            try:
                path.unlink()
            except OSError:
                pass
        return None

    print(f"    [ok] Integrity OK: {total:,} bytes == manifest")
    return local_dems


def process_demo_file(
    path: Path,
    cfg: ParserConfig,
    output_dir: Path,
) -> bool:
    """Run one parser worker so native demoparser finalizers cannot stall a batch."""
    return _run_parse_worker(path, cfg, str(output_dir))


def already_parsed(demo_ids: list[str], output_dir: str | Path) -> bool:
    root = Path(output_dir)
    required = ("rounds.parquet", "ticks.parquet", "teams.parquet", "validation.parquet", "parse_metadata.parquet")
    for demo_id in demo_ids:
        match_dir = root / demo_id
        if not all((match_dir / name).exists() for name in required):
            return False
        try:
            rounds = pd.read_parquet(match_dir / "rounds.parquet", columns=["round_num"])
            validation = pd.read_parquet(match_dir / "validation.parquet")
        except Exception:
            return False
        nums = pd.to_numeric(rounds["round_num"], errors="coerce").dropna().astype(int)
        if nums.empty or nums.nunique() != len(nums):
            return False
        if sorted(nums.tolist()) != list(range(int(nums.min()), int(nums.max()) + 1)):
            return False
        errors = (validation["status"] == "fail") & (validation["severity"] == "error")
        if errors.any():
            return False
    return True


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the batch-pipeline CLI parser for reuse in tests and entry points."""
    parser = argparse.ArgumentParser(description="CS2 pipeline from manifest.json")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N matches")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip matches that already have valid Parquet output",
    )
    parser.add_argument("--tickrate", type=int, default=None, help="Force tickrate; default auto-detect")
    parser.add_argument(
        "--tick-sample",
        type=int,
        default=1,
        help="Legacy flag; ticks are no longer sampled before derived logic",
    )
    parser.add_argument(
        "--no-positions",
        action="store_true",
        help="Disable sampled spatial positions (enabled by default)",
    )
    parser.add_argument(
        "--position-sample",
        type=int,
        default=16,
        help="Keep every N-th tick in positions_sampled",
    )
    parser.add_argument(
        "--position-props",
        default="standard",
        choices=["minimal", "standard"],
        help="Spatial tick prop preset when positions are enabled",
    )
    parser.add_argument(
        "--skip-validation-stats",
        action="store_true",
        help="Skip demo-derived player_stats validation table",
    )
    parser.add_argument(
        "--skip-shots",
        action="store_true",
        help="Do not extract shots table",
    )
    parser.add_argument(
        "--skip-footsteps",
        action="store_true",
        help="Do not extract footsteps table",
    )
    parser.add_argument(
        "--tournament-root",
        default=os.environ.get("TOURNAMENT_ROOT") or str(DEFAULT_TOURNAMENT_ROOT),
        help="Path to the tournament root folder containing manifest.json and demos",
    )
    parser.add_argument(
        "--temp-dir",
        default=str(ROOT / "temp"),
        help="Path to temporary staging folder for demos",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "output"),
        help="Path to the output folder for Parquet tables",
    )
    parser.add_argument("--core-events-only", action="store_true", help="Skip non-core detected events")
    parser.add_argument(
        "--all-raw-events",
        action="store_true",
        help="Parse every raw engine event (slow and memory intensive)",
    )
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    cfg = ParserConfig(
        tickrate=args.tickrate,
        tick_sample=args.tick_sample,
        verbose=args.verbose,
        with_positions=not args.no_positions,
        all_events=not args.core_events_only,
        exhaustive_events=args.all_raw_events,
        position_sample=args.position_sample,
        position_props_mode=args.position_props,
        skip_validation_stats=args.skip_validation_stats,
        skip_shots=args.skip_shots,
        skip_footsteps=args.skip_footsteps,
        allow_partial=args.allow_partial,
    )

    tournament_root = Path(args.tournament_root)
    manifest_path = tournament_root / "index" / "manifest.json"
    demos_root = tournament_root
    temp_dir = Path(args.temp_dir)
    output_dir = Path(args.output_dir)

    if not manifest_path.exists():
        print(f"[!] Manifest not found at: {manifest_path}")
        print("Please check the path or set the TOURNAMENT_ROOT environment variable/argument.")
        return 1

    limit_txt = args.limit if args.limit is not None else "all"
    print(f"== CS2 Pipeline: {limit_txt} matches from manifest ==")
    print(f"   manifest:       {manifest_path}")
    print(f"   demos root:     {demos_root}")
    print(f"   temp:           {temp_dir}")
    print(f"   output:         {output_dir}")
    print("   format:         parquet")
    print(f"   skip-existing:  {args.skip_existing}")
    print(f"   with-positions: {cfg.with_positions}")
    print()

    manifest = load_manifest(manifest_path)
    matches = pick_matches(manifest, limit=args.limit)
    print(f"Selected matches: {len(matches)}\n")

    ok = skipped = failed = 0
    for index, match in enumerate(matches, 1):
        label = f"{match['matchId']} ({' vs '.join(match['teams']) or '??'})"
        map_list = ",".join(match["maps"]) or "?"
        stage = f" · {match['stage']}" if match["stage"] else ""
        print(f"[{index}/{len(matches)}] Match {label} [{map_list}]{stage}")

        src_dir = demos_root / match["corePath"]
        dem_names = [demo_match_id(path) for path in find_demo_files(src_dir)] if src_dir.exists() else []

        if args.skip_existing and dem_names and already_parsed(dem_names, output_dir):
            print(f"    [skip] Already in Parquet ({len(dem_names)} demos)\n")
            skipped += 1
            continue

        local_dems = fetch_demo_files(demos_root, temp_dir, match["corePath"], match["sizeBytes"])
        if not local_dems:
            failed += 1
            print()
            continue

        match_ok = 0
        for demo_path in local_dems:
            if process_demo_file(demo_path, cfg, output_dir):
                match_ok += 1
            try:
                demo_path.unlink()
                print(f"    [del] Removed {demo_path.name}")
            except OSError as exc:
                print(f"    [!] Failed to remove {demo_path.name}: {exc}")

        if match_ok:
            ok += 1
        else:
            failed += 1
        print()

    print(f"== Done: processed {ok}, skipped {skipped}, failed {failed} (of {len(matches)}) ==")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    # demoparser2/Polars can keep native worker pools alive after all files are
    # written.  A normal interpreter shutdown may then sit at 100% CPU for
    # minutes.  Flush user-visible output and terminate the CLI process without
    # running heavy native finalizers.
    exit_code = main()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(int(exit_code))
