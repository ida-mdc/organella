"""The options commands share, as groups, and the builders that turn parsed options into the things a run is made of."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import click

from organella.app.settings import Settings, parse_geometry_as
from organella.core import normalised
from organella.geometry.writer import GeometryWriter
from organella.sinks import ParquetSink
from organella.sinks.colours import colours_from_file
from organella.sources import open_source
from organella.sources.arrays import DEFAULT_CACHE_BYTES
from organella.sources.label_map import read_label_map


def _names(_ctx, _param, value: str | None) -> frozenset[str]:
    """``NAME,NAME`` as normalised names."""
    return frozenset(normalised(n) for n in value.split(",") if n.strip()) if value else frozenset()


def _sigma(_ctx, _param, value: str) -> float | tuple[float, ...]:
    """``0.7`` or ``Z,Y,X``."""
    numbers = tuple(float(v) for v in value.split(","))
    return numbers[0] if len(numbers) == 1 else numbers


def _voxel_size(_ctx, _param, value: str | None) -> tuple[float, ...] | None:
    return tuple(float(v) for v in value.split(",")) if value else None


def _geometry_as(_ctx, _param, value: str) -> dict[str, str]:
    try:
        return parse_geometry_as(value)
    except ValueError as error:
        raise click.BadParameter(str(error)) from error


def _group(*options: Callable) -> Callable:
    def apply(command: Callable) -> Callable:
        for option in reversed(options):
            command = option(command)
        return command
    return apply


source_options = _group(
    click.option("-p", "--paths", "groups", multiple=True, help="Subfolder to import as a group, repeatable."),
    click.option("--object-mask", help="Mask that bounds each object; everything else is clipped to it."),
    click.option("--voxel-size-um", callback=_voxel_size, help="Z,Y,X in µm (Y,X for a plane); read from the files when omitted."),
    click.option("--entities", callback=_names, help="Measure only these structures, plus the object mask: NAME,NAME."),
    click.option("--label-map", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                 help='JSON of {"1": "liver"}: split one volume whose ids each mean a different structure; only named ids become structures.'),
    click.option("--label-map-entity", help="Which structure --label-map splits; needed only when there is more than one labelled."),
    click.option("--cache-gb", default=DEFAULT_CACHE_BYTES / 2**30, show_default=True,
                 help="Memory for decoded chunks of a Zarr or N5 store, shared by the sample's arrays."),
)

distance_options = _group(
    click.option("--reach-um", default=Settings.reach_um, show_default=True, help="Distances are exact up to this far."),
)

measure_options = _group(
    click.option("--chunk-edge", default=Settings.chunk_edge, show_default=True,
                 help="Voxels along an edge of the cubes the volume is read in; must exceed the reach in voxels."),
    click.option("--no-clip", is_flag=True, help="Measure outside the object mask too. Clipped to it by default."),
    click.option("--no-contacts", is_flag=True, help="Skip which instances of a structure touch."),
    click.option("--contact-max-um", default=Settings.contact_max_um, show_default=True,
                 help="The largest gap between two instances of one structure that still counts as a contact."),
    click.option("--skeletons", callback=_names, help="Structures to trace a skeleton of, for branches, length and tortuosity: NAME,NAME. The costliest option."),
    click.option("--max-skeleton-voxels", default=Settings.max_skeleton_voxels, show_default=True, help="Instances above this many voxels are not traced."),
    click.option("--skeleton-scale", default=Settings.skeleton_scale, show_default=True, help="Reach around a traced path, in local radii."),
    click.option("--skeleton-const-voxels", default=Settings.skeleton_const_voxels, show_default=True, help="Reach added to it, in voxels of the coarsest axis."),
    click.option("--skeleton-prune", default=Settings.skeleton_prune, show_default=True, help="Cut terminal branches shorter than this many radii; 0 keeps all."),
)

statistics_options = _group(
    click.option("--no-instances", is_flag=True, help="Per-structure rows only: no instance, distance or baseline rows."),
    click.option("--polarity-spread", is_flag=True, help="Measure each instance's angular spread as seen from the object centre."),
    click.option("--distance-histograms", is_flag=True, help="Per instance and target, a histogram of its voxels' distances, not only the nearest."),
    click.option("--baseline-exclude", callback=_names, help="Structures left out of the region every distance is read against, e.g. nucleus: NAME,NAME."),
)

geometry_options = _group(
    click.option("--geometry-as", default="", callback=_geometry_as,
                 help="How a surface is stored: NAME=KIND,... with KIND one of mesh, ellipsoid, tube; * stands for the rest; "
                      "+skeleton also traces one. Unnamed: decided from the shape."),
    click.option("--mesh-smooth-sigma", default="0.7", show_default=True, callback=_sigma,
                 help="Smoothing before marching cubes: one number in voxels of the finest axis, or Z,Y,X; 0 is none."),
    click.option("--mesh-step-size", default=Settings.mesh_step_size, show_default=True, help="Marching-cubes step; 1 is full resolution."),
    click.option("--mesh-target-reduction", default=Settings.mesh_target_reduction, show_default=True, help="Fraction of faces removed by decimation."),
    click.option("--mesh-level", type=float, help="Iso-surface level on the signed distance field (default 0)."),
    click.option("--mesh-max-vertices", default=Settings.mesh_max_vertices, show_default=True, help="Most vertices per surface; 0 is no cap."),
    click.option("--reuse-geometry", is_flag=True, help="Keep a geometry file an earlier run wrote under the same settings."),
)

report_options = _group(
    click.option("--object-noun", help="What one measured thing is called in the report, e.g. cell, or nucleus/nuclei."),
    click.option("--description", default="", help="What this data is and who it credits; the report page shows it above the first section."),
    click.option("--colours", "--colors", "colours", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                 help='JSON of {"mito": "#d62728"}: structures drawn in that colour in every chart.'),
)


def source_from(path: Path, o: dict):
    return open_source(path, o["groups"], o["object_mask"], o["voxel_size_um"], int(o["cache_gb"] * 2**30), o["entities"],
                       read_label_map(o["label_map"]) if o["label_map"] else None, o["label_map_entity"])


def settings_from(o: dict, *, geometry: bool) -> Settings:
    return Settings(
        reach_um=o.get("reach_um", Settings.reach_um), chunk_edge=o["chunk_edge"], clip_to_object_mask=not o["no_clip"], contacts=not o["no_contacts"],
        contact_max_um=o["contact_max_um"], skeletons=o["skeletons"], max_skeleton_voxels=o["max_skeleton_voxels"],
        skeleton_scale=o["skeleton_scale"], skeleton_const_voxels=o["skeleton_const_voxels"], skeleton_prune=o["skeleton_prune"],
        instances=not o.get("no_instances", False), polarity_spread=o.get("polarity_spread", False),
        distance_histograms=o.get("distance_histograms", False), baseline_exclude=o.get("baseline_exclude", frozenset()),
        geometry_as=o["geometry_as"], keep_skeletons=geometry, mesh_smooth_sigma=o["mesh_smooth_sigma"],
        mesh_step_size=o["mesh_step_size"], mesh_target_reduction=o["mesh_target_reduction"], mesh_level=o["mesh_level"],
        mesh_max_vertices=o["mesh_max_vertices"],
    )


def geometry_into(folder: Path, o: dict) -> GeometryWriter:
    return GeometryWriter(folder, o["reuse_geometry"])


def sink_to(output: Path, input: Path, o: dict) -> ParquetSink:
    return ParquetSink(output, o["object_noun"], o["description"], colours_from_file(o["colours"]) if o["colours"] else {}, o["groups"], input)
