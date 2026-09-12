"""Each object's rows on disk as it finishes, so an interrupted run can be resumed.

Each object's rows go to ``<output>_parts/<object>.parquet`` the moment it is done, so a run
that dies late no longer loses the hours it had already measured, and ``--resume`` reads back
the ones it can trust.

Trust is the point of the module: a part is reused only when it is *readable*, has *rows*,
and records the *same settings* this run would measure under. One written under other
settings is worse than none - change the voxel size or ``--no-clip`` and the old rows answer
a different question, with nothing in the report to say so.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional, Sequence

import polars as pl

from organella.config import RunConfig
from organella.pipeline.objects import ObjectResult

logger = logging.getLogger(__name__)

# The settings a part was measured under, carried in the part itself so no bookkeeping file
# can go missing or disagree with it. Dropped again before the rows reach the report.
FINGERPRINT_COLUMN = "_organella_settings"


def settings_fingerprint(excluded: Sequence[str] = ()) -> str:
    """What the current settings would produce, as a digest a part can be checked against."""
    return RunConfig.from_env().fingerprint(tuple(sorted(excluded)))


def part_path(parts_dir: Path, object_id: str) -> Path:
    return parts_dir / f"{object_id}.parquet"


def discard_parts(parts_dir: Path) -> None:
    """Remove the parts once the report they were insurance for exists."""
    try:
        if parts_dir.is_dir():
            shutil.rmtree(parts_dir)
    except OSError as exc:
        logger.warning("organella: could not remove %s (%s)", parts_dir, exc)


def write_part(parts_dir: Path, result: ObjectResult, fingerprint: str) -> None:
    """Keep one object's rows on disk, or say why it could not be done.

    Written from the parent, so there is one writer and no object half-writes its own part
    while a worker is being killed. Failing to keep one costs a resume, not the report.
    """
    if result.error or not result.object_row:
        return
    try:
        parts_dir.mkdir(parents=True, exist_ok=True)
        stamped = result.frame().with_columns(pl.lit(fingerprint).alias(FINGERPRINT_COLUMN))
        writing = part_path(parts_dir, result.object_id).with_suffix(".parquet.writing")
        stamped.write_parquet(writing)
        # Renamed last: a reader never sees a partly written part, so resume cannot pick one
        # up mid-write.
        writing.replace(part_path(parts_dir, result.object_id))
    except Exception as exc:  # noqa: BLE001 - failing to cache must not fail the run
        logger.warning("organella: could not keep %s for resume (%s: %s)",
                       result.object_id, type(exc).__name__, exc)


def read_part(path: Path, fingerprint: str) -> Optional[pl.DataFrame]:
    """One object's rows back from disk, or None if the part cannot be trusted."""
    try:
        frame = pl.read_parquet(path)
    except Exception as exc:  # noqa: BLE001 - unreadable is a reason to measure again
        logger.warning("organella: %s is not readable (%s); measuring that object again",
                       path, type(exc).__name__)
        return None
    if frame.height == 0 or "obs_level" not in frame.columns:
        logger.warning("organella: %s holds nothing usable; measuring that object again", path)
        return None
    if FINGERPRINT_COLUMN not in frame.columns or frame[FINGERPRINT_COLUMN][0] != fingerprint:
        logger.info("organella: %s was measured under other settings; measuring it again", path)
        return None
    return frame.drop(FINGERPRINT_COLUMN)


class PartsCache:
    """Where a run keeps each object as it finishes, and what it may read back.

    One object per part, so the batch's two questions are per object too: has this one
    already been measured under these settings (:meth:`rows_already_measured`), and keep
    these rows (:meth:`keep`). With no directory it answers no and does nothing, so a batch
    needs no special case.
    """

    def __init__(self, directory: Optional[Path], fingerprint: str, *, resume: bool) -> None:
        self._directory = directory
        self._fingerprint = fingerprint
        self._resume = resume

    def rows_already_measured(self, folder: Path) -> Optional[pl.DataFrame]:
        """This object's rows from an earlier run, if they can be trusted; else None."""
        if not (self._resume and self._directory):
            return None
        path = part_path(self._directory, folder.name)
        return read_part(path, self._fingerprint) if path.is_file() else None

    def keep(self, result: ObjectResult) -> None:
        """Put one object's rows on disk, now that it has finished."""
        if self._directory is not None:
            write_part(self._directory, result, self._fingerprint)
