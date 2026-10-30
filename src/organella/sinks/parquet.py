from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import polars as pl

import organella
from organella.app.ports import RunInfo
from organella.report.file import DESCRIPTION_KEY, FOOTER, NOUN_KEY, write_report

PRIVACY_SUMMARY = [
    "- object folder paths and the names of the volumes read",
    "- voxel size, extent and per-structure measurements",
    "- no pixel data: geometry is written beside the report, never into it",
]


@dataclass(frozen=True)
class ParquetSink:
    """The report as one parquet file: the rows, a description on every column, and a footer saying how it was made.

    The footer carries the descriptions as well as the fields do: a browser's parquet reader drops field metadata, so the
    page reads the footer copy to explain a metric under its chart.
    """

    path: Path
    noun: str | None = None          # what one measured thing is called, e.g. "cell" or "nucleus/nuclei"
    description: str = ""            # what the data is and who it credits
    colours: Mapping[str, str] = field(default_factory=dict)   # structure name -> #rrggbb
    paths: Sequence[str] = ()
    root: Path | None = None

    def write(self, rows: pl.DataFrame, run: RunInfo | None = None) -> None:
        rows = rows.sort("object_id", "obs_level", maintain_order=True).pipe(_without_null_columns).pipe(self._coloured)
        write_report(rows, self.path, self._footer(run))

    def _coloured(self, rows: pl.DataFrame) -> pl.DataFrame:
        if not self.colours or "entity_name" not in rows.columns:
            return rows
        return rows.with_columns(entity_colour=pl.col("entity_name").replace_strict(dict(self.colours), default=None))

    def _footer(self, run: RunInfo | None) -> dict[str, str]:
        footer = {
            FOOTER + "project_name": self.path.stem, DESCRIPTION_KEY: self.description, FOOTER + "version": organella.__version__,
            FOOTER + "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), FOOTER + "loader": "organella",
            FOOTER + "paths": json.dumps(list(self.paths)),
            FOOTER + "processing_stats": json.dumps({
                "wall_s": round(run.seconds, 3) if run else 0.0, "n_objects": run.n_objects if run else 0,
                "n_failed": len(run.failures) if run else 0,
                "seconds_per_object": {k: round(v, 1) for k, v in (run.per_object_seconds if run else {}).items()}}),
            FOOTER + "privacy_summary": json.dumps(PRIVACY_SUMMARY),
        }
        if self.root is not None:
            footer[FOOTER + "base_dir"] = str(self.root.resolve())
        if self.noun:
            footer[NOUN_KEY] = self.noun
        return footer


def _without_null_columns(rows: pl.DataFrame) -> pl.DataFrame:
    """A column that is empty everywhere has no type; the viewer's parquet reader needs one."""
    return rows.with_columns(pl.col(c).cast(pl.String) for c, t in rows.schema.items() if t == pl.Null)
