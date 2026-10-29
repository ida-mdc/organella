from __future__ import annotations

import logging
import time
from typing import Sequence

import polars as pl

from organella.app.prepare import prepared
from organella.app.ports import GeometrySink, Measurement, PartStore, ReportSink, RunInfo, SampleSource
from organella.app.settings import Settings
from organella.core import OBS_LEVEL, RowType, Sample

log = logging.getLogger(__name__)


def process(
    source: SampleSource,
    measurements: Sequence[Measurement],
    sink: ReportSink,
    settings: Settings,
    geometry: GeometrySink | None = None,
    parts: PartStore | None = None,
) -> dict[str, str]:
    """Measure every sample and write one report, and the geometry beside it if asked. Returns the failures by sample id."""
    frames: list[pl.DataFrame] = []
    failures: dict[str, str] = {}
    seconds: dict[str, float] = {}
    started = time.time()
    fingerprint = settings.fingerprint(geometry is not None, [type(m).__name__ for m in measurements])
    for ref in source.samples():
        began = time.time()
        try:
            kept = parts.load(ref.id, fingerprint) if parts else None
            if kept is not None:
                log.info("%s: reusing the rows an earlier run measured", ref.id)
            else:
                kept = _measure(ref.open(), measurements, settings, geometry)
                if parts:
                    parts.save(ref.id, fingerprint, kept)
            frames.extend(kept)
            seconds[ref.id] = time.time() - began
        except Exception as error:  # one bad sample must not lose the report of the others
            log.exception("%s failed", ref.id)
            failures[ref.id] = f"{type(error).__name__}: {error}"
    if not frames and not failures:
        failures["input"] = "no samples found"
    if frames:
        sink.write(pl.concat(frames, how="diagonal_relaxed"), RunInfo(time.time() - started, seconds, failures))
        if parts and not failures:
            parts.discard()   # the report they were insurance for exists; with a failure they are kept for the next attempt
    return failures


def _measure(sample: Sample, measurements: Sequence[Measurement], settings: Settings,
             geometry: GeometrySink | None = None) -> list[pl.DataFrame]:
    sample, shared = prepared(sample, settings), {}
    frames = [
        _stamped(rows, row_type, sample)
        for measurement in measurements
        for row_type, rows in measurement.measure(sample, settings, shared).items()
        if rows.width
    ]
    if geometry is None:
        return frames
    where = pl.lit(str(geometry.write(sample, shared, settings)))   # the object row says where its geometry went
    return [f.with_columns(mesh_geometry_file=where) if f["row_type"][0] == RowType.OBJECT else f for f in frames]


def _stamped(rows: pl.DataFrame, row_type: RowType, sample: Sample) -> pl.DataFrame:
    return rows.with_columns(
        object_id=pl.lit(sample.id),
        row_type=pl.lit(row_type.value),
        obs_level=pl.lit(OBS_LEVEL[row_type], dtype=pl.Int64),
    )
