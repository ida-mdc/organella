"""Measuring a batch of object folders, and assembling what comes back.

The steps, in the order :func:`analyse` takes them:

    how wide to run             pool.worker_count, from memory rather than cores
    what is already measured    parts.PartsCache, for --resume
    measure the rest            pool.measure_every_object -> objects.measure_object
    assemble                    table.one_table
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

from organella.analysis.parallel import mesh_worker_budget
from organella.model.report import Report
from organella.pipeline.objects import ObjectResult
from organella.pipeline.parts import PartsCache, settings_fingerprint
from organella.pipeline.pool import Work, measure_every_object, worker_count
from organella.pipeline.table import one_table

logger = logging.getLogger(__name__)


def analyse(
    folders: Sequence[Path],
    root: Path,
    paths: Sequence[str] = (),
    *,
    excluded: Sequence[str] = (),
    workers: Optional[int] = None,
    peak_gb: float = 4.0,
    parts_dir: Optional[Path] = None,
    resume: bool = False,
) -> Report:
    """Measure every object folder and return the rows plus whatever failed."""
    started = time.perf_counter()
    work = [(folder, group_of(folder, root, paths)) for folder in folders]
    n_workers = _plan_the_two_pools(workers, len(folders), peak_gb)

    parts = PartsCache(parts_dir, settings_fingerprint(excluded), resume=resume)
    reused, remaining = _split_off_what_is_already_measured(work, parts, len(work))
    progress = _progress(len(work), already_done=len(reused))

    def landed(result: ObjectResult) -> None:
        """Kept on disk first, then said out loud.

        If the run dies on the next object, the line the reader saw and the part that
        survives on disk say the same thing.
        """
        parts.keep(result)
        progress(result)

    results = measure_every_object(remaining, excluded, n_workers,
                                   on_result=landed) if remaining else []
    measured, failures = _rows_and_failures(results)

    return Report(
        rows=one_table([*reused, *measured]),
        failures=failures,
        seconds=time.perf_counter() - started,
        per_object_seconds={r.object_id: r.seconds for r in results},
    )


def group_of(folder: Path, root: Path, paths: Sequence[str]) -> str:
    """The import path an object belongs to: the group every row of it carries."""
    try:
        relative = folder.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    for path in paths:
        if relative == Path(path) or Path(path) in relative.parents:
            return path
    return relative.parts[0] if len(relative.parts) > 1 else ""


def _plan_the_two_pools(requested: Optional[int], n_objects: int, peak_gb: float) -> int:
    """How many objects at once, and how many mesh processes each of them gets.

    Two levels of parallelism, so they have to be decided together; the object pool is the
    one returned, and the mesh budget travels to the workers in the environment.
    """
    n_workers = worker_count(requested, n_objects, peak_gb)
    # This pool is sized by memory rather than cores, so on big objects most of the machine
    # would sit idle; each object gets the share left over. setdefault, so an explicit
    # --mesh-workers is left alone.
    share = max(1, (os.cpu_count() or 1) // n_workers)
    os.environ.setdefault("ORGANELLA_MESH_WORKERS", str(mesh_worker_budget(share)))
    # The same share for the distance transforms, which are the other thing in a run that
    # can use a whole machine: one per object at a time, threaded inside. Left at one core
    # they were most of a large object's wall time - the contact gaps alone walk every
    # instance - and given every core in every worker they would oversubscribe as soon as
    # the pool is wider than one.
    os.environ.setdefault("ORGANELLA_EDT_THREADS", str(share))
    logger.info("organella: %d object(s), %d worker(s), %s mesh process(es) and "
                "%s transform thread(s) each",
                n_objects, n_workers, os.environ["ORGANELLA_MESH_WORKERS"],
                os.environ["ORGANELLA_EDT_THREADS"])
    return n_workers


def _progress(total: int, already_done: int = 0):
    """Say each object as it lands, and how much of the batch that leaves.

    Called from inside the pool: logged afterwards, a batch that takes an hour says nothing
    for an hour and then everything at once, which looks exactly like one that has hung.
    Objects come back out of order, so the number is how many are done, not a position.
    """
    done = already_done

    def note(result: ObjectResult) -> None:
        nonlocal done
        done += 1
        if result.error:
            # The reason is already logged by whichever layer caught it, naming the same
            # object; this line only keeps the count honest.
            logger.warning("organella: [%d/%d] %s failed", done, total, result.object_id)
        else:
            logger.info("organella: [%d/%d] %s done in %.1f s",
                        done, total, result.object_id, result.seconds)

    return note


def _split_off_what_is_already_measured(
    work: Sequence[Work], parts: PartsCache, total: int,
) -> Tuple[List[pl.DataFrame], List[Work]]:
    """The rows an earlier run already produced, and the objects still to measure."""
    reused: List[pl.DataFrame] = []
    remaining: List[Work] = []
    for folder, group in work:
        rows = parts.rows_already_measured(folder)
        if rows is None:
            remaining.append((folder, group))
            continue
        # Counted into the same running total as the objects that are about to be measured:
        # --resume otherwise reads as a batch that skipped most of its work.
        logger.info("organella: [%d/%d] %s already measured; reusing its %d row(s)",
                    len(reused) + 1, total, folder.name, rows.height)
        reused.append(rows)
    return reused, remaining


def _rows_and_failures(
    results: Sequence[ObjectResult],
) -> Tuple[List[pl.DataFrame], Dict[str, str]]:
    """What the batch measured, and which objects it could not measure at all."""
    measured: List[pl.DataFrame] = []
    failures: Dict[str, str] = {}
    for result in results:
        if result.error:
            failures[result.object_id] = result.error
            continue
        measured.append(result.frame())
    return measured, failures
