"""What one run produced.

The rows are already the report - one table, every depth of row in it (see
:mod:`organella.model.rows`). What travels with them is how the run went, which is
what the parquet's provenance footer is written from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import polars as pl


@dataclass
class Report:
    """What a run produced, and what it could not."""
    rows: pl.DataFrame
    failures: Dict[str, str]
    seconds: float
    per_object_seconds: Dict[str, float]

    @property
    def n_objects(self) -> int:
        return int((self.rows["obs_level"] == 0).sum()) if self.rows.height else 0
