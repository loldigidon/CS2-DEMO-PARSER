# Release checklist

## Code and tests

- [ ] Version updated in `cs2parser/_version.py` (package metadata reads it dynamically).
- [ ] `CHANGELOG.md` contains the release date and user-visible changes.
- [ ] `python -m compileall -q config.py main.py launcher.py pipeline.py cs2parser scripts tests` passes.
- [ ] `python -m pytest -q` passes on Python 3.11, 3.12, and 3.13.
- [ ] `python -m build` and `python -m twine check dist/*` pass.

## Real-demo validation

- [ ] `.dem.zst` integrity checked before parsing.
- [ ] `.rar` extraction tested with 7-Zip or UnRAR and temporary files are removed.
- [ ] `START.bat` completes first-run bootstrap and launches the GUI on Windows.
- [ ] `python scripts/release_smoke_test.py <demo> --out release-smoke-output` completes with exit code 0.
- [ ] `validation.parquet` has no error-severity failures.
- [ ] Dashboard builds with `--mode parse-viz --no-serve`.
- [ ] `index.html` opens locally and the browser console has no errors.
- [ ] Score, rounds, map, teams, economy, kills, trades, bomb plants, and radar playback are spot-checked.

## GitHub release

- [ ] CI is green on the release commit.
- [ ] Tag uses `vX.Y.Z` and matches the package version.
- [ ] Generated release notes are reviewed for private paths or demo data.
- [ ] Wheel and source distribution are attached to the GitHub Release.
