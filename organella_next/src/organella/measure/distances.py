from __future__ import annotations

import json
from dataclasses import dataclass, field as dataclass_field

import dask
import dask.array as da
import numpy as np
import polars as pl

from organella.app.settings import Settings
from organella.core import RowType, Sample, Structure, normalised
from organella.measure.distance_field import distance_to, reach_cap
from organella.measure.per_label import (chunks_with_origin, ends_in_chunk, extremes, histogram_in_chunk, histograms_of,
                                         minimised, minimum_in_chunk, smallest, summed, tree_merged)

# The baseline is binned this much finer than it is stored, so its median is exact to a small fraction of the reach.
FINE_PER_STORED_BIN = 32


class Distances:
    """How far each instance is from every other structure, and how far anything in the object is.

    Distances are exact up to ``Settings.reach_um``; ``distance_beyond_reach`` marks the ones that are not measured past
    it. The baseline is the same distance over every voxel of the object, which is what says whether a measured distance
    is close or merely as close as anything would be.
    """

    def measure(self, sample: Sample, settings: Settings, shared: dict) -> dict[RowType, pl.DataFrame]:
        _require_halo_fits(sample, settings)
        tips = _tip_voxels(sample, shared.get("tips", {}))
        plans = [_Plan.of(sample, target, settings, tips) for target in sample.structures.values()]
        # One compute for every target: dask reads and decodes each chunk of a structure once and shares it.
        results = iter(dask.compute(*(lazy for plan in plans for lazy in plan.lazies())))
        distance_rows: list[pl.DataFrame] = []
        baseline_rows: list[pl.DataFrame] = []
        for plan in plans:
            found = plan.unpack(results)
            for source in plan.sources:
                if found.minima[source.name].height:
                    distance_rows.append(_distance_frame(source, plan.target, found.minima[source.name], found, settings))
            if found.target_exists:
                baseline_rows.append(_baseline_frame(plan.target, found.fine_counts, plan.excluded, settings))
        return {
            RowType.DISTANCE: pl.concat(distance_rows, how="diagonal_relaxed") if distance_rows else pl.DataFrame(),
            RowType.BASELINE: pl.concat(baseline_rows) if baseline_rows else pl.DataFrame(),
        }


@dataclass
class _Found:
    """What the computation of one target came to."""

    minima: dict = dataclass_field(default_factory=dict)       # source name -> nearest distance per instance
    histograms: dict = dataclass_field(default_factory=dict)   # source name -> per-instance histograms
    ends: dict = dataclass_field(default_factory=dict)          # source name -> tip distances
    fine_counts: np.ndarray | None = None
    target_exists: bool = False


@dataclass(frozen=True)
class _Plan:
    """What measuring one target needs, still lazy: one report per chunk of its field, merged in a tree."""

    target: Structure
    sources: list[Structure]
    bundle: object
    exists: object
    excluded: tuple[str, ...]

    @staticmethod
    def of(sample: Sample, target: Structure, settings: Settings, tips: dict) -> _Plan:
        target_voxels = sample.distance_target(target.name)
        field = distance_to(target_voxels, sample.voxel_size, settings.reach_um)
        sources = [s for s in sample.labelled() if s is not target]
        named = [s for s in sample.structures.values() if normalised(s.name) in settings.baseline_exclude]
        region = sample.region & ~target_voxels   # the target is always out of it: its distance to itself is zero
        for structure in named:
            if structure is not target:
                region = region & ~structure.foreground
        # Everything read against a chunk of the field is read in the one task that makes it, so no chunk waits in memory
        # for a reduction that dask would otherwise order after all the others.
        upper = float(reach_cap(settings.reach_um))
        instance_bins = settings.instance_histogram_bins if settings.distance_histograms else None
        known_tips = dask.delayed(tips)
        reports = [dask.delayed(_chunk_report)(origin, f, r, {s.name: c for s, c in zip(sources, labels)}, known_tips,
                                               instance_bins, upper, FINE_PER_STORED_BIN * settings.histogram_bins)
                   for origin, (f, r, *labels) in chunks_with_origin(field, region, *(s.data for s in sources))]
        return _Plan(target, sources, dask.delayed(_finished)(tree_merged(reports, _merged)),
                     dask.delayed(bool)(target_voxels.any()), tuple(s.name for s in named))

    def lazies(self) -> list:
        return [self.bundle, self.exists]

    def unpack(self, results) -> _Found:
        bundle, exists = next(results), next(results)
        found = _Found(minima=bundle["minima"], histograms=bundle["histograms"], ends=bundle["ends"],
                       fine_counts=bundle["fine"], target_exists=exists)
        if not found.target_exists:
            found.ends = {}   # nothing to be near: no tip distance, where the whole-instance one reads as the cap
        return found


def _chunk_report(origin: np.ndarray, field: np.ndarray, region: np.ndarray, labels: dict, tips: dict,
                  instance_bins: int | None, upper: float, fine_bins: int) -> dict:
    """Everything one chunk of a target's field says about the instances and the object's voxels in it."""
    return {
        "fine": np.histogram(field[region], bins=fine_bins, range=(0.0, upper))[0].astype(np.int64),
        "minima": {name: minimum_in_chunk(chunk, field) for name, chunk in labels.items()},
        "histograms": {name: histogram_in_chunk(chunk, field, instance_bins, upper) for name, chunk in labels.items()}
        if instance_bins else {},
        "ends": {name: ends_in_chunk(field, origin, *tips[name]) for name in labels if name in tips},
    }


def _merged(reports: list[dict]) -> dict:
    first = reports[0]
    return {
        "fine": np.sum([r["fine"] for r in reports], axis=0),
        "minima": {name: smallest([r["minima"][name] for r in reports]) for name in first["minima"]},
        "histograms": {name: summed([r["histograms"][name] for r in reports]) for name in first["histograms"]},
        "ends": {name: extremes([r["ends"][name] for r in reports]) for name in first["ends"]},
    }


def _finished(report: dict) -> dict:
    merged = _merged([report])
    return {"fine": merged["fine"], "ends": merged["ends"],
            "minima": {name: minimised(frame) for name, frame in merged["minima"].items()},
            "histograms": {name: histograms_of(frame) for name, frame in merged["histograms"].items()}}


def _tip_voxels(sample: Sample, tips_um: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Each skeletonised structure's tips as voxels ``(n, ndim)``, and which instance each belongs to."""
    edge, shape, found = np.array(sample.voxel_size.um), np.array(sample.shape), {}
    for name, by_label in tips_um.items():
        owned = [(label, t) for label, t in by_label.items() if len(t)]
        if owned:
            voxels = np.concatenate([np.clip(np.rint(t / edge).astype(np.int64), 0, shape - 1) for _, t in owned])
            found[name] = (voxels, np.concatenate([np.full(len(t), label) for label, t in owned]))
    return found


def _require_halo_fits(sample: Sample, settings: Settings) -> None:
    halo = max(sample.voxel_size.voxels_within(settings.reach_um))
    if halo >= settings.chunk_edge:
        raise ValueError(
            f"reach {settings.reach_um} µm is {halo} voxels, which does not fit chunks of "
            f"{settings.chunk_edge}: lower the reach or raise the chunk edge"
        )


def _capped(values: np.ndarray, settings: Settings) -> np.ndarray:
    """Distances as µm, a capped one reading exactly the reach that was asked for."""
    return np.where(values >= reach_cap(settings.reach_um), settings.reach_um, values.astype(np.float64))


def _distance_frame(source: Structure, target: Structure, smallest: pl.DataFrame, found: _Found, settings: Settings) -> pl.DataFrame:
    ids, minima = smallest["id"].to_numpy(), smallest["min"].to_numpy()
    frame = pl.DataFrame({
        "distance_entity": source.name,
        "distance_label": ids,
        "distance_target": target.name,
        "distance_um": _capped(minima, settings),
        "distance_beyond_reach": minima >= reach_cap(settings.reach_um),
    })
    ends, histograms = found.ends.get(source.name), found.histograms.get(source.name)
    if ends is not None:
        tips = frame.select(pl.col("distance_label").alias("id")).join(ends, on="id", how="left")
        frame = frame.with_columns(
            distance_end_min_um=pl.Series(_capped(tips["end_min"].fill_null(np.nan).to_numpy(), settings)).fill_nan(None),
            distance_end_max_um=pl.Series(_capped(tips["end_max"].fill_null(np.nan).to_numpy(), settings)).fill_nan(None),
        )
    if histograms is not None:
        both = frame.select(pl.col("distance_label").alias("id")).join(histograms, on="id", how="left")
        counts = both["counts"].to_list()
        voxels = np.array([sum(c) if c is not None else 0 for c in counts], dtype=float)
        frame = frame.with_columns(
            distance_mean_um=pl.Series(both["total"].to_numpy() / np.maximum(voxels, 1)),
            distance_hist_min_um=0.0, distance_hist_max_um=float(settings.reach_um),
            distance_hist_counts=pl.Series([json.dumps(c) for c in counts]),
        )
    return frame


def _median(counts: np.ndarray, upper: float) -> float:
    """The median of a binned population over 0..``upper``, interpolated inside the bin that holds it."""
    total = int(counts.sum())
    if total <= 0:
        return float("nan")
    cumulative = np.cumsum(counts)
    at = min(int(np.searchsorted(cumulative, total / 2.0, side="left")), len(counts) - 1)
    before = float(cumulative[at - 1]) if at else 0.0
    share = (total / 2.0 - before) / counts[at] if counts[at] > 0 else 0.0
    return (at + share) * upper / len(counts)


def _baseline_frame(target: Structure, fine: np.ndarray, excluded: tuple[str, ...], settings: Settings) -> pl.DataFrame:
    stored = fine.reshape(settings.histogram_bins, -1).sum(axis=1)
    centres = (np.arange(fine.size) + 0.5) * settings.reach_um / fine.size
    return pl.DataFrame({
        "baseline_target": target.name,
        "baseline_excluded": ",".join(excluded) or None,
        "baseline_voxels": int(fine.sum()),
        "baseline_mean_um": float((centres * fine).sum() / max(fine.sum(), 1)),
        "baseline_median_um": _median(fine, settings.reach_um),
        "baseline_hist_min_um": 0.0,
        "baseline_hist_max_um": settings.reach_um,
        "baseline_hist_counts": json.dumps(stored.astype(int).tolist()),
    }, schema_overrides={"baseline_excluded": pl.String})
