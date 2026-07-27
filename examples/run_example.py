"""Пример локального использования как библиотеки.

Запуск: python examples/run_example.py path/to/match.dem[.zst] [output_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ParserConfig
from cs2parser import (
    build_match_tables,
    demo_match_id,
    materialized_demo,
    parse_demo,
    save_all,
    save_event_tables,
)
from cs2parser.rounds import round_summary


def main(source_path: str, output_dir: str = "output") -> None:
    source = Path(source_path)
    cfg = ParserConfig(with_positions=True, all_events=True, verbose=True)
    with materialized_demo(source) as demo_path:
        dem = parse_demo(str(demo_path), cfg)
        tables, event_tables, effective_cfg = build_match_tables(dem, cfg)

    match_id = demo_match_id(source)
    save_all(tables, output_dir, match_id)
    save_event_tables(event_tables, Path(output_dir) / match_id)

    print(f"\nTickrate: {effective_cfg.tickrate}")
    print(f"Events: {len(event_tables)}")
    print("\n=== КОМАНДЫ ===")
    print(tables["teams"])
    print("\n=== РАУНДЫ ===")
    print(tables["rounds"])
    print("\n=== СТАТИСТИКА ИГРОКОВ ===")
    print(tables["player_stats"])
    print("\n=== ВАЛИДАЦИЯ ===")
    print(tables["validation"])
    print("\n=== СВОДКА ПО РАУНДУ 1 ===")
    print(round_summary(tables, int(tables["rounds"]["round_num"].min())))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Укажи путь: python examples/run_example.py match.dem[.zst] [output_dir]")
        raise SystemExit(1)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "output")
