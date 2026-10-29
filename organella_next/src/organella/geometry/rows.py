"""The rows of one object's geometry file: an instance per row with the surface it is drawn as, whole-structure masks,
and the touching pairs. Each carries the measurements the report has, so a row can be sorted and coloured on its own."""
from __future__ import annotations

import dask
import numpy as np
import polars as pl
from dask.delayed import Delayed

from organella.app.settings import Settings
from organella.core import Kind, Sample, Structure
from organella.geometry.payloads import ellipsoid_payload, skeleton_payload, tube_payload
from organella.geometry.surfaces import choose_surface, generate_mesh, generate_outline, instance_sigma
from organella.measure.windows import box

# Measurements carried onto each row beside its size and shape, so a row sorts and colours on its own.
CARRIED = ["aspect_ratio_major_minor", "branches", "length_um", "tortuosity", "distance_to_closest_same_type_um",
           "polar_dist_um", "polar_az_deg", "polar_el_deg", "polar_angle_deg", "polar_nz", "polar_ny", "polar_nx",
           "polar_spread_deg"]
SHAPE = {3: ["volume_um3", "surface_area_um2", "sphericity"], 2: ["area_um2", "perimeter_um", "circularity"]}


def ellipsoid_of(covariance_voxels: np.ndarray, centroid_voxels: np.ndarray, voxel_um: np.ndarray, voxels: int) -> bytes:
    """The ellipsoid with the instance's own second moments and its own volume: its centre, diameters and principal axes
    in x, y, z.

    A solid ellipsoid of semi-axis r has variance r squared over five along that axis, so the shape comes from twice
    the square root of five times the variance. It is then scaled to the volume the instance really has, as ITK's
    equivalent ellipsoid is: a few voxels cannot fill the volume their moments imply.
    Axes are the rows, in the order of the ascending diameters.
    """
    if len(voxel_um) != 3:
        return b""
    values, vectors = np.linalg.eigh(covariance_voxels * np.outer(voxel_um, voxel_um))
    diameters = 2.0 * np.sqrt(5.0 * np.maximum(values, 0.0))
    implied = np.pi / 6.0 * np.prod(diameters)
    if implied > 0:
        diameters = diameters * (voxels * np.prod(voxel_um) / implied) ** (1.0 / 3.0)
    return ellipsoid_payload((centroid_voxels * voxel_um)[::-1], diameters, vectors.T[:, ::-1].ravel())


def _mesh_job(window: np.ndarray, label: int | None, origin: np.ndarray, voxel_um: tuple[float, ...], settings: Settings,
              sigma, step: int) -> bytes:
    """One instance's (or one mask's) surface payload: a mesh in a volume, an outline in a plane."""
    binary = window > 0 if label is None else window == label
    spacing = tuple(v * step for v in voxel_um)
    if len(voxel_um) == 2:
        return generate_outline(binary, origin / step, spacing)
    return generate_mesh(binary, origin / step, spacing, step_size=settings.mesh_step_size, smooth_sigma=sigma,
                         target_reduction=settings.mesh_target_reduction, level=settings.mesh_level,
                         max_vertices=settings.mesh_max_vertices)


def _bounds(stats: pl.DataFrame, row: int, ndim: int) -> tuple[np.ndarray, np.ndarray]:
    return (np.array([stats[f"lo{i}"][row] for i in range(ndim)]), np.array([stats[f"hi{i}"][row] for i in range(ndim)]))


def instance_rows(structure: Structure, sample: Sample, stats: pl.DataFrame, report: pl.DataFrame, traced: list | None,
                  settings: Settings) -> tuple[list[dict], list[tuple[dict, Delayed]]]:
    """The rows of a labelled structure's instances, and the meshes still to be computed for them."""
    voxel = np.array(sample.voxel_size.um)
    ndim, planar = len(voxel), len(voxel) == 2
    forced = settings.forced_surface(structure.name)
    measured = report.rename({c: c.removeprefix("instance_") for c in report.columns}).to_dicts()
    rows, jobs = [], []
    for r, found in enumerate(measured):
        label = int(stats["id"][r])
        low, high = _bounds(stats, r, ndim)
        roundness = found.get("sphericity", found.get("circularity", float("nan")))
        skeleton = traced[r] if traced is not None else None
        has_skeleton = skeleton is not None and skeleton.vertices is not None and len(skeleton.vertices) > 0
        kind = "mesh" if planar else (forced or choose_surface(roundness, found.get("aspect_ratio_major_minor"),
                                                                 skeleton.branches if skeleton is not None else None, has_skeleton,
                                                                 found.get("volume_um3")))
        surface = b""
        if kind == "ellipsoid":
            covariance = np.array([[stats[f"v{min(i, j)}{max(i, j)}"][r] for j in range(ndim)] for i in range(ndim)])
            surface = ellipsoid_of(covariance, np.array([stats[f"c{i}"][r] for i in range(ndim)]), voxel, int(stats["count"][r]))
        elif kind == "tube" and has_skeleton and skeleton.radii is not None:
            surface = tube_payload(skeleton.vertices, skeleton.edges, skeleton.radii)
        if kind != "mesh" and not surface:
            kind = "mesh"   # a primitive needs finite moments or a centre line; an instance with neither is still a shape
        row = {**{k: v for k, v in found.items() if k in CARRIED or k in SHAPE[ndim]},
               "entity_name": structure.name, "entity_kind": "label", "row_type": "instance", "label_id": label,
               "spatial_dims": ndim, "surface_kind": kind, "surface": surface, "outline": b"",
               "skeleton": skeleton_payload(skeleton.vertices, skeleton.edges) if has_skeleton else b""}
        rows.append(row)
        if kind == "mesh":
            fill = float(stats["count"][r]) / float(np.prod(high - low + 1))
            sigma = instance_sigma(settings.mesh_smooth_sigma, roundness, fill)
            jobs.append((row, dask.delayed(_mesh_job)(box(structure.data, low, high), label, low, tuple(voxel), settings, sigma, 1)))
    return rows, jobs


def mask_rows(structure: Structure, sample: Sample, stats: pl.DataFrame, entity: dict, settings: Settings):
    """The row of a whole-structure mask: its measurements and its surface, or nothing if it is empty."""
    if stats.height == 0:
        return [], []
    ndim = len(sample.voxel_size.um)
    low, high = _bounds(stats, 0, ndim)
    roundness = entity.get("sphericity", entity.get("circularity", float("nan")))
    fill = float(stats["count"][0]) / float(np.prod(high - low + 1))
    row = {**{k: v for k, v in entity.items() if k in SHAPE[ndim] or k == "aspect_ratio_major_minor"},
           "entity_name": structure.name, "entity_kind": "mask", "row_type": "file", "label_id": None,
           "spatial_dims": ndim, "surface_kind": "mesh", "surface": b"", "outline": b"", "skeleton": b""}
    voxels = int(np.prod(high - low + 1))
    step = max(1, int(np.ceil((voxels / settings.coarse_voxels) ** (1 / ndim))))
    job = dask.delayed(_mesh_job)(box(structure.foreground, low, high, step=step), None, low, tuple(sample.voxel_size.um),
                                  settings, instance_sigma(settings.mesh_smooth_sigma, roundness, fill), step)
    return [row], [(row, job)]


def contact_rows(contacts: pl.DataFrame | None) -> list[dict]:
    """The touching pairs, in the row shape the viewer splits out on load. Both sides are one structure."""
    if contacts is None:
        return []
    return [{"row_type": "contact", "entity_a": c["contact_entity"], "label_a": c["contact_label_a"],
             "entity_b": c["contact_entity"], "label_b": c["contact_label_b"], "gap_um": c["contact_gap_um"],
             "surface": b"", "skeleton": b""} for c in contacts.to_dicts()]
