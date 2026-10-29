from __future__ import annotations

from dataclasses import dataclass
from functools import reduce

import dask
import dask.array as da
import numpy as np
import polars as pl
from scipy.spatial import cKDTree

from organella.app.settings import Settings
from organella.core import Kind, RowType, Sample, Structure
from organella.measure import columns
from organella.measure.depth import boundary_voxels, depth_um
from organella.measure.per_label import counts_and_centroids
from organella.measure.polarity import aspect_ratio, polarity, spread_deg
from organella.measure.contacts import lazy_gaps, merged
from organella.measure.polarity import lazy_spread
from organella.measure.skeletons import SkeletonParams, lazy_skeleton
from organella.measure.surface import lazy_surface
from organella.measure.windows import compute_in_batches


@dataclass(frozen=True)
class Findings:
    """What the statistics pass found, left in the sample's ``shared`` for the geometry to draw from."""

    stats: dict[str, pl.DataFrame]       # per structure: id, count, centroid, covariance, bounding box
    entities: pl.DataFrame
    instances: dict[str, pl.DataFrame]   # per labelled structure
    skeletons: dict[str, list]           # per skeletonised structure, one Traced per instance
    contacts: pl.DataFrame | None


class Statistics:
    """The object row, a row per structure, and a row per instance of the labelled ones.

    One pass reads each structure for counts, centroids, moments and boxes together, which matters when its
    chunks come from a remote store; a second reads each instance's own box for its surface. The object's
    centre, which every polarity column is measured from, comes out of the first pass.
    """

    def measure(self, sample: Sample, settings: Settings, shared: dict) -> dict[RowType, pl.DataFrame]:
        structures = list(sample.structures.values())
        mask = sample.structures[sample.object_mask] if sample.object_mask else None
        # All delayed: computed together with arrays, dask hands the delayed results back out of order.
        lazies = [counts_and_centroids(_origin_region(sample).astype(np.uint8)), *map(_lazy_statistics, structures)]
        if mask is not None:
            lazies.append(dask.delayed(np.asarray)(boundary_voxels(mask.foreground)))
        found = dask.compute(*lazies)
        centre_stats, per_structure = found[0], dict(zip((s.name for s in structures), found[1:]))
        boundary = found[-1] if mask is not None else None

        edge = np.array(sample.voxel_size.um)
        centre = None if centre_stats.height == 0 else np.array(centre_stats.select(f"c{i}" for i in range(len(edge))).row(0)) * edge
        centroids = {s.name: _entity_centroid(per_structure[s.name]) for s in structures}
        depths = self._depths(sample, mask, boundary, centroids, edge)
        surfaces = self._surfaces(sample, structures, per_structure, settings)
        contacts = self._contacts(sample, structures, per_structure, settings) if settings.contacts else None
        skeletons = self._skeletons(sample, structures, per_structure, settings, shared)
        spreads = self._spreads(sample, structures, per_structure, settings, centre)
        entities = self._entities(sample, structures, per_structure, centroids, depths, surfaces, centre, edge)
        instances = self._instances(sample, structures, per_structure, surfaces, skeletons, spreads, centre, edge)
        shared["findings"] = Findings(per_structure, entities, instances, skeletons, contacts)
        return {
            RowType.OBJECT: pl.DataFrame([_object_row(sample, centre_stats, centre, contacts)]),
            RowType.ENTITY: entities,
            RowType.INSTANCE: pl.concat(instances.values(), how="diagonal_relaxed") if instances else pl.DataFrame(),
            RowType.CONTACT: contacts if contacts is not None else pl.DataFrame(),
        }

    @staticmethod
    def _spreads(sample, structures, per_structure, settings, centre) -> dict[str, np.ndarray]:
        """The angular spread of each instance's voxels around the object centre, for the structures that have instances."""
        if not (settings.polarity_spread and settings.instances and centre is not None):
            return {}
        voxel, ndim = np.array(sample.voxel_size.um), len(sample.shape)
        out = {}
        for s in structures:
            stats = per_structure[s.name]
            if s.kind is not Kind.LABEL or not stats.height:
                continue
            lazies = [lazy_spread(s.data, *_bounds(stats, r, ndim), int(stats["id"][r]), voxel, centre) for r in range(stats.height)]
            out[s.name] = np.array(compute_in_batches(lazies, settings.surface_batch), dtype=np.float64)
        return out

    @staticmethod
    def _skeletons(sample, structures, per_structure, settings, shared) -> dict[str, list]:
        """A skeleton traced for each instance of the structures asked for; their tips are left for the distances."""
        params = SkeletonParams(settings.skeleton_scale, settings.skeleton_const_voxels, settings.skeleton_prune)
        voxel_um, ndim, traced = tuple(sample.voxel_size.um), len(sample.shape), {}
        for s in structures:
            stats = per_structure[s.name]
            if s.kind is not Kind.LABEL or not settings.instances or not settings.wants_skeleton(s.name) or not stats.height:
                continue
            lazies = [lazy_skeleton(s.data, np.array([stats[f"lo{i}"][r] for i in range(ndim)]),
                                    np.array([stats[f"hi{i}"][r] for i in range(ndim)]), int(stats["id"][r]), voxel_um,
                                    params, settings.max_skeleton_voxels, settings.keep_skeletons) for r in range(stats.height)]
            traced[s.name] = compute_in_batches(lazies, settings.surface_batch)
            shared.setdefault("tips", {})[s.name] = {int(label): t.tips for label, t in zip(stats["id"], traced[s.name])}
        return traced

    @staticmethod
    def _contacts(sample, structures, per_structure, settings) -> pl.DataFrame:
        """Each pair of instances of one structure within the contact distance of each other."""
        spacing, tables = tuple(sample.voxel_size.um), []
        for s in structures:
            stats = per_structure[s.name]
            if s.kind is not Kind.LABEL or stats.height < 2:
                continue
            lazies = [lazy_gaps(s.data, np.array([stats[f"lo{i}"][r] for i in range(len(spacing))]),
                                np.array([stats[f"hi{i}"][r] for i in range(len(spacing))]),
                                int(stats["id"][r]), sample.voxel_size, settings.contact_max_um)
                      for r in range(stats.height)]
            tables.append(merged(compute_in_batches(lazies, settings.surface_batch), s.name))
        return pl.concat(tables) if tables else merged([], "")

    @staticmethod
    def _depths(sample, mask, boundary, centroids, edge) -> dict[str, float]:
        """How deep inside the object each structure's centroid sits; 0 where the centroid falls outside it."""
        located = {name: c for name, c in centroids.items() if c is not None}
        if mask is None or not located:
            return {}
        at = np.clip(np.rint(np.array(list(located.values()))).astype(np.int64), 0, np.array(sample.shape) - 1)
        inside = mask.foreground.vindex[tuple(at.T)].compute()
        return dict(zip(located, np.where(inside, depth_um(boundary, edge, at.astype(float)), 0.0)))

    @staticmethod
    def _surfaces(sample, structures, per_structure, settings) -> dict[str, pl.DataFrame]:
        """Boundary and roundness of each instance of a labelled structure, and of each mask as a whole."""
        spacing = tuple(sample.voxel_size.um)
        wanted: list[tuple[str, int]] = []
        lazies = []
        for s in structures:
            stats = per_structure[s.name]
            if s.kind is Kind.LABEL and not settings.instances:
                continue
            for row in range(stats.height):
                low = np.array([stats[f"lo{i}"][row] for i in range(len(spacing))])
                high = np.array([stats[f"hi{i}"][row] for i in range(len(spacing))])
                label = int(stats["id"][row]) if s.kind is Kind.LABEL else None
                data = s.data if s.kind is Kind.LABEL else s.foreground
                lazies.append(lazy_surface(data, low, high, label, spacing, settings.coarse_voxels))
                wanted.append((s.name, row))
        frames = compute_in_batches(lazies, settings.surface_batch)
        return {s.name: pl.concat([f for (n, _), f in zip(wanted, frames) if n == s.name])
                for s in structures if s.name in {n for n, _ in wanted}}

    @staticmethod
    def _entities(sample, structures, per_structure, centroids, depths, surfaces, centre, edge) -> pl.DataFrame:
        ndim, rows = len(edge), []
        for s in structures:
            stats = per_structure[s.name]
            total = float(stats["count"].sum()) * sample.voxel_size.volume
            row = {"entity_name": s.name, "entity_kind": s.kind.value, columns.total_size(ndim): total,
                   "instance_count": stats.height if s.kind is Kind.LABEL else None}
            if s.kind is Kind.MASK and stats.height:
                shape = surfaces[s.name]
                row.update({columns.size(ndim): total, columns.boundary(ndim): shape["boundary"][0],
                            columns.roundness(ndim): shape["roundness"][0],
                            "aspect_ratio_major_minor": float(aspect_ratio(_covariance(stats, edge))[0])})
                if centre is not None:
                    row.update({k: float(v[0]) for k, v in polarity((centroids[s.name] * edge - centre)[None, :]).items()})
            row["polar_depth_um"] = depths.get(s.name)
            rows.append(row)
        return pl.DataFrame(rows, schema_overrides={"instance_count": pl.Int64}, infer_schema_length=None)

    @staticmethod
    def _instances(sample, structures, per_structure, surfaces, skeletons, spreads, centre, edge) -> dict[str, pl.DataFrame]:
        return {s.name: _instance_rows(s, per_structure[s.name], surfaces[s.name], skeletons, spreads.get(s.name), sample, centre, edge)
                for s in structures if s.kind is Kind.LABEL and per_structure[s.name].height and surfaces.get(s.name) is not None}


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


def _object_row(sample: Sample, centre_stats: pl.DataFrame, centre, contacts: pl.DataFrame | None) -> dict:
    axes, edge = sample.voxel_size.axes, sample.voxel_size.um
    row = {
        "imported_path_short": sample.group,
        **{f"pixel_size_{a}": e for a, e in zip(axes, edge)},
        **{f"size_{a}": n for a, n in zip(axes, sample.shape)},
        "object_mask_name": sample.object_mask,
    }
    if sample.object_mask:
        row[columns.object_size(len(edge))] = float(centre_stats["count"].sum()) * sample.voxel_size.volume
    if centre is not None:
        row.update({f"object_center_{a}_um": float(c) for a, c in zip(axes, centre)})
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


def _bounds(stats: pl.DataFrame, row: int, ndim: int) -> tuple[np.ndarray, np.ndarray]:
    return (np.array([stats[f"lo{i}"][row] for i in range(ndim)]), np.array([stats[f"hi{i}"][row] for i in range(ndim)]))
