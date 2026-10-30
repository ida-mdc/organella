from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import polars as pl

log = logging.getLogger(__name__)

# The settings a part was measured under, carried in the part itself so no bookkeeping file can go missing or disagree
# with it. Dropped again before the rows reach the report.
FINGERPRINT = "_organella_settings"


@dataclass(frozen=True)
class ParquetParts:
    """A part is reused only when it is readable, has rows, and records the same settings this run would measure under.
    One written under other settings is worse than none: change the voxel size or --no-clip and the old rows answer a
    different question, with nothing in the report to say so."""

    folder: Path
    reuse: bool = False   # kept as each sample finishes either way; read back only when resuming

    def load(self, sample_id: str, fingerprint: str) -> list[pl.DataFrame] | None:
        path = self._path(sample_id)
        if not self.reuse or not path.is_file():
            return None
        try:
            rows = pl.read_parquet(path)
        except Exception as error:  # a run killed mid-write leaves a file that does not open
            log.warning("%s is not readable (%s); measuring again", path, type(error).__name__)
            return None
        if not rows.height or FINGERPRINT not in rows.columns or rows[FINGERPRINT][0] != fingerprint:
            return None
        return [rows.drop(FINGERPRINT)]

    def save(self, sample_id: str, fingerprint: str, frames: list[pl.DataFrame]) -> None:
        """Failing to keep one costs a resume, not the report."""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            pl.concat(frames, how="diagonal_relaxed").with_columns(pl.lit(fingerprint).alias(FINGERPRINT)).write_parquet(self._path(sample_id))
        except Exception as error:
            log.warning("could not keep the rows of %s (%s)", sample_id, error)

    def discard(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def _path(self, sample_id: str) -> Path:
        return self.folder / f"{sample_id}.parquet"
