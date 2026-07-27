"""Demo input helpers, including RAR discovery and temporary extraction."""
from __future__ import annotations

import locale
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Iterator


# Browsers and chat clients often rename duplicate downloads from ``match.dem.zst``
# to ``match.dem(1).zst``.  Treat both forms as the same supported input type.
_COMPRESSED_DEMO_RE = re.compile(r"(?i)^(?P<stem>.+)\.dem(?:\s*\(\d+\))?\.zst$")
_PLAIN_DEMO_RE = re.compile(r"(?i)^(?P<stem>.+)\.dem$")
_RAR_RE = re.compile(r"(?i)^.+\.rar$")


class RarExtractorNotFoundError(RuntimeError):
    """Raised when RAR input is requested without 7-Zip or UnRAR."""


def is_demo_path(path: str | Path) -> bool:
    name = Path(path).name
    return bool(_PLAIN_DEMO_RE.match(name) or _COMPRESSED_DEMO_RE.match(name))


def is_compressed_demo_path(path: str | Path) -> bool:
    return bool(_COMPRESSED_DEMO_RE.match(Path(path).name))


def is_rar_path(path: str | Path) -> bool:
    return bool(_RAR_RE.match(Path(path).name))


def is_supported_input_path(path: str | Path) -> bool:
    return is_demo_path(path) or is_rar_path(path)


def demo_match_id(path: str | Path) -> str:
    name = Path(path).name
    for pattern in (_COMPRESSED_DEMO_RE, _PLAIN_DEMO_RE):
        match = pattern.match(name)
        if match:
            return match.group("stem")
    return Path(path).stem


def find_demo_files(folder: str | Path) -> list[Path]:
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_file() and is_demo_path(path)),
        key=lambda path: path.name.lower(),
    )


def find_input_files(folder: str | Path) -> list[Path]:
    """Find demos and RAR archives recursively below *folder*."""
    root = Path(folder)
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and is_supported_input_path(path)
        ),
        key=lambda path: str(path.relative_to(root)).lower(),
    )


def _rar_tool() -> tuple[Path, str]:
    override = os.environ.get("CS2_RAR_TOOL")
    candidates: list[tuple[str | Path, str]] = []
    if override:
        override_path = Path(override).expanduser()
        kind = "unrar" if override_path.name.lower().startswith("unrar") else "7z"
        candidates.append((override_path, kind))

    candidates.extend((name, "7z") for name in ("7z", "7zz"))
    candidates.append(("unrar", "unrar"))

    if os.name == "nt":
        program_files = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            r"C:\Program Files",
            r"C:\Program Files (x86)",
        ]
        for root in dict.fromkeys(value for value in program_files if value):
            candidates.extend([
                (Path(root) / "7-Zip" / "7z.exe", "7z"),
                (Path(root) / "WinRAR" / "UnRAR.exe", "unrar"),
            ])

    for candidate, kind in candidates:
        path = shutil.which(str(candidate))
        if path:
            return Path(path).resolve(), kind
        candidate_path = Path(candidate)
        if candidate_path.is_file():
            return candidate_path.resolve(), kind
    raise RarExtractorNotFoundError(
        "Для чтения .rar установите 7-Zip или WinRAR/UnRAR. "
        "Также можно указать путь к программе через CS2_RAR_TOOL."
    )


def _run_archive_command(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, check=False)

    def decode(value: bytes) -> str:
        for encoding in ("utf-8", locale.getpreferredencoding(False), "cp866"):
            try:
                return value.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        return value.decode("utf-8", errors="replace")

    stdout = decode(completed.stdout)
    stderr = decode(completed.stderr)
    if completed.returncode != 0:
        details = (stderr or stdout).strip()
        raise RuntimeError(f"Ошибка распаковки RAR ({completed.returncode}): {details}")
    return stdout


def _safe_demo_members(member_names: list[str]) -> list[str]:
    """Keep demo members while rejecting absolute and parent-traversal paths."""
    safe: list[str] = []
    for raw_name in member_names:
        name = raw_name.strip().replace("\\", "/")
        if not name:
            continue
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts:
            continue
        if member.parts and ":" in member.parts[0]:
            continue
        if is_demo_path(member.name):
            safe.append(raw_name.strip())
    return list(dict.fromkeys(safe))


def _rar_members(archive: Path, tool: Path, kind: str) -> list[str]:
    if kind == "7z":
        output = _run_archive_command([
            str(tool), "l", "-ba", "-slt", "-sccUTF-8", "--", str(archive),
        ])
        names = re.findall(r"(?m)^Path = (.+)$", output)
    else:
        output = _run_archive_command([str(tool), "lb", str(archive)])
        names = output.splitlines()
    return _safe_demo_members(names)


def _extract_rar(archive: Path, destination: Path) -> list[Path]:
    tool, kind = _rar_tool()
    members = _rar_members(archive, tool, kind)
    if not members:
        return []

    destination.mkdir(parents=True, exist_ok=True)
    if kind == "7z":
        command = [
            str(tool), "x", "-y", "-aoa", "-sccUTF-8",
            f"-o{destination}", "--", str(archive), *members,
        ]
    else:
        command = [
            str(tool), "x", "-idq", "-o+", str(archive), *members,
            str(destination) + os.sep,
        ]
    _run_archive_command(command)
    return sorted(
        (path for path in destination.rglob("*") if path.is_file() and is_demo_path(path)),
        key=lambda path: str(path.relative_to(destination)).lower(),
    )


@contextmanager
def materialized_demo_collection(path: str | Path) -> Iterator[list[Path]]:
    """Yield every demo represented by one file, RAR, or recursive folder.

    RAR members live in a temporary directory for the whole parse batch and
    are removed automatically after all worker processes finish.
    """
    source = Path(path).expanduser()
    if source.is_file():
        inputs = [source] if is_supported_input_path(source) else []
    elif source.is_dir():
        inputs = find_input_files(source)
    else:
        inputs = []

    direct = [item.resolve() for item in inputs if is_demo_path(item)]
    archives = [item.resolve() for item in inputs if is_rar_path(item)]
    if not archives:
        yield direct
        return

    with tempfile.TemporaryDirectory(prefix="cs2-rar-") as temp_name:
        temp_root = Path(temp_name)
        extracted: list[Path] = []
        for index, archive in enumerate(archives, 1):
            archive_root = temp_root / f"{index:04d}-{archive.stem}"
            members = _extract_rar(archive, archive_root)
            print(f"[archive] {archive.name}: найдено демо — {len(members)}", flush=True)
            extracted.extend(members)

        combined = list(dict.fromkeys([*direct, *extracted]))
        combined.sort(key=lambda item: (item.name.lower(), str(item).lower()))
        yield combined


@contextmanager
def materialized_demo(path: str | Path) -> Iterator[Path]:
    """Yield a real ``.dem`` path and remove temporary decompression afterward."""
    source = Path(path)
    if _PLAIN_DEMO_RE.match(source.name):
        yield source
        return
    if not is_compressed_demo_path(source):
        raise ValueError(f"unsupported demo format: {source}")

    try:
        import zstandard as zstd
    except ImportError as exc:  # pragma: no cover - covered by requirements
        raise RuntimeError("zstandard is required to read compressed demo files") from exc

    temp_dir = Path(tempfile.mkdtemp(prefix="cs2-demo-"))
    target = temp_dir / f"{demo_match_id(source)}.dem"
    try:
        with source.open("rb") as compressed, target.open("wb") as output:
            reader = zstd.ZstdDecompressor().stream_reader(compressed)
            try:
                shutil.copyfileobj(reader, output, length=1024 * 1024)
            finally:
                reader.close()
        yield target
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
