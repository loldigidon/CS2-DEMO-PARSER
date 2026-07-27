from __future__ import annotations

from pathlib import Path

import cs2parser.input as input_module
from cs2parser.input import (
    find_input_files,
    is_rar_path,
    is_supported_input_path,
    materialized_demo_collection,
)


def test_supported_inputs_include_rar_and_recursive_folders(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    demo = nested / "match.dem"
    archive = tmp_path / "series.RAR"
    ignored = tmp_path / "notes.txt"
    demo.write_bytes(b"demo")
    archive.write_bytes(b"rar")
    ignored.write_text("ignore", encoding="utf-8")

    assert is_rar_path(archive)
    assert is_supported_input_path(archive)
    assert find_input_files(tmp_path) == [demo, archive]


def test_materialized_rar_collection_lives_for_context_only(tmp_path, monkeypatch):
    archive = tmp_path / "series.rar"
    archive.write_bytes(b"not-a-real-rar")
    extracted_root: Path | None = None

    def fake_extract(_archive: Path, destination: Path) -> list[Path]:
        nonlocal extracted_root
        extracted_root = destination.parent
        demo = destination / "nested" / "map.dem"
        demo.parent.mkdir(parents=True)
        demo.write_bytes(b"demo")
        return [demo]

    monkeypatch.setattr(input_module, "_extract_rar", fake_extract)
    with materialized_demo_collection(archive) as demos:
        assert [path.name for path in demos] == ["map.dem"]
        assert demos[0].read_bytes() == b"demo"
        assert extracted_root is not None and extracted_root.exists()

    assert extracted_root is not None and not extracted_root.exists()


def test_rar_member_filter_rejects_path_traversal_and_non_demos():
    members = input_module._safe_demo_members([
        "maps/m1.dem",
        r"maps\m2.dem.zst",
        "../escape.dem",
        "/absolute.dem",
        "C:/drive.dem",
        "readme.txt",
    ])

    assert members == ["maps/m1.dem", r"maps\m2.dem.zst"]
