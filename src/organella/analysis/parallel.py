"""A process pool for the per-instance work inside one object.

Meshing and contact gaps have the same shape: thousands of independent pieces of work on
small crops of one object. The batch pool does not cover it, being sized by what an object
costs in memory rather than by cores, so on large objects most of the machine sits idle
while one instance at a time is measured.

Both share the same two hazards, which is why this is one class and not two:

- **spawn, not fork.** The process holds native threads (ITK, BLAS, polars) and a forked
  child can deadlock on their locks.
- **a pool is not always worth opening.** Spawn costs about a second and every task pays
  pickling both ways, and whether that is repaid depends on the total work, which no count
  of instances predicts - so WorkPool measures one batch instead of guessing.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from itertools import islice
from typing import Callable, Iterator, List, Optional, Sequence, TypeVar

logger = logging.getLogger(__name__)

# Tasks timed to decide whether a pool is worth it. Small, because this runs here
# rather than in the pool: it is the price of finding out.
_SAMPLE = 24

# Per-worker allowance. Meshing one instance holds an EDT, a smoothed copy and the
# marching-cubes output over its padded bbox: at 0.024x0.016x0.016 um a 1-Mvoxel bbox peaks
# near 75 MB and a 7-Mvoxel one near 473 MB.
_WORKER_GB = 0.5

# More than this stopped helping anyway - 22 processes measured slower than 8 on a real
# object - so capping costs no speed and bounds what the pool can hold at once.
_WORKER_CAP = 8

# Of the memory still free, the share the pool may plan to use. The parent is holding the
# whole object and its distance transform, and wants the rest.
_MEMORY_SHARE = 0.25

T = TypeVar("T")
R = TypeVar("R")


def batched(items: Sequence[T], size: int) -> Iterator[List[T]]:
    """`items` in chunks of `size`. itertools.batched is 3.12+, and this supports 3.11."""
    it = iter(items)
    while chunk := list(islice(it, size)):
        yield chunk


def mesh_worker_budget(cores: int) -> int:
    """How many mesh processes are safe, given the cores offered and the memory free.

    Cores alone nearly took a machine down: one object worker on a 22-core box was handed
    22 mesh processes, each able to hold half a gigabyte, while the parent held a 26 GB
    object. Bounded by free memory and by where more processes stopped helping.
    """
    share = max(1, cores)
    try:
        import psutil

        free_gb = psutil.virtual_memory().available / 1024**3
        share = min(share, max(1, int(free_gb * _MEMORY_SHARE / _WORKER_GB)))
    except Exception:  # noqa: BLE001 - no psutil is a reason to be careful, not to stop
        # No memory reading, so the cap below is the whole budget - which is what being
        # careful comes down to here, and is applied either way.
        pass
    return max(1, min(share, _WORKER_CAP))


def worker_share(requested: int = 0) -> int:
    """How many processes one object may use for its own per-instance work.

    An explicit --mesh-workers is taken at its word. Otherwise the batch has already worked
    out a bounded share and passed it down in ORGANELLA_MESH_WORKERS; running one object on
    its own (the `mesh` command) works one out here.
    """
    if requested:
        return max(1, int(requested))
    raw = os.environ.get("ORGANELLA_MESH_WORKERS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning("organella: ORGANELLA_MESH_WORKERS=%r is not a number; ignoring", raw)
    return mesh_worker_budget(os.cpu_count() or 1)


class WorkPool:
    """Independent per-instance work, farmed out only once it is measurably worth it.

    Whether a pool pays depends on the total work, which no count of instances predicts:
    400 large instances gained 1.7x, while 600 small ones lost 1.45x to pickling and IPC. So
    it runs the first batch here, times it, and extrapolates over `total`:

        projected = elapsed / len(batch) * total

    Below `min_seconds` it never opens one, and a wrong guess costs the one batch it
    measured.

    `what` names the work in the warning a broken pool logs. It falls back rather than
    raising: serial is slow, not wrong, where losing an object's geometry to a dead pool is
    the worse outcome.
    """

    def __init__(self, workers: int, total: int = 0, what: str = "work",
                 min_seconds: float = 4.0) -> None:
        self._workers = workers
        self._total = total
        self._what = what
        self._min_seconds = min_seconds
        self._pool: Optional[ProcessPoolExecutor] = None
        self._decided = workers <= 1     # one worker: nothing to decide

    def map(self, fn: Callable[[T], R], tasks: Sequence[T],
            total: Optional[int] = None) -> List[R]:
        """`total` is how many tasks of this kind are coming, for the projection."""
        if not tasks:
            return []
        if self._pool is None and not self._decided:
            return self._measure(fn, tasks, total)
        if self._pool is None:
            return [fn(task) for task in tasks]
        try:
            # A run of tasks per worker, not one round trip each: at one per trip the IPC
            # on thousands of small tasks is the whole cost.
            chunk = max(1, len(tasks) // (self._workers * 4))
            return list(self._open().map(fn, tasks, chunksize=chunk))
        except Exception as exc:  # noqa: BLE001 - a broken pool must not cost the result
            logger.warning("organella: parallel %s failed (%s); one at a time",
                           self._what, type(exc).__name__)
            self._workers, self._pool = 1, None
            return [fn(task) for task in tasks]

    def _measure(self, fn: Callable[[T], R], tasks: Sequence[T],
                 total: Optional[int]) -> List[R]:
        """Time a short sample here, then decide whether the rest is worth a pool.

        A sample, not the whole batch: it is serial work that could have been shared, so
        timing 256 instances to decide about 400 spends the gain finding out it was there.
        """
        sample = tasks[:_SAMPLE]
        started = time.perf_counter()
        results = list(map(fn, sample))
        elapsed = time.perf_counter() - started
        expected = max(total or self._total, len(tasks))
        projected = elapsed / len(sample) * expected
        self._decided = True
        if projected < self._min_seconds:
            self._workers = 1
            logger.debug("organella: %s projected at %.1f s over %d; not worth a pool",
                         self._what, projected, expected)
        else:
            logger.info("organella: %s projected at %.0f s over %d; using %d processes",
                        self._what, projected, expected, self._workers)
            self._open()
        rest = tasks[len(sample):]
        if rest:
            results.extend(self.map(fn, rest, total))
        return results

    def _open(self) -> ProcessPoolExecutor:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=self._workers,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return self._pool

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown()
