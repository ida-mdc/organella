"""Rows into one table: the shape a report is written from.

Two jobs, in the order they happen. First a measurer's columnar tables become frames of
deep rows, one frame per row kind (:func:`deep_row_frames`). Then every frame of every
object - object rows, entity rows, deep rows - becomes the single table the report is
(:func:`one_table`).

Frames, not lists of dicts, because of the scale: a real object has ~8000 instances and five
times as many instance-target distances, so a batch of twenty is millions of rows. Columnar
they are a few hundred lists; as per-row dictionaries they would cost more to assemble than
they did to measure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
import polars as pl

from label_anatomy.model import DEEP_OBS_LEVEL, ROW_TYPE, ObjectMeasurement

# Identity first, then what kind of row it is and what it is about; everything else follows
# in the order the measurers declared it.
_LEADING_COLUMNS = ("obs_level", ROW_TYPE, "object_id", "imported_path_short",
                    "entity_name", "entity_kind", "spatial_dims")

# The polars type each declared column becomes. Declared rather than inferred, so a column
# no instance of one object could fill still arrives as the number it is: inferred, it would
# be a column of untyped nulls, and concatenating that with another object's real values is
# exactly the mismatch the old list columns used to hit.
_DTYPES: Dict[Any, pl.DataType] = {
    str: pl.Utf8,
    int: pl.Int64,
    float: pl.Float64,
    np.int64: pl.Int64,
    np.float64: pl.Float64,
}


def deep_row_frames(
    measurement: ObjectMeasurement,
    schemas: Mapping[str, Mapping[str, Any]],
    object_id: str,
) -> List[pl.DataFrame]:
    """One frame per kind of deep row a measurer produced.

    Each frame is the measurer's own columnar table plus the three columns a deep row needs
    to be found again: which object it belongs to, what kind of row it is, and how deep it
    sits.
    """
    frames = []
    for kind, table in measurement.tables.items():
        if not _height_of(table):
            continue
        frames.append(_typed(table, schemas[kind]).with_columns(
            pl.lit(DEEP_OBS_LEVEL, dtype=pl.Int64).alias("obs_level"),
            pl.lit(kind).alias(ROW_TYPE),
            pl.lit(object_id).alias("object_id"),
        ))
    return frames


def rows_as_frame(rows: Sequence[Mapping[str, Any]]) -> pl.DataFrame:
    """A handful of rows built key by key - the object row, the entity rows - as a frame."""
    return pl.DataFrame([dict(row) for row in rows if row],
                        infer_schema_length=None, strict=False)


def stacked(frames: Sequence[pl.DataFrame]) -> pl.DataFrame:
    """Frames of different shapes as one, keeping every column any of them has.

    diagonal_relaxed, so a column one row kind fills and another does not comes through as
    nulls, and a column that arrived typed in one frame and empty in another is widened to
    the type that can hold both rather than refused.
    """
    if not frames:
        return pl.DataFrame()
    return frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal_relaxed")


def one_table(frames: Sequence[pl.DataFrame]) -> pl.DataFrame:
    """Every object's rows as the one table the report is written from."""
    table = stacked([frame for frame in frames if frame.height])
    if not table.height:
        return pl.DataFrame()
    table = _without_empty_columns(table)
    return _in_reading_order(_measurements_as_float32(table))


def _height_of(table: Mapping[str, Sequence[Any]]) -> int:
    """How many rows a columnar table holds; its columns are all the same length."""
    return len(next(iter(table.values()), ()))


def _typed(table: Mapping[str, Sequence[Any]], declared: Mapping[str, Any]) -> pl.DataFrame:
    """One columnar table as a frame, with the types the measurer declared for it."""
    return pl.DataFrame(
        dict(table),
        schema={name: _DTYPES.get(declared[name], pl.Utf8) for name in table},
    )


def _without_empty_columns(table: pl.DataFrame) -> pl.DataFrame:
    """Drop the columns no row of this batch filled.

    Every column either dimensionality can produce is declared, so a batch of volumes
    declares the 2D ones too; a report should carry the columns it has values for.
    """
    return table.select([c for c in table.columns
                         if table[c].null_count() < table.height])


def _measurements_as_float32(table: pl.DataFrame) -> pl.DataFrame:
    """Measurements as float32, scalars and lists alike.

    Seven significant digits is far more than a µm measurement carries, and the viewer loads
    the whole file: on a real batch this is ~12% of it.
    """
    casts = []
    for column, dtype in table.schema.items():
        if dtype == pl.Float64:
            casts.append(pl.col(column).cast(pl.Float32))
        elif dtype == pl.List(pl.Float64):
            casts.append(pl.col(column).cast(pl.List(pl.Float32)))
    return table.with_columns(casts)


def _in_reading_order(table: pl.DataFrame) -> pl.DataFrame:
    """What a row is, and what it is about, before what was measured of it."""
    leading = [c for c in _LEADING_COLUMNS if c in table.columns]
    return table.select(leading + [c for c in table.columns if c not in leading])
