from __future__ import annotations

import json
from functools import reduce

import dask
import dask.array as da
import numpy as np
import polars as pl
from scipy.spatial import cKDTree

from organella.app.settings import Settings
from organella.app.shared import Findings, Shared
from organella.core import Kind, RowType, Sample, Structure
from organella.measure import columns
from organella.measure.contacts import contacts_of
from organella.measure.depth import boundary_voxels, centroid_depths
from organella.measure.per_label import counts_and_centroids
from organella.measure.polarity import aspect_ratio, direction_profile, polarity, spreads
from organella.measure.skeletons import trace_all
from organella.measure.boundary import boundaries


class Statistics:
    """The object row, a row per structure, and a row per instance of the labelled ones.

    One pass reads each structure for counts, centroids, moments and boxes together, which matters when its
    chunks come from a remote store. Each metric that needs an instance whole (surface, skeleton, contacts, polarity
    spread) then reads that instance's own box, in its own module. The object's centre, which every polarity column is
    measured from, comes out of the first pass.
    """

    def measure(self, sample: Sample, settings: Settings, shared: Shared) -> dict[RowType, pl.DataFrame]:
        structures = list(sample.structures.values())
        centre_stats, per_structure, boundary = _first_pass(sample)
        edge = np.array(sample.voxel_size.um)
        centre = None if centre_stats.height == 0 else np.array(centre_stats.select(f"c{i}" for i in range(len(edge))).row(0)) * edge
        centroids = {s.name: _entity_centroid(per_structure[s.name]) for s in structures}

        depths = centroid_depths(sample, boundary, centroids)
        shapes = boundaries(sample, per_structure, settings)
        contacts = contacts_of(sample, per_structure, settings) if settings.contacts else None
        skeletons = trace_all(sample, per_structure, settings)
        spread = spreads(sample, per_structure, settings, centre)
        directions = None if centre is None else dask.compute(direction_profile(_origin_region(sample), edge, centre))[0]
        shared.tips = {name: {int(label): t.tips for label, t in zip(per_structure[name]["id"], traced)}
                       for name, traced in skeletons.items()}

        entities = _entity_rows(sample, per_structure, centroids, depths, shapes, centre, edge)
        instances = {s.name: _instance_rows(s, per_structure[s.name], shapes[s.name], skeletons, spread.get(s.name), sample, centre, edge)
                     for s in sample.labelled() if per_structure[s.name].height and s.name in shapes}
        shared.findings = Findings(per_structure, entities, instances, skeletons, contacts)
        return {
            RowType.OBJECT: pl.DataFrame([_object_row(sample, centre_stats, centre, contacts, directions)]),
            RowType.ENTITY: entities,
            RowType.INSTANCE: pl.concat(instances.values(), how="diagonal_relaxed") if instances else pl.DataFrame(),
            RowType.CONTACT: contacts if contacts is not None else pl.DataFrame(),
        }


def _first_pass(sample: Sample) -> tuple[pl.DataFrame, dict[str, pl.DataFrame], np.ndarray | None]:
    """The object's own counts and centroid, the same for every structure, and the object mask's outside boundary."""
    structures = list(sample.structures.values())
    mask = sample.structures[sample.object_mask] if sample.object_mask else None
    # All delayed: computed together with arrays, dask hands the delayed results back out of order.
    lazies = [counts_and_centroids(_origin_region(sample).astype(np.uint8)), *map(_lazy_statistics, structures)]
    if mask is not None:
        lazies.append(boundary_voxels(mask.foreground))
    found = dask.compute(*lazies)
    return found[0], dict(zip((s.name for s in structures), found[1:1 + len(structures)])), found[-1] if mask is not None else None


def _entity_rows(sample, per_structure, centroids, depths, shapes, centre, edge) -> pl.DataFrame:
    structures, ndim, rows = list(sample.structures.values()), len(edge), []
    for s in structures:
        stats = per_structure[s.name]
        total = float(stats["count"].sum()) * sample.voxel_size.volume
        row = {"entity_name": s.name, "entity_kind": s.kind.value, columns.total_size(ndim): total,
               "instance_count": stats.height if s.kind is Kind.LABEL else None}
        if s.kind is Kind.MASK and stats.height:
            shape = shapes[s.name]
            row.update({columns.size(ndim): total, columns.boundary(ndim): shape["boundary"][0],
                        columns.roundness(ndim): shape["roundness"][0],
                        "aspect_ratio_major_minor": float(aspect_ratio(_covariance(stats, edge))[0])})
            if centre is not None:
                row.update({k: float(v[0]) for k, v in polarity((centroids[s.name] * edge - centre)[None, :]).items()})
        row["polar_depth_um"] = depths.get(s.name)
        rows.append(row)
    return pl.DataFrame(rows, schema_overrides={"instance_count": pl.Int64}, infer_schema_length=None)


def _origin_region(sample: Sample) -> da.Array:
    """What the object's centre is taken of: its mask, or with no mask everything that was segmented."""
    if sample.object_mask:
        return sample.region
    return reduce(da.logical_or, (s.foreground for s in sample.structures.values()))


def _lazy_statistics(structure: Structure):
    return counts_and_centroids(structure.data if structure.kind is Kind.LABEL else structure.foreground.astype(np.uint8))


def _entity_centroid(stats: pl.DataFrame) -> np.ndarray | None:
    """The centroid in voxel coordinates of the whole structure, from its instances' counts and centroids."""
    if stats.height == 0:
        return None
    centres = stats.select(c for c in stats.columns if c[0] == "c" and c[1:].isdigit()).to_numpy()
    return (centres * stats["count"].to_numpy()[:, None]).sum(axis=0) / stats["count"].sum()


def _covariance(stats: pl.DataFrame, edge: np.ndarray) -> np.ndarray:
    """Covariances ``(n, ndim, ndim)`` of each row's voxel coordinates, in µm."""
    ndim = len(edge)
    covariance = np.empty((stats.height, ndim, ndim))
    for i in range(ndim):
        for j in range(i, ndim):
            covariance[:, i, j] = covariance[:, j, i] = stats[f"v{i}{j}"].to_numpy() * edge[i] * edge[j]
    return covariance


def _object_row(sample: Sample, centre_stats: pl.DataFrame, centre, contacts: pl.DataFrame | None, directions: np.ndarray | None) -> dict:
    axes, edge = sample.voxel_size.axes, sample.voxel_size.um
    row = {
        "imported_path_short": sample.group,
        **{f"pixel_size_{a}": e for a, e in zip(axes, edge)},
        **{f"size_{a}": n for a, n in zip(axes, sample.shape)},
        "object_mask_name": sample.object_mask,
        "spatial_dims": len(edge),
    }
    if sample.object_mask:
        row[columns.object_size(len(edge))] = float(centre_stats["count"].sum()) * sample.voxel_size.volume
    if centre is not None:
        row.update({f"object_center_{a}_um": float(c) for a, c in zip(axes, centre)})
    if directions is not None:
        row["polar_shape_counts"] = json.dumps(directions[0].astype(int).tolist())
        row["polar_shape_reach"] = json.dumps(np.round(directions[1], 3).tolist())
    if contacts is not None:
        row["contact_count"] = contacts.height
    return row


def _instance_rows(structure: Structure, stats: pl.DataFrame, surface: pl.DataFrame, skeletons: dict, spread: np.ndarray | None,
                   sample: Sample, centre, edge) -> pl.DataFrame:
    ndim = len(edge)
    centroid_um = stats.select(f"c{i}" for i in range(ndim)).to_numpy() * edge
    nearest = (cKDTree(centroid_um).query(centroid_um, k=2)[0][:, 1] if stats.height > 1
               else np.full(stats.height, np.nan))
    polar = {} if centre is None else {f"instance_{k}": v for k, v in polarity(centroid_um - centre).items()}
    shape = {}
    if skeletons:  # a column for every structure once any was traced; NaN where this one was not
        traced = skeletons.get(structure.name)
        for column, attribute in (("branches", "branches"), ("length_um", "length_um"), ("tortuosity", "tortuosity")):
            shape[f"instance_{column}"] = (np.array([getattr(t, attribute) for t in traced], dtype=np.float64)
                                           if traced is not None else np.full(stats.height, np.nan))
    return pl.DataFrame({
        "instance_entity": structure.name,
        "instance_label": stats["id"],
        f"instance_{columns.size(ndim)}": stats["count"].to_numpy() * sample.voxel_size.volume,
        f"instance_{columns.boundary(ndim)}": surface["boundary"],
        f"instance_{columns.roundness(ndim)}": surface["roundness"],
        "instance_aspect_ratio_major_minor": aspect_ratio(_covariance(stats, edge)),
        "instance_distance_to_closest_same_type_um": nearest,
        **{f"instance_centroid_{a}_um": centroid_um[:, i] for i, a in enumerate(sample.voxel_size.axes)},
        **shape,
        **({"instance_polar_spread_deg": spread} if spread is not None else {}),
        **polar,
    })
