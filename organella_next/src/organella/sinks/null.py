from __future__ import annotations

import polars as pl

from organella.app.ports import RunInfo


class NullSink:
    """Takes the rows and keeps nothing, for a run made for its side files."""

    def write(self, rows: pl.DataFrame, run: RunInfo | None = None) -> None:
        pass
