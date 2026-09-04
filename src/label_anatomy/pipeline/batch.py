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

from label_anatomy.analysis.parallel import mesh_worker_budget
from label_anatomy.model.report import Report
from label_anatomy.pipeline.objects import ObjectResult
from label_anatomy.pipeline.parts import PartsCache, settings_fingerprint
from label_anatomy.pipeline.pool import Work, measure_every_object, worker_count
from label_anatomy.pipeline.table import one_table

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
    reused, remaining = _split_off_what_is_already_measured(work, parts)
    results = measure_every_object(remaining, excluded, n_workers,
                                   on_result=parts.keep) if remaining else []
    measured, failures = _rows_and_failures(results)

    return Report(
        rows=one_table([*reused, *measured]),
        failures=failures,
        seconds=time.perf_counter() - started,
        per_object_seconds={r.object_id: r.seconds for r in results},
    )


def group_of(folder: Path, root: Path, paths: Sequence[str]) -> str:
    """The import path an object belongs to, which is the viewer's default grouping."""
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
    # Meshing farms an object's instances out to processes of its own, and this pool is
    # sized by memory rather than by cores, so on big objects most of the machine would
    # otherwise sit idle. Each object gets the share left over; an explicit --mesh-workers
    # already set it, and setdefault leaves that alone.
    os.environ.setdefault(
        "LABEL_ANATOMY_MESH_WORKERS",
        str(mesh_worker_budget(max(1, (os.cpu_count() or 1) // n_workers))))
    logger.info("anatomy: %d object(s), %d worker(s), %s mesh process(es) each",
                n_objects, n_workers, os.environ["LABEL_ANATOMY_MESH_WORKERS"])
    return n_workers


def _split_off_what_is_already_measured(
    work: Sequence[Work], parts: PartsCache,
) -> Tuple[List[pl.DataFrame], List[Work]]:
    """The rows an earlier run already produced, and the objects still to measure."""
    reused: List[pl.DataFrame] = []
    remaining: List[Work] = []
    for folder, group in work:
        rows = parts.rows_already_measured(folder)
        if rows is None:
            remaining.append((folder, group))
            continue
        logger.info("anatomy: %s already measured; reusing its %d row(s)",
                    folder.name, rows.height)
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
        logger.info("anatomy: %s done in %.1f s", result.object_id, result.seconds)
    return measured, failures
