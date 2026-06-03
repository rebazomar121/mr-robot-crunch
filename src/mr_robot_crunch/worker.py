"""Per-process worker.

Each worker expands its assigned base permutations through the mutation
pipeline and streams the results into its own shard file. It checks the shared
pause/stop events between bases so the parent can suspend or cleanly stop the
whole run, persisting a resume cursor as it goes.
"""

import time
from typing import Dict, List

from . import engine, partition, session

# How often (in bases processed) to flush the shard and persist the cursor.
_FLUSH_EVERY = 64


def run_worker(
    session_id: str,
    worker_id: int,
    num_workers: int,
    words: List[str],
    layers: Dict[str, bool],
    skip: int,
    pause_event,
    stop_event,
    counter,
) -> None:
    """Worker entry point (runs in a child process).

    ``counter`` is a shared multiprocessing.Value('L') of lines written, used
    by the parent only for a progress display.
    """
    shard = session.shard_path(session_id, worker_id)
    # Append so a resumed worker continues its existing shard.
    mode = "a" if skip > 0 else "w"

    local_written = 0
    completed = skip
    last_flush = time.monotonic()

    # Optional output policy filter (length / required character classes).
    keep = engine.make_predicate(layers)

    try:
        with open(shard, mode, buffering=1 << 20) as fh:
            for local_index, base in partition.worker_bases(
                words, worker_id, num_workers, layers, skip=skip
            ):
                # Respect stop/pause between bases.
                if stop_event.is_set():
                    break
                while pause_event.is_set():
                    if stop_event.is_set():
                        break
                    time.sleep(0.2)
                if stop_event.is_set():
                    break

                # Each unit's expansion is duplicate-free by construction, so we
                # stream straight to disk (O(1) memory). Cross-unit / cross-worker
                # duplicates are removed once, globally, by the final sort -u.
                for word in engine.expand_base(base, words, layers):
                    if keep is not None and not keep(word):
                        continue
                    fh.write(word)
                    fh.write("\n")
                    local_written += 1

                completed = local_index + 1

                if (completed % _FLUSH_EVERY) == 0:
                    fh.flush()
                    session.write_cursor(session_id, worker_id, completed)
                    with counter.get_lock():
                        counter.value += local_written
                    local_written = 0
                    last_flush = time.monotonic()

            fh.flush()
    finally:
        # Final cursor + counter update regardless of how we exited.
        session.write_cursor(session_id, worker_id, completed)
        if local_written:
            with counter.get_lock():
                counter.value += local_written
