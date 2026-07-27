"""Run the release parser against one real demo and verify its artifacts."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cs2parser.input import demo_match_id, is_compressed_demo_path, is_demo_path  # noqa: E402


REQUIRED_TABLES = (
    "parse_metadata.parquet",
    "validation.parquet",
    "rounds.parquet",
    "teams.parquet",
    "ticks.parquet",
    "player_stats.parquet",
)


def _preflight_demo(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Demo not found: {path}")
    if not is_demo_path(path):
        raise ValueError(f"Unsupported demo name: {path.name}")
    if is_compressed_demo_path(path):
        zstd = shutil.which("zstd")
        if zstd:
            subprocess.run([zstd, "-t", str(path)], check=True)


def _validate_output(match_dir: Path, expect_dashboard: bool) -> dict[str, object]:
    missing = [name for name in REQUIRED_TABLES if not (match_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"Missing required output files: {', '.join(missing)}")

    metadata = pd.read_parquet(match_dir / "parse_metadata.parquet")
    validation = pd.read_parquet(match_dir / "validation.parquet")
    required_validation_columns = {"status", "severity"}
    if not required_validation_columns.issubset(validation.columns):
        missing_columns = sorted(required_validation_columns - set(validation.columns))
        raise RuntimeError(f"validation.parquet is missing columns: {', '.join(missing_columns)}")
    error_failures = validation[
        validation["status"].astype(str).str.lower().eq("fail")
        & validation["severity"].astype(str).str.lower().eq("error")
    ]
    if not error_failures.empty:
        details = error_failures[[c for c in ("check", "details") if c in error_failures.columns]]
        raise RuntimeError(f"Core validation failed:\n{details.to_string(index=False)}")

    if expect_dashboard:
        dashboard = match_dir / "visualization"
        missing_dashboard = [name for name in ("index.html", "standalone.html") if not (dashboard / name).is_file()]
        if missing_dashboard:
            raise RuntimeError(f"Missing dashboard files: {', '.join(missing_dashboard)}")

    row = metadata.iloc[0] if not metadata.empty else pd.Series(dtype="object")
    rounds = pd.read_parquet(match_dir / "rounds.parquet")
    players = pd.read_parquet(match_dir / "player_stats.parquet")
    return {
        "match_id": match_dir.name,
        "map": row.get("map_name", "unknown"),
        "tickrate": row.get("tickrate", "unknown"),
        "rounds": len(rounds),
        "player_rows": len(players),
        "dashboard": expect_dashboard,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("demo", type=Path, help="Path to .dem or .dem.zst")
    parser.add_argument("--out", type=Path, default=Path("release-smoke-output"))
    parser.add_argument("--parse-only", action="store_true", help="Skip dashboard generation")
    parser.add_argument("--no-positions", action="store_true")
    parser.add_argument("--position-sample", type=int, default=16)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    demo = args.demo.expanduser().resolve()
    output = args.out.expanduser().resolve()
    _preflight_demo(demo)

    mode = "parse" if args.parse_only else "parse-viz"
    command = [
        sys.executable,
        str(ROOT / "main.py"),
        str(demo),
        "--mode",
        mode,
        "--out",
        str(output),
        "--position-sample",
        str(max(args.position_sample, 1)),
        "--no-serve",
        "--no-browser",
    ]
    if args.no_positions:
        command.append("--no-positions")

    print("[smoke]", " ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode != 0:
        return completed.returncode

    summary = _validate_output(output / demo_match_id(demo), expect_dashboard=not args.parse_only)
    print("[smoke] PASS")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
