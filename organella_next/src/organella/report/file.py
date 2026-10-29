"""A report is one parquet file: the rows, a description on every column, and a footer saying how it was made."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from organella.report.column_descriptions import descriptions_for

FOOTER = "organella_"
DESCRIPTIONS_KEY = FOOTER + "column_descriptions"
DESCRIPTION_KEY = FOOTER + "description"
NOUN_KEY = FOOTER + "object_noun"


def read_report(path: Path) -> tuple[pl.DataFrame, dict[str, str]]:
    """The rows and the footer a report carries."""
    if not path.is_file():
        raise FileNotFoundError(f"no report at {path}")
    raw = pq.read_metadata(path).metadata or {}
    footer = {k.decode(): v.decode() for k, v in raw.items() if not k.startswith(b"ARROW:")}
    return pl.read_parquet(path), footer


def write_report(rows: pl.DataFrame, path: Path, footer: dict[str, str]) -> None:
    """Rows, a description on every field that has one, and the footer, which holds the descriptions too: a browser's
    parquet reader drops field metadata, so the page reads the footer copy to explain a metric under its chart."""
    described = descriptions_for(rows.columns, footer.get(NOUN_KEY))
    table = rows.to_arrow()
    fields = [f.with_metadata({**(f.metadata or {}), b"description": described[f.name].encode()}) if f.name in described else f
              for f in table.schema]
    footer = {**footer, DESCRIPTIONS_KEY: json.dumps(described, sort_keys=True)}
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table.cast(pa.schema(fields, metadata={k: v.encode() for k, v in footer.items()})), path, compression="zstd")


def describe(path: Path, text: str) -> None:
    """Put ``text`` in an existing report's footer without measuring anything again: credits are what nobody has ready when
    a run starts and everybody wants once the report is being sent to someone."""
    rows, footer = read_report(path)
    write_report(rows, path, {**footer, DESCRIPTION_KEY: text})


def recolour(path: Path, colours: dict[str, str]) -> int:
    """Write ``colours`` into an existing report's entity rows; returns how many structures were coloured. Colouring is
    presentation and a run takes minutes, so a palette can be tried against a report that already exists."""
    rows, footer = read_report(path)
    if "entity_name" not in rows.columns:
        raise ValueError(f"{path} has no entity rows to colour")
    coloured = rows.with_columns(entity_colour=pl.col("entity_name").replace_strict(colours, default=None))
    write_report(coloured, path, footer)
    return int(coloured.filter(pl.col("row_type") == "entity")["entity_colour"].is_not_null().sum())
