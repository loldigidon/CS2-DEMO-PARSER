# Changelog

All notable user-facing changes are documented here.

## [0.1.0] - 2026-07-27

### Added

- Installable Python package metadata and console commands: `cs2-demo-parser` and `cs2-demo-pipeline`.
- One-click Windows launcher (`START.bat` + GUI) that bootstraps its environment and runs the complete workflow.
- Recursive `.rar`/demo folder input with automatic temporary extraction through 7-Zip or UnRAR.
- A batch dashboard hub that links every processed match instead of opening only the first one.
- GitHub Actions CI for Python 3.11–3.13, package builds, tagged GitHub releases, and Dependabot updates.
- GitHub issue forms, pull-request checklist, contribution guide, security policy, and release checklist.
- `--version` support for both command-line applications.

### Fixed

- Batch pipeline crash caused by the missing `--all-raw-events` argument.
- Personal absolute tournament path removed from the batch-pipeline default.
- Bundled radar assets moved into the Python package so installed wheels can generate dashboards.
- Anubis dashboards now use the bundled current CS2 overview and the official `pos_x=-2796`, `pos_y=3328`, `scale=5.22` transform instead of an unaligned auto-fit placeholder.
- Current `de_nuke2` demo headers resolve to the bundled `de_nuke` upper/lower radar assets.
- Runtime and development dependencies separated; direct NumPy and Polars requirements are now explicit where used.

### Included from the provided project snapshot

- Local `.dem` and `.dem.zst` parsing through Awpy.
- Parquet event, round, economy, player, clutch, trade, and position tables.
- FACEIT-style local dashboard with round radar playback and Nuke floor handling.
- Strict output validation and calibration reports for advanced metrics.
