<div align="center">

# CS2 Demo Parser

Локальный парсер демок Counter-Strike 2 с Parquet-экспортом и HTML-отчётом.

Python 3.11–3.13 · MIT · `.rar` · `.dem` · `.dem.zst` · папки с несколькими матчами

[Быстрый запуск](#быстрый-запуск-на-windows) · [CLI](#запуск-из-консоли) · [Результат](#что-создаёт-парсер) · [Разработка](#разработка)

</div>

<p align="center">
  <img src="https://raw.githubusercontent.com/loldigidon/CS2-DEMO-PARSER/main/docs/images/match-overview.png" alt="Итоги матча Team Spirit против 100T на Anubis" width="100%">
</p>

## Быстрый запуск на Windows

Нужны Python 3.11–3.13 и, только для RAR, [7-Zip](https://www.7-zip.org/) или WinRAR.

1. [Скачайте проект](https://github.com/loldigidon/CS2-DEMO-PARSER/archive/refs/heads/main.zip) и распакуйте ZIP.
2. Запустите [`START.bat`](START.bat).
3. Выберите RAR, демку или папку и нажмите **«Запустить всё»**.

При первом запуске `START.bat` создаст `.venv` и установит зависимости. Затем он
обработает все найденные демки и откроет отчёт. Файл или папку можно перетащить
прямо на `START.bat`.

## Что поддерживается

| Вход | Обработка |
|---|---|
| `.rar` | Из архива извлекаются только `.dem` и `.dem.zst`; временные файлы удаляются после обработки |
| `.dem` | Разбирается напрямую |
| `.dem.zst` | Автоматически распаковывается во временный файл |
| Папка | Рекурсивно находятся все поддерживаемые архивы и демки |

Для каждого матча создаются Parquet-таблицы и автономный dashboard. Если матчей
несколько, дополнительно создаётся общая страница со ссылками на все карты.

## Dashboard

В отчёте есть:

- итоговый счёт и статистика игроков;
- Rating, Swing, ADR, KAST, openings, trades и clutches;
- личные дуэли;
- оружие, гранаты и их результативность;
- экономика и закупы по раундам;
- покадровый радар с позициями, направлением взгляда, убийствами и utility.

### Радар раунда

<p align="center">
  <img src="https://raw.githubusercontent.com/loldigidon/CS2-DEMO-PARSER/main/docs/images/round-radar.png" alt="Покадровый радар раунда на Anubis" width="100%">
</p>

### Экономика

<p align="center">
  <img src="https://raw.githubusercontent.com/loldigidon/CS2-DEMO-PARSER/main/docs/images/economy.png" alt="Экономика команд по раундам" width="100%">
</p>

Скриншоты получены из реального BO3. Демки и сгенерированные данные в репозиторий
не добавлены.

## Запуск из консоли

Установка:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

RAR или отдельная демка:

```powershell
cs2-demo-parser series.rar --mode parse-viz --out output
cs2-demo-parser match.dem.zst --mode parse-viz --out output
```

Целая папка:

```powershell
cs2-demo-parser D:\demos --mode parse-viz --out output
```

Без автоматического открытия браузера:

```powershell
cs2-demo-parser series.rar --mode parse-viz --out output --no-serve
```

Если команда `cs2-demo-parser` ещё не установлена, используйте `python main.py`
с теми же аргументами.

### Основные параметры

| Параметр | Назначение |
|---|---|
| `--mode parse` | Только создать таблицы |
| `--mode parse-viz` | Создать таблицы и dashboard |
| `--mode visualize` | Пересобрать dashboard из готового результата |
| `--no-positions` | Не сохранять позиции игроков |
| `--position-sample N` | Сохранять каждую N-ю позицию |
| `--core-events-only` | Ограничиться стандартным набором событий Awpy |
| `--all-raw-events` | Извлечь все доступные engine events; медленно и требует больше RAM |
| `--allow-partial` | Не завершать процесс с ошибкой при замечаниях core-валидации |

Полный список:

```powershell
cs2-demo-parser --help
```

## Что создаёт парсер

```text
output/
├── index.html                    # список матчей, если их несколько
└── <match-id>/
    ├── header.parquet
    ├── parse_metadata.parquet
    ├── validation.parquet
    ├── rounds.parquet
    ├── teams.parquet
    ├── positions_sampled.parquet
    ├── kills.parquet
    ├── damages.parquet
    ├── grenades.parquet
    ├── bomb.parquet
    ├── player_stats.parquet
    ├── events/
    │   ├── manifest.json
    │   └── <event>.parquet
    └── visualization/
        ├── index.html
        └── standalone.html
```

Дополнительные таблицы содержат shots, smokes, infernos, flashes, reloads,
footsteps, buys, trades, opening kills, buy outcomes и clutches.

`validation.parquet` содержит результаты обязательных проверок. Матч получает
статус `[ok]` только после прохождения core-валидации.

SteamID и XUID сохраняются строками, чтобы не терять точность.

## Batch pipeline

Для турнирной структуры с `manifest.json`:

```powershell
cs2-demo-pipeline `
  --tournament-root D:\tournament `
  --output-dir output `
  --skip-existing
```

Подробные параметры:

```powershell
cs2-demo-pipeline --help
```

## Приватность

Демки, таблицы и отчёты обрабатываются локально. Сервер dashboard слушает только
`127.0.0.1`. Проект не отправляет данные матча во внешние API.

Готовый результат может содержать SteamID, никнеймы и позиции игроков — не
публикуйте папку `output` без проверки.

## Ограничения

- Состав событий зависит от версии CS2 и полноты конкретной демки.
- GOTV-демки обычно содержат больше данных, чем POV-демки.
- `--all-raw-events` выполняет дополнительный полный проход и может использовать много памяти.
- Rating и Swing — локальные расчётные метрики, а не официальная реализация FACEIT.

## Разработка

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m build
python -m twine check dist/*
```

CI проверяет Python 3.11, 3.12 и 3.13, запускает тесты, CLI smoke test и сборку
wheel/sdist.

См. также [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) и
[CHANGELOG.md](CHANGELOG.md).

## Лицензия

[MIT](LICENSE). Проект использует [Awpy](https://github.com/pnxenopoulos/awpy).
Counter-Strike и оригинальные игровые ассеты принадлежат Valve Corporation.
