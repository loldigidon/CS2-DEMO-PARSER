"""Console application for parsing demos and opening the local visualizer."""
from __future__ import annotations

import argparse
from collections import Counter
import os
import json
import subprocess
import sys
import traceback
from pathlib import Path
from dataclasses import asdict

from config import ParserConfig
from cs2parser import (
    __version__,
    RarExtractorNotFoundError,
    demo_match_id,
    materialized_demo,
    materialized_demo_collection,
    parse_demo,
    save_all,
    save_event_tables,
)
from cs2parser.process import build_match_tables
from cs2parser.validation import has_validation_errors
from cs2parser.visualization import (
    VisualizationError,
    build_dashboard,
    build_dashboard_hub,
    find_parsed_matches,
    serve_dashboard,
)


def process_one(path: Path, cfg: ParserConfig, out_dir: str, *, terminate_process: bool = False) -> bool:
    match_id = demo_match_id(path)
    print(f"[+] Парсинг {path.name} ...")
    try:
        with materialized_demo(path) as demo_path:
            dem = parse_demo(str(demo_path), cfg)
            tables, event_tables, effective_cfg = build_match_tables(dem, cfg)
        save_all(tables, out_dir, match_id)
        if effective_cfg.all_events:
            save_event_tables(event_tables, Path(out_dir) / match_id)
    except Exception as exc:
        print(f"[!] Не удалось распарсить {path.name}: {exc}")
        traceback.print_exc()
        if terminate_process:
            sys.stdout.flush(); sys.stderr.flush(); os._exit(1)
        return False

    validation = tables["validation"]
    errors = has_validation_errors(validation)
    failed = validation[validation["status"] == "fail"]
    if not failed.empty:
        for row in failed.itertuples(index=False):
            print(f"    [{row.severity}] {row.check}: {row.details}")

    if errors and not effective_cfg.allow_partial:
        print(f"[invalid] {match_id}: файлы сохранены для проверки, но core-валидация не пройдена")
        if terminate_process:
            sys.stdout.flush(); sys.stderr.flush(); os._exit(1)
        return False

    print(
        f"[ok] {match_id} -> {Path(out_dir) / match_id} "
        f"(tickrate={effective_cfg.tickrate}, events={int(tables['parse_metadata'].iloc[0]['parsed_event_count'])})"
    )
    if terminate_process:
        # Do not unwind this frame: dropping the multi-million-row pandas/Polars
        # objects can deadlock in native finalizers on some platforms.
        sys.stdout.flush(); sys.stderr.flush(); os._exit(0)
    return True


def _run_parse_worker(path: Path, cfg: ParserConfig, out_dir: str) -> bool:
    """Parse one demo in an isolated process and bypass native shutdown stalls."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(path),
        "--out", str(out_dir),
        "--_parse-worker-config", json.dumps(asdict(cfg), ensure_ascii=False),
    ]
    completed = subprocess.run(command, check=False)
    return completed.returncode == 0


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{text}{suffix}: ").strip()
    return value or (default or "")


def _pick_number(items: list[Path], title: str, allow_all: bool = False) -> list[Path]:
    print(f"\n{title}")
    for index, item in enumerate(items, 1):
        print(f"  {index}. {item.name}")
    if allow_all:
        print("  0. Все файлы")
    while True:
        raw = _prompt("Выберите номер", "0" if allow_all else "1")
        try:
            choice = int(raw)
        except ValueError:
            print("Введите номер из списка.")
            continue
        if allow_all and choice == 0:
            return items
        if 1 <= choice <= len(items):
            return [items[choice - 1]]
        print("Такого номера нет.")


def _interactive_request() -> tuple[str, str, str, str | None, bool]:
    print("\n=== CS2 Demo Parser + Local Visualizer ===")
    print("  1. Только парсер")
    print("  2. Парсинг + визуализация")
    print("  3. Визуализировать уже распарсенные данные")
    choices = {"1": "parse", "2": "parse-viz", "3": "visualize"}
    mode = ""
    while mode not in choices:
        mode = _prompt("Режим", "2")
    selected_mode = choices[mode]

    if selected_mode == "visualize":
        path = _prompt("Папка output или папка конкретного матча", "output")
        out = "output"
    else:
        path = _prompt("Путь к .rar/.dem/.dem.zst или папке с архивами и демками")
        out = _prompt("Папка для Parquet", "output")
    radar = _prompt("Папка с радарами (Enter = встроенные)", "") or None
    browser = _prompt("Открыть браузер? y/n", "y").lower() not in {"n", "no", "нет"}
    return selected_mode, path, out, radar, browser


def _select_demos(demos: list[Path], interactive: bool, mode: str) -> list[Path]:
    if interactive and len(demos) > 1:
        return _pick_number(demos, "Найденные демки", allow_all=(mode in {"parse", "parse-viz"}))
    return demos


def _resolve_parsed_matches(path_value: str, match_name: str | None, interactive: bool) -> list[Path]:
    matches = find_parsed_matches(path_value)
    if match_name:
        matches = [match for match in matches if match.name == match_name]
    if interactive and len(matches) > 1:
        return _pick_number(matches, "Распарсенные матчи")
    return matches


def _build_and_maybe_serve(
    matches: list[Path],
    dashboard_root: str | None,
    radar_dir: str | None,
    no_serve: bool,
    no_browser: bool,
    host: str,
    port: int,
) -> bool:
    dashboards: list[Path] = []
    for match in matches:
        destination = Path(dashboard_root).expanduser() / match.name if dashboard_root else None
        try:
            index = build_dashboard(match, destination=destination, radar_dir=radar_dir)
        except VisualizationError as exc:
            print(f"[!] Визуализация {match.name}: {exc}")
            continue
        dashboards.append(index)
        print(f"[viz] Готово: {index}")

    if not dashboards:
        return False
    entrypoint = dashboards[0]
    if len(dashboards) > 1:
        entrypoint = build_dashboard_hub(dashboards)
        print(f"[viz] Общая страница для {len(dashboards)} матчей: {entrypoint}")
    if not no_serve:
        serve_dashboard(entrypoint, host=host, port=port, open_browser=not no_browser)
    return True


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Локальный CS2 demo parser с FACEIT-style визуализацией (без API/CDN)",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help=".rar/.dem/.dem.zst, папка с архивами/демками или папка Parquet-матча",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--mode", choices=["parse", "parse-viz", "visualize"], default=None,
        help="parse: только Parquet; parse-viz: Parquet + dashboard; visualize: dashboard из готового Parquet",
    )
    parser.add_argument("--out", default="output", help="Папка Parquet output")
    parser.add_argument("--match", default=None, help="Имя матча при выборе из общей output-папки")
    parser.add_argument("--dashboard-dir", default=None, help="Отдельная корневая папка для dashboard")
    parser.add_argument("--radar-dir", default=None, help="Папка с *_radar.dds/png (по умолчанию встроенные)")
    parser.add_argument("--no-serve", action="store_true", help="Только создать dashboard, не запускать HTTP-сервер")
    parser.add_argument("--no-browser", action="store_true", help="Не открывать браузер автоматически")
    parser.add_argument("--host", default="127.0.0.1", help="Host локального visualizer-сервера")
    parser.add_argument("--port", type=int, default=0, help="Порт сервера; 0 = выбрать свободный")

    parser.add_argument("--tickrate", type=int, default=None, help="Принудительный tickrate; по умолчанию авто")
    parser.add_argument("--tick-sample", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--no-positions", action="store_true", help="Не сохранять позиции (radar playback будет недоступен)")
    parser.add_argument("--position-sample", type=int, default=16, help="Сохранять каждую N-ю позицию")
    parser.add_argument(
        "--position-props", default="standard", choices=["minimal", "standard"],
        help="Набор свойств позиции",
    )
    parser.add_argument("--core-events-only", action="store_true", help="Только core events Awpy")
    parser.add_argument(
        "--all-raw-events", action="store_true",
        help="Дополнительно перепарсить каждый raw event движка (медленно и требует много RAM)",
    )
    parser.add_argument("--skip-validation-stats", action="store_true")
    parser.add_argument("--skip-shots", action="store_true")
    parser.add_argument("--skip-footsteps", action="store_true")
    parser.add_argument("--allow-partial", action="store_true", help="Успешный exit code при ошибках валидации")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--_parse-worker-config", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args._parse_worker_config is not None:
        if not args.path:
            parser.error("worker requires a demo path")
        worker_cfg = ParserConfig(**json.loads(args._parse_worker_config))
        process_one(Path(args.path), worker_cfg, args.out, terminate_process=True)
        os._exit(1)  # process_one always exits in worker mode

    interactive = args.path is None and args.mode is None

    if interactive:
        mode, path_value, out_value, radar_value, browser = _interactive_request()
        args.mode = mode
        args.path = path_value
        args.out = out_value
        args.radar_dir = radar_value
        args.no_browser = not browser
    else:
        args.mode = args.mode or "parse"

    if not args.path:
        parser.error("укажите path или запустите main.py без аргументов для интерактивного меню")

    if args.mode == "visualize":
        matches = _resolve_parsed_matches(args.path, args.match, interactive)
        if not matches:
            print("[!] Не найдены папки распарсенных матчей с parse_metadata.parquet")
            return 1
        return 0 if _build_and_maybe_serve(
            matches, args.dashboard_dir, args.radar_dir, args.no_serve,
            args.no_browser, args.host, args.port,
        ) else 1

    cfg = ParserConfig(
        tickrate=args.tickrate,
        tick_sample=args.tick_sample,
        verbose=args.verbose,
        all_events=not args.core_events_only,
        exhaustive_events=args.all_raw_events,
        with_positions=not args.no_positions,
        position_sample=args.position_sample,
        position_props_mode=args.position_props,
        skip_validation_stats=args.skip_validation_stats,
        skip_shots=args.skip_shots,
        skip_footsteps=args.skip_footsteps,
        allow_partial=args.allow_partial,
    )

    successful: list[Path] = []
    try:
        with materialized_demo_collection(args.path) as discovered:
            demos = _select_demos(discovered, interactive, args.mode)
            if not demos:
                print("[!] Не найдены .rar, .dem, .dem.zst или .dem(1).zst файлы")
                return 1

            ids = [demo_match_id(path) for path in demos]
            duplicates = sorted(match_id for match_id, count in Counter(ids).items() if count > 1)
            if duplicates:
                print(
                    "[!] Найдены демки с одинаковыми именами результата: "
                    + ", ".join(duplicates)
                    + ". Переименуйте файлы, чтобы результаты не перезаписывались."
                )
                return 1

            for demo_path in demos:
                if _run_parse_worker(demo_path, cfg, args.out):
                    successful.append(Path(args.out).expanduser().resolve() / demo_match_id(demo_path))
    except (RarExtractorNotFoundError, RuntimeError) as exc:
        print(f"[!] Не удалось прочитать входные данные: {exc}")
        return 1

    print(f"\nГотово: {len(successful)}/{len(demos)} демо распарсено")

    if args.mode == "parse-viz" and successful:
        visualized = _build_and_maybe_serve(
            successful, args.dashboard_dir, args.radar_dir, args.no_serve,
            args.no_browser, args.host, args.port,
        )
        return 0 if len(successful) == len(demos) and visualized else 1
    return 0 if len(successful) == len(demos) else 1


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
