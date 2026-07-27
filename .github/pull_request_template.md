## What changed

<!-- Describe the user-visible change and why it is needed. -->

## Validation

- [ ] `python -m compileall -q config.py main.py pipeline.py cs2parser scripts tests`
- [ ] `python -m pytest -q`
- [ ] Tested against a real `.dem` or `.dem.zst` when parser behavior changed
- [ ] Dashboard checked without console errors when UI behavior changed
- [ ] README / CHANGELOG updated when behavior or CLI changed

## Demo privacy

- [ ] No private demo, player data, generated output, or local absolute path was committed
