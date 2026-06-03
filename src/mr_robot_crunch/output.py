"""Merge worker shard files into a single output file.

The default path deduplicates globally. Because each shard is independent, we
sort the shards *in parallel* and then do a cheap k-way merge (``sort -m -u``)
instead of re-sorting everything in one pass — much faster and lower peak
memory on large runs. Falls back to a pure-Python set merge if ``sort`` is
unavailable. ``dedup=False`` skips deduplication entirely (plain concatenation,
fastest), and ``gzip_out=True`` writes a gzip-compressed file.
"""

import gzip
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

_SORT_ENV = {"LC_ALL": "C"}  # byte-oriented: fast and deterministic


def _open_out(output: Path, gzip_out: bool):
    if gzip_out:
        return gzip.open(output, "wt", encoding="utf-8", errors="replace")
    return open(output, "w")


def merge_shards(
    shards: List[Path],
    output: Path,
    dedup: bool = True,
    gzip_out: bool = False,
) -> Tuple[int, str]:
    """Merge ``shards`` into ``output``.

    Returns ``(line_count, method)`` where ``method`` is one of
    ``"sort-merge"``, ``"in-memory dedup"`` or ``"concat"``.
    """
    existing = [s for s in shards if s.exists()]
    output.parent.mkdir(parents=True, exist_ok=True)

    if not dedup:
        return _concat(existing, output, gzip_out), "concat"

    if shutil.which("sort"):
        return _sort_merge(existing, output, gzip_out), "sort-merge"

    return _python_dedup(existing, output, gzip_out), "in-memory dedup"


def _concat(shards: List[Path], output: Path, gzip_out: bool) -> int:
    count = 0
    with _open_out(output, gzip_out) as out:
        for shard in shards:
            with open(shard, "r", errors="replace") as fh:
                for line in fh:
                    out.write(line if line.endswith("\n") else line + "\n")
                    count += 1
    return count


def _sort_merge(shards: List[Path], output: Path, gzip_out: bool) -> int:
    """Sort each shard concurrently, then k-way merge with ``sort -m -u``."""
    if not shards:
        with _open_out(output, gzip_out):
            pass
        return 0

    sorted_paths: List[Path] = []
    procs = []
    try:
        # Kick off one `sort -u` per shard in parallel (each writes a .sorted file).
        for shard in shards:
            sp = shard.with_suffix(shard.suffix + ".sorted")
            sorted_paths.append(sp)
            with open(sp, "w") as out:
                procs.append(
                    (subprocess.Popen(
                        ["sort", "-u", str(shard)], stdout=out, env=_SORT_ENV
                    ), sp)
                )
        for proc, sp in procs:
            if proc.wait() != 0:
                raise subprocess.CalledProcessError(proc.returncode, "sort")

        # Cheap merge of already-sorted, already-unique inputs.
        if gzip_out:
            merge = subprocess.Popen(
                ["sort", "-m", "-u", *[str(p) for p in sorted_paths]],
                stdout=subprocess.PIPE, env=_SORT_ENV,
            )
            count = 0
            with gzip.open(output, "wt", encoding="utf-8", errors="replace") as gz:
                for line in merge.stdout:
                    gz.write(line.decode("utf-8", "replace"))
                    count += 1
            if merge.wait() != 0:
                raise subprocess.CalledProcessError(merge.returncode, "sort -m")
            return count

        with open(output, "w") as out:
            subprocess.run(
                ["sort", "-m", "-u", *[str(p) for p in sorted_paths]],
                stdout=out, check=True, env=_SORT_ENV,
            )
        return _count_lines(output)
    finally:
        for sp in sorted_paths:
            try:
                sp.unlink()
            except OSError:
                pass


def _python_dedup(shards: List[Path], output: Path, gzip_out: bool) -> int:
    seen = set()
    with _open_out(output, gzip_out) as out:
        for shard in shards:
            with open(shard, "r", errors="replace") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if line and line not in seen:
                        seen.add(line)
                        out.write(line + "\n")
    return len(seen)


def _count_lines(path: Path) -> int:
    count = 0
    with open(path, "r", errors="replace") as fh:
        for _ in fh:
            count += 1
    return count
