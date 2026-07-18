"""Deterministic helpers used only by pyELK's PEP 517 build."""

from __future__ import annotations

import gzip
import os
import tarfile
from pathlib import Path


def build_reproducible_sdist(
    base_name: str | os.PathLike[str],
    base_dir: str | os.PathLike[str],
    *,
    epoch: int,
    root_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Create a byte-reproducible ``.tar.gz`` source archive.

    Setuptools normalises most tar metadata but historically leaves the gzip
    header timestamp dependent on wall-clock time.  Release builds need the
    complete artifact, not just its expanded tree, to be reproducible.
    """

    if epoch < 0:
        raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer")
    root = Path.cwd() if root_dir is None else Path(root_dir)
    source = Path(base_dir)
    if not source.is_absolute():
        source = root / source
    if not source.is_dir():
        raise RuntimeError(f"sdist release tree does not exist: {source}")

    output = Path(f"{base_name}.tar.gz")
    if not output.is_absolute():
        output = Path.cwd() / output
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_root = Path(base_dir).name
    entries = (source, *sorted(source.rglob("*"), key=lambda path: path.relative_to(source).parts))

    def normalized(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = epoch
        info.pax_headers = {}
        if info.isdir():
            info.mode = 0o755
        elif info.isfile():
            info.mode = 0o755 if info.mode & 0o111 else 0o644
        return info

    with (
        output.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for entry in entries:
            relative = entry.relative_to(source)
            archive.add(
                entry,
                arcname=(Path(archive_root) / relative).as_posix(),
                recursive=False,
                filter=normalized,
            )
    return str(output)


__all__ = ["build_reproducible_sdist"]
