"""Measuring a batch in parallel, and surviving a worker that is killed.

How wide the pool is comes from memory, not cores (:func:`worker_count`): one object can
need several gigabytes, and running more at once than fit invites the OOM killer.

Which is why this is more than a ``pool.map``. SIGKILL gives a worker no chance to report
anything and breaks the pool for everyone, and ``map`` then raises and loses *every* result,
including objects that finished hours earlier. So results are taken as they complete
(:func:`measure_in_pool`) and whatever the broken pool never ran is retried narrower
(:func:`measure_every_object`), fewer resident at once usually being the fix.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from organella.config import RunConfig
from organella.pipeline.objects import ObjectResult, measure_object

logger = logging.getLogger(__name__)

# One object to measure: the folder it is, and the import path it belongs to.
Work = Tuple[Path, str]

# What to do with each object as it finishes, before anything else can go wrong.
OnResult = Optional[Callable[[ObjectResult], None]]


# The level the parent is logging at, left in the environment for the workers to pick up.
LOG_LEVEL_ENV = "ORGANELLA_LOG_LEVEL"


def log_in_this_worker() -> None:
    """Give a spawned worker the same log handler the parent set up.

    A spawned worker re-imports the package but never runs the CLI, so everything it wrote
    about the object it was reading went to a logger with no handler. The parent leaves its
    level in the environment and this reads it back, which is what makes a long run say what
    it is doing. Lines from several workers interleave, so each names its object.
    """
    level = os.environ.get(LOG_LEVEL_ENV)
    if not level:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger = logging.getLogger("organella")
    package_logger.handlers[:] = [handler]
    package_logger.setLevel(int(level))
    package_logger.propagate = False


def worker_count(requested: Optional[int], n_objects: int, peak_gb: float) -> int:
    """As many objects at once as memory allows, never more than there are objects."""
    if requested is not None:
        return max(1, min(requested, n_objects))
    try:
        import psutil
        available = psutil.virtual_memory().available / 1024**3 * 0.8
    except Exception:  # noqa: BLE001 - without a memory reading, one object at a time
        return 1
    fit = int(available // peak_gb) if peak_gb > 0 else n_objects
    return max(1, min(fit, n_objects, os.cpu_count() or 1))


def measure_every_object(work: Sequence[Work], excluded: Sequence[str], n_workers: int,
                         dispatch=None, on_result: OnResult = None,
                         config: Optional[RunConfig] = None) -> List[ObjectResult]:
    """Measure every object, retrying whatever a killed pool never got to.

    Each round measures what is left; if the pool broke, the objects it never ran go into the
    next round at half the concurrency. An object that still kills a pool of one is recorded
    as a failure, so the rest of the batch can finish without it.
    """
    dispatch = dispatch or measure_in_pool
    if _is_a_single_object(work, n_workers):
        return [_measured_here(work[0], excluded, on_result, config)]

    results: List[ObjectResult] = []
    while work:
        finished, unrun = dispatch(work, excluded, n_workers, on_result, config)
        results.extend(finished)
        if not unrun:
            break
        n_workers, work, given_up = _after_a_killed_worker(n_workers, unrun,
                                                           made_progress=bool(finished))
        results.extend(given_up)
    return results


def measure_in_pool(work: Sequence[Work], excluded: Sequence[str], n_workers: int,
                    on_result: OnResult = None,
                    config: Optional[RunConfig] = None) -> Tuple[List[ObjectResult], List[Work]]:
    """One round: what the pool measured, and what it never got to because it broke.

    Results are handled as they complete, which is what lets ``on_result`` put an object's
    rows on disk before anything else goes wrong. What the pool never ran comes back in
    submission order, so a retry meets the objects in the same sequence.
    """
    # spawn, not fork: the parent holds native threads (polars, DuckDB, BLAS) and a forked
    # child can deadlock on their locks. The settings travel with each task, so a spawned
    # worker inherits nothing it needs.
    context = multiprocessing.get_context("spawn")
    finished: List[ObjectResult] = []
    unrun: List[Tuple[int, Path, str]] = []
    with ProcessPoolExecutor(max_workers=n_workers, mp_context=context,
                             initializer=log_in_this_worker) as pool:
        submitted = {
            pool.submit(measure_object, folder, group, tuple(excluded), config):
                (i, folder, group)
            for i, (folder, group) in enumerate(work)
        }
        for future in as_completed(submitted):
            index, folder, group = submitted[future]
            try:
                result = future.result()
            except BrokenProcessPool:
                unrun.append((index, folder, group))
                continue
            except Exception as exc:  # noqa: BLE001 - one bad object must not stop the batch
                logger.error("organella: %s failed: %s: %s",
                             folder.name, type(exc).__name__, exc)
                result = ObjectResult(folder.name, error=f"{type(exc).__name__}: {exc}")
            finished.append(result)
            if on_result is not None:
                on_result(result)
    return finished, [(folder, group) for _index, folder, group in sorted(unrun)]


def _is_a_single_object(work: Sequence[Work], n_workers: int) -> bool:
    """One object and one worker needs no pool: measure it here and keep the traceback."""
    return len(work) == 1 and n_workers == 1


def _measured_here(one: Work, excluded: Sequence[str], on_result: OnResult,
                   config: Optional[RunConfig] = None) -> ObjectResult:
    folder, group = one
    result = measure_object(folder, group, excluded, config)
    if on_result is not None:
        on_result(result)
    return result


def _after_a_killed_worker(
    n_workers: int, unrun: Sequence[Work], made_progress: bool,
) -> Tuple[int, List[Work], List[ObjectResult]]:
    """The next round: how wide, what is left in it, and any object given up on.

    Narrower first, since several large objects resident at once is the usual cause. A pool
    of one that still made progress keeps going. A pool of one that made none has met the
    object that kills it: that object is named and dropped, and the rest carry on.
    """
    if n_workers > 1:
        narrower = max(1, n_workers // 2)
        logger.warning(
            "organella: a worker was killed (out of memory?); %d object(s) did not run, "
            "retrying them with %d worker(s)", len(unrun), narrower)
        return narrower, list(unrun), []

    if made_progress:
        logger.warning("organella: a worker was killed; retrying the remaining %d object(s)",
                       len(unrun))
        return n_workers, list(unrun), []

    folder, _group = unrun[0]
    logger.error("organella: %s killed its worker (out of memory?); skipping it", folder.name)
    given_up = ObjectResult(folder.name, error="worker killed, most likely out of memory")
    return n_workers, list(unrun[1:]), [given_up]
