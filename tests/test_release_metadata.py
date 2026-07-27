"""Release packaging and CLI regression tests."""
from __future__ import annotations

from pathlib import Path

import pytest

import main
import pipeline
from cs2parser import __version__
from cs2parser.visualization import BUNDLED_RADAR_ROOT, STATIC_ROOT


def test_version_is_exposed_consistently():
    assert __version__ == "0.1.0"


@pytest.mark.parametrize(
    ("builder", "program"),
    [
        (main.build_arg_parser, "cs2-demo-parser"),
        (pipeline.build_arg_parser, "cs2-demo-pipeline"),
    ],
)
def test_cli_version_flag(builder, program, capsys):
    parser = builder()
    parser.prog = program
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"{program} {__version__}"


def test_pipeline_exposes_all_raw_events_flag():
    args = pipeline.build_arg_parser().parse_args(["--all-raw-events"])
    assert args.all_raw_events is True


def test_packaged_dashboard_assets_exist():
    project_root = Path(__file__).resolve().parents[1]
    assert (project_root / "START.bat").is_file()
    assert (project_root / "launcher.py").is_file()
    assert (STATIC_ROOT / "index.html").is_file()
    assert (STATIC_ROOT / "styles.css").is_file()
    assert (STATIC_ROOT / "app.js").is_file()
    assert (BUNDLED_RADAR_ROOT / "de_anubis_radar.png").is_file()
    assert (BUNDLED_RADAR_ROOT / "de_mirage_radar.dds").is_file()
    assert (BUNDLED_RADAR_ROOT / "de_nuke_lower_radar.dds").is_file()
    assert Path(BUNDLED_RADAR_ROOT).parent.name == "cs2parser"
