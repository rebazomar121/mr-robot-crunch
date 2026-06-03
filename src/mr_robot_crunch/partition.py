"""Partition work units across workers without overlap.

Work unit index ``i`` is assigned to worker ``i % num_workers``. Units are
disjoint and each unit's expansion is deterministic, so no two workers ever
perform the same work. ``skip`` lets a resumed worker fast-forward past the
units it already completed. Both layered and mask modes go through
``engine.iter_bases`` / ``engine.count_bases``.
"""

from typing import Dict, Iterator, List, Tuple

from . import engine


def worker_bases(
    words: List[str],
    worker_id: int,
    num_workers: int,
    cfg: Dict,
    skip: int = 0,
) -> Iterator[Tuple[int, object]]:
    """Yield ``(local_index, base)`` assigned to ``worker_id``.

    ``local_index`` counts the units handed to *this* worker (0-based) and is
    what gets persisted as the resume cursor.
    """
    local_index = 0
    for global_index, base in enumerate(engine.iter_bases(words, cfg)):
        if global_index % num_workers != worker_id:
            continue
        if local_index < skip:
            local_index += 1
            continue
        yield local_index, base
        local_index += 1


def worker_base_count(words: List[str], worker_id: int, num_workers: int, cfg: Dict) -> int:
    """How many units will be assigned to ``worker_id`` (for progress totals)."""
    total = engine.count_bases(words, cfg)
    if worker_id >= total:
        return 0
    return (total - worker_id + num_workers - 1) // num_workers
