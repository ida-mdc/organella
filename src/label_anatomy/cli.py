"""``label-anatomy``: survey a batch, measure it, and open the report.

``process`` drives the pipeline in :mod:`label_anatomy.pipeline`, which loads each
object whole: an object cannot be split, because a distance is *to* another structure and a
contact is between two of them. Measurer options travel as environment variables, since a
measurer is constructed with no arguments; every flag here sets one.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Tuple

import click

if TYPE_CHECKING:                      # analysis.meshes pulls in scikit-image
    from label_anatomy.analysis.meshes import MeshOptions

from label_anatomy.config import AnatomyConfig
from label_anatomy import pipeline, report_io
from label_anatomy.pipeline.pool import LOG_LEVEL_ENV
from label_anatomy.measure.discovery import inspect_object_dir
from label_anatomy.measure.readers import read_header
from label_anatomy.measure import find_object_dirs, load_object
from label_anatomy import report_page

logger = logging.getLogger(__name__)

# Written into the parquet footer metadata (anatomy_flavour); the report page shows it in
# strip at the foot of the page.
FLAVOR = "object anatomy"

# Peak resident memory per worker, as a multiple of (stack + one distance transform).
# Calibrated on a 133-megavoxel five-entity object: predicted 4.7 GB, measured 4.4 GB.
# Measured, not guessed: on a 565-megavoxel seven-entity object the worker the OOM killer
# took was resident at 32.2 GB, where 2.5 had projected 23.7 GB. 3.5 covers that with a
# little margin. Being wrong is no longer fatal - a killed worker is retried at lower
# concurrency rather than losing the batch - but it still costs the time already spent.
_PEAK_OVERHEAD = 3.5


def _object_extent(object_dir: Path) -> Tuple[int, int]:
    """(voxels per entity, entity count) for one object, from TIFF headers only."""
    d = inspect_object_dir(object_dir)
    if d.source is None:
        return 0, 0
    try:
        shape, _ = read_header(d.source)
    except Exception:
        return 0, 0
    # Only the entities the run will actually stack: --entities is what makes an object
    # carrying a hundred structures fit at all, so budgeting for all of them would send a
    # seven-entity run to one worker.
    wanted = AnatomyConfig.from_env().entities
    names = [e.name for e in d.entities.values()]
    if wanted:
        names = [n for n in names if n in wanted or n == d.object_mask_name]
    return math.prod(int(s) for s in shape), max(1, len(names))


def _stacked_mb(object_dir: Path) -> float:
    """Megabytes one object occupies as a CZYX stack, worst case (4 bytes per label id).

    The loader narrows the stack to the smallest integer type the labels need, usually
    uint16, so this is an over-estimate - which is the safe direction for a budget whose
    only job is to stay above the real size.
    """
    voxels, entities = _object_extent(object_dir)
    return voxels * entities * 4 / 1024 / 1024


def estimate_peak_gb(object_dir: Path) -> float:
    """Rough peak resident memory for processing one object, in GB.

    The stack (2 bytes per voxel per entity) plus one whole-volume float32 distance
    transform - the measurers keep only one alive - times measured overhead.
    """
    voxels, entities = _object_extent(object_dir)
    return voxels * (2 * entities + 4) * _PEAK_OVERHEAD / 1024**3


def mesh_options(**overrides: Any) -> "MeshOptions":
    """MeshOptions from the environment, so both commands read one configuration."""
    from label_anatomy.analysis.meshes import MeshOptions

    cfg = AnatomyConfig.from_env()
    return MeshOptions(
        smooth_sigma=cfg.mesh_smooth_sigma,
        step_size=cfg.mesh_step_size,
        target_reduction=cfg.mesh_target_reduction,
        level=cfg.mesh_level,
        geometry_as=cfg.geometry_as,
        max_skeleton_voxels=cfg.max_skeleton_voxels,
        num_threads=cfg.num_threads,
        contact_max_um=cfg.contact_max_um,
        **overrides,
    )


def _apply_mesh_env(
    mesh_dir: Path | None,
    smooth_sigma: float | None,
    step_size: int | None,
    target_reduction: float | None,
    level: float | None,
    mesh_format: str | None = None,
    mesh_workers: int | None = None,
    reuse_geometry: bool = False,
) -> None:
    settings = {
        "LABEL_ANATOMY_MESH_DIR": mesh_dir,
        "LABEL_ANATOMY_MESH_FORMAT": mesh_format,
        "LABEL_ANATOMY_MESH_WORKERS": mesh_workers,
        "LABEL_ANATOMY_REUSE_GEOMETRY": 1 if reuse_geometry else None,
        "LABEL_ANATOMY_MESH_SMOOTH_SIGMA": smooth_sigma,
        "LABEL_ANATOMY_MESH_STEP_SIZE": step_size,
        "LABEL_ANATOMY_MESH_TARGET_REDUCTION": target_reduction,
        "LABEL_ANATOMY_MESH_LEVEL": level,
    }
    for key, value in settings.items():
        if value is not None:
            os.environ[key] = str(value)


def _mesh_flags(fn):
    """The --mesh-* options, identical on `process --with-mesh` and `mesh`."""
    for option in reversed([
        click.option("--mesh-smooth-sigma", type=float, default=None, metavar="SIGMA",
                     help="Gaussian sigma before marching cubes (default: 0.7; 0 disables)."),
        click.option("--mesh-step-size", type=int, default=None, metavar="N",
                     help="Marching-cubes step size; 1 = full resolution (default: 2)."),
        click.option("--mesh-target-reduction", type=float, default=None, metavar="F",
                     help="Decimation fraction (default: 0.8, keeping ~20%% of faces)."),
        click.option("--mesh-level", type=float, default=None, metavar="L",
                     help="Iso-surface level on the signed distance field (default: 0)."),
        click.option("--mesh-workers", type=int, default=None, metavar="N",
                     help="Processes meshing instances of one object (default: the cores "
                          "the object pool is not using)."),
        click.option("--reuse-geometry", is_flag=True,
                     help="Keep any geometry.parquet an object already has instead of "
                          "meshing it again. Meshing dominates a run, so this is how a "
                          "batch that died partway is finished in minutes."),
    ]):
        fn = option(fn)
    return fn


def _apply_analysis_env(
    object_mask: str | None,
    object_noun: str | None,
    voxel_size_um: str | None,
    no_clip: bool,
    auto_label_masks: bool,
    contact_max_um: float | None,
    max_skeleton_voxels: int | None,
    num_threads: int | None,
    polarity_spread: bool = False,
    distance_histograms: bool = False,
    geometry_as: str | None = None,
    entities: str | None = None,
    label_map: Path | None = None,
    label_map_entity: str | None = None,
) -> None:
    """Plugin options travel as environment variables; see config.AnatomyConfig."""
    settings = {
        "LABEL_ANATOMY_OBJECT_MASK": object_mask,
        "LABEL_ANATOMY_OBJECT_NOUN": object_noun,
        "LABEL_ANATOMY_VOXEL_SIZE_UM": voxel_size_um,
        "LABEL_ANATOMY_NO_CLIP": "1" if no_clip else None,
        "LABEL_ANATOMY_AUTO_LABEL_MASKS": "1" if auto_label_masks else None,
        "LABEL_ANATOMY_CONTACT_MAX_UM": contact_max_um,
        "LABEL_ANATOMY_MAX_SKELETON_VOXELS": max_skeleton_voxels,
        "LABEL_ANATOMY_NUM_THREADS": num_threads,
        "LABEL_ANATOMY_POLARITY_SPREAD": "1" if polarity_spread else None,
        "LABEL_ANATOMY_DISTANCE_HISTOGRAMS": "1" if distance_histograms else None,
        "LABEL_ANATOMY_GEOMETRY_AS": geometry_as,
        "LABEL_ANATOMY_ENTITIES": entities,
        "LABEL_ANATOMY_LABEL_MAP": label_map,
        "LABEL_ANATOMY_LABEL_MAP_ENTITY": label_map_entity,
    }
    for key, value in settings.items():
        if value is not None:
            os.environ[key] = str(value)


def _colours_from_file(path: Path) -> dict[str, str]:
    """The palette in a settings file, validated. See config.entity_colours for the format."""
    os.environ["LABEL_ANATOMY_ENTITY_COLOURS"] = str(path)
    try:
        return AnatomyConfig.from_env().entity_colours
    except ValueError as error:
        raise click.ClickException(str(error)) from None


@click.group()
@click.option("-q", "--quiet", is_flag=True, help="Only warnings and errors.")
@click.option("-v", "--verbose", is_flag=True, help="Everything, including per-entity detail.")
def cli(quiet: bool, verbose: bool) -> None:
    """The spatial anatomy of segmented objects: measure them, then read the report.

    A long run says what it is reading and counts off each object as it lands - objects are
    measured in a process pool, so nothing arrives in order and a batch that is working
    looks exactly like one that has hung. This is configured here because nothing else does
    it - the progress lines were PixelPatrol's to show when this was a flavour of it, and
    standalone they went nowhere.
    """
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger = logging.getLogger("label_anatomy")
    package_logger.handlers[:] = [handler]
    package_logger.setLevel(level)
    # Ours only: a dependency's INFO stream would bury the one line per object that matters.
    package_logger.propagate = False
    # The workers are spawned, so they never reach this function. They read the level back
    # out of the environment instead; without it their side of the run is silent.
    os.environ[LOG_LEVEL_ENV] = str(level)


@cli.command()
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("palette", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def colours(report: Path, palette: Path) -> None:
    """Colour an existing REPORT from a PALETTE file, without measuring anything again.

    PALETTE is JSON, one hex colour per structure:

        {"mito": "#d62728", "er": "#2ca02c"}

    The colours land on the report's entity rows, so they travel with it and every widget draws
    that structure the same. Structures the file does not name keep the built-in palette. Run it
    again with a different file to change your mind; nothing else in the report is touched.
    """
    named = report_io.recolour(report, _colours_from_file(palette))
    click.echo(f"{report}: coloured {named} entity row(s) from {palette}")


@cli.command()
@click.argument("object_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False, path_type=Path),
              help="Where to write the .parquet report.")
@click.option("--paths", "-p", multiple=True,
              help="Subdirectory to import as its own group (repeatable). Becomes the "
                   "default grouping in the report.")
@click.option("--object-mask", default=None, metavar="NAME",
              help="Mask that bounds each object, e.g. pm. Never guessed: everything is "
                   "measured relative to it, the entities are clipped and cropped to it, "
                   "and polarity is measured from its centroid. Leave it out and the "
                   "entities are measured where they lie, with no clipping, no cropping "
                   "and no extent of their own - polarity is then measured from the centre "
                   "of everything segmented. Run 'dry-run' to see the masks each folder "
                   "has.")
@click.option("--object-noun", default=None, metavar="WORD",
              help="What one measured thing is called in the report, e.g. 'cell'. Give an "
                   "irregular plural as 'nucleus/nuclei'. Presentation only - it changes no "
                   "column and no measurement, only the words the report uses. Left out, a "
                   "run with --object-mask says 'object' and one without says 'dataset', "
                   "since without a bounding mask there is no bounded thing to name.")
@click.option("--voxel-size-um", default=None, metavar="Z,Y,X",
              help="Voxel size in µm. Inferred from the source TIFF metadata when omitted.")
@click.option("--no-clip", "no_clip", is_flag=True,
              help="Measure outside the object mask too. Entities are clipped to it by "
                   "default, since that is what naming a bounding mask means; pass this "
                   "for data already confined to the object, or when truncating what "
                   "straddles the boundary is worse than including it.")
@click.option("--auto-label-masks", is_flag=True,
              help="Promote masks with several connected components to label entities.")
@click.option("--entities", default=None, metavar="NAMES",
              help="Measure only these entities, e.g. liver,spleen,aorta, plus the object "
                   "mask. Everything a folder has is measured when this is left out, which "
                   "for a published segmentation can be far more than a question needs: "
                   "each entity is another full-size channel of the stack, so a subject "
                   "carrying 117 structures is selected down before it fits in memory.")
@click.option("--label-map", metavar="FILE",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="JSON file of label id: structure name pairs, e.g. {\"1\": \"liver\"}, "
                   "splitting one volume whose ids each mean a different structure into an "
                   "entity per id. Only the ids it names become entities.")
@click.option("--label-map-entity", default=None, metavar="NAME",
              help="Which entity --label-map splits. Needed only when a folder has more "
                   "than one label entity, in which case it is an error to leave it out "
                   "rather than a guess at which one was meant.")
@click.option("--contact-max-um", type=float, default=None, metavar="T",
              help="Largest gap between two instances of one structure that still counts "
                   "as a contact (default: 0.5).")
@click.option("--max-skeleton-voxels", type=int, default=None, metavar="N",
              help="Skip curve skeletons for instances above this voxel count (default: 500000).")
@click.option("--num-threads", type=int, default=None, metavar="N",
              help="kimimaro worker count (default: 1; objects already run in parallel).")
@click.option("--geometry-as", "geometry_as", default=None, metavar="NAME=KIND,...",
              help="How each structure's surface is stored: mesh, ellipsoid or tube, "
                   "and/or skeleton for a centre line over it - combine with '+', e.g. "
                   "mito=mesh+skeleton,vesicle=ellipsoid. A structure not named here is "
                   "decided from its measured shape: round and compact ones become "
                   "ellipsoids of 60 bytes instead of meshes, which is what makes tens of "
                   "thousands of instances drawable. It is also not skeletonised, since "
                   "branches, length and tortuosity mean something for a filament and "
                   "nothing for a granule, and skeletonising is the most expensive thing "
                   "in a run.")
@click.option("--polarity-spread", is_flag=True,
              help="Also measure each instance's angular spread on the polarity sphere.")
@click.option("--distance-histograms", is_flag=True,
              help="Also measure per-instance distance distributions, not just the minimum.")
@click.option("--colours", "--colors", "colours", metavar="FILE",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="JSON file of structure: hex colour pairs, e.g. {\"mito\": \"#d62728\"}. "
                   "The report carries them, so every widget colours the same way and a shared "
                   "report arrives coloured. Structures it does not name keep the built-in "
                   "palette.")
@click.option("--no-contacts", is_flag=True,
              help="Skip the contact rows: which instances of a structure touch each other.")
@click.option("--no-instances", is_flag=True,
              help="Skip per-instance measurements: entity-level morphology only.")
@click.option("--max-workers", type=int, default=None, help="Worker processes (default: auto).")
@click.option("--resume", is_flag=True,
              help="Skip objects already measured by an interrupted run. Each object's rows "
                   "are kept in <output>_parts/ as it finishes and removed once the report "
                   "is written, so this only has anything to reuse after a run that died.")
@click.option("--with-mesh", is_flag=True,
              help="Also write per-object geometry for the 3D views and Blender: meshes "
                   "and skeletons for a volume, outlines and skeletons for a plane. It goes "
                   "to <output>_meshes/, never into the parquet.")
@click.option("--mesh-dir", type=click.Path(file_okay=False, path_type=Path), default=None,
              help="Where --with-mesh writes the geometry (default: <output>_meshes).")
@_mesh_flags
def process(
    object_dir: Path, output: Path, paths: Tuple[str, ...], object_mask: str | None,
    object_noun: str | None, voxel_size_um: str | None,
    no_clip: bool, auto_label_masks: bool, entities: str | None,
    label_map: Path | None, label_map_entity: str | None,
    contact_max_um: float | None,
    max_skeleton_voxels: int | None, num_threads: int | None,
    geometry_as: str | None, polarity_spread: bool,
    distance_histograms: bool, colours: Path | None, no_contacts: bool, no_instances: bool,
    max_workers: int | None, resume: bool, with_mesh: bool,
    mesh_dir: Path | None, mesh_smooth_sigma: float | None, mesh_step_size: int | None,
    mesh_target_reduction: float | None, mesh_level: float | None,
    mesh_workers: int | None, reuse_geometry: bool,
) -> None:
    """Analyse every object folder under OBJECT_DIR and write one report."""
    objects = find_object_dirs(object_dir)
    if not objects:
        raise click.ClickException(
            f"No object folders found under {object_dir}. An object folder holds a source image "
            "plus <prefix>_<name>_label.tif / _mask.tif volumes; run 'dry-run' to see "
            "what was rejected and why."
        )
    _apply_analysis_env(object_mask, object_noun, voxel_size_um, no_clip, auto_label_masks,
                        contact_max_um, max_skeleton_voxels, num_threads,
                        polarity_spread, distance_histograms,
                        geometry_as, entities, label_map, label_map_entity)

    meshes_to = (mesh_dir or output.with_name(output.stem + "_meshes")) if with_mesh else None
    _apply_mesh_env(meshes_to, mesh_smooth_sigma, mesh_step_size, mesh_target_reduction,
                    mesh_level, mesh_workers=mesh_workers,
                    reuse_geometry=reuse_geometry)

    excluded = {"anatomy-contacts"} if no_contacts else set()
    if not with_mesh:
        excluded.add("anatomy-mesh")
    if no_instances:
        excluded.add("anatomy-instances")

    peak = max((estimate_peak_gb(d) for d in objects), default=0.0)
    workers = pipeline.worker_count(max_workers, len(objects), peak)
    click.echo(f"{len(objects)} object folder(s); {workers} worker(s) "
               f"(largest object needs ~{peak:.1f} GB each)")

    parts = output.with_name(output.stem + "_parts")
    report = pipeline.analyse(objects, object_dir, list(paths),
                              excluded=sorted(excluded), workers=workers, peak_gb=peak,
                              parts_dir=parts, resume=resume)
    try:
        report_io.write(report, output, root=object_dir, paths=list(paths), flavor=FLAVOR,
                        object_noun=object_noun)
    except report_io.EmptyReport as empty:
        # The parts are deliberately kept: this is exactly the run worth resuming once
        # whatever went wrong is fixed.
        raise click.ClickException(
            f"{empty}. "
            + (f"All {len(objects)} object(s) failed:\n  "
               + "\n  ".join(f"{name}: {why}" for name, why in report.failures.items())
               if report.failures
               else "No object produced any rows; run 'dry-run' to see what each folder holds.")
        ) from None
    # The report is the durable copy now, so the per-object ones have done their job. Kept
    # when something failed, since that is the run worth resuming.
    if not report.failures:
        pipeline.discard_parts(parts)
    if colours:
        # The same call the `colours` command makes: one path for one column, and it can be
        # run again later without redoing any of the measuring.
        named = report_io.recolour(output, _colours_from_file(colours))
        click.echo(f"Coloured {named} entity row(s) from {colours}")
    click.echo(f"Report written to {output} "
               f"({report.n_objects} object(s) in {report.seconds:.0f} s)")
    for object_id, error in report.failures.items():
        click.echo(f"FAILED  {object_id}: {error}")
    if report.failures:
        raise SystemExit(1)
    if meshes_to:
        from label_anatomy.analysis.meshes import GEOMETRY_FILENAME

        click.echo(f"Meshes written to {meshes_to}/<object>/{GEOMETRY_FILENAME}")


def _entity_list(names: list[str], limit: int = 10) -> str:
    """Entity names for one line of dry-run, kept to a line.

    A published segmentation can carry over a hundred structures per object, and printing
    all of them per folder buries the warnings and errors this command exists to show.
    The cross-object summary below still names every one.
    """
    if not names:
        return "(none)"
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])}  … +{len(names) - limit} more"


@cli.command(name="dry-run")
@click.argument("object_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--object-mask", default=None, metavar="NAME",
              help="Check this mask is present in every folder, and mark it with * below. "
                   "Left out, the masks are only listed, which is where you find the name "
                   "to pass to 'process'.")
def dry_run(object_dir: Path, object_mask: str | None) -> None:
    """Show which folders would be analysed, and what was ignored in each.

    Reads TIFF headers only, so it is fast even for a large batch. Without --object-mask this
    is the survey you run first, to see which masks the folders have; with one, it also checks
    that every folder has that mask. Exits non-zero if any folder that looks like an object
    cannot be analysed.
    """
    objects = find_object_dirs(object_dir)
    if not objects:
        click.echo(f"No object folders found under {object_dir}.")
        raise SystemExit(1)

    problems = 0
    entity_presence: dict[str, int] = {}
    for folder in objects:
        # Without a name, a missing object mask is not a problem to report: listing the
        # masks is what this command is for.
        d = inspect_object_dir(folder, object_mask)
        if object_mask is None:
            d = replace(d, errors=[e for e in d.errors if "No object mask named" not in e])
        rel = folder.relative_to(object_dir) if folder != object_dir else Path(folder.name)
        click.echo(f"\n{rel}")
        click.echo(f"  source  {d.source.name if d.source else '(none)'}"
                   f"   [{_stacked_mb(folder):,.1f} MB stacked]")
        for kind in ("label", "mask", "auto"):
            names = sorted(
                e.name + ("*" if e.name == d.object_mask_name else "")
                for e in d.entities.values() if e.kind == kind
            )
            if kind == "auto":
                # Named by the file alone, so what each one is gets read off its content
                # when the pixels are loaded. Nothing to show unless there are some.
                if names:
                    click.echo(f"  auto    {_entity_list(names)}"
                               f"   [label or mask, decided from content]")
                continue
            click.echo(f"  {kind + 's':7s} {_entity_list(names)}")
        for entity in d.entities.values():
            entity_presence[f"{entity.kind}:{entity.name}"] = (
                entity_presence.get(f"{entity.kind}:{entity.name}", 0) + 1
            )
        for path in d.unparsed:
            click.echo(f"  warn    ignored {path.name}: not <prefix>_<name>_label|labels|mask")
        for path, reason in d.rejected:
            click.echo(f"  warn    ignored {path.name}: {reason}")
        for error in d.errors:
            click.echo(f"  ERROR   {error}")
            problems += 1

    legend = "  (* = object mask)" if object_mask else ""
    click.echo(f"\n===== {len(objects)} object folder(s) ====={legend}")
    for key, count in sorted(entity_presence.items()):
        missing = "" if count == len(objects) else "   ← missing in some objects"
        click.echo(f"  {key:24s} {count}/{len(objects)}{missing}")
    if not object_mask:
        # An auto entity is a candidate too: naming one as the boundary is what settles
        # that it is a mask, so it belongs in the list you pick from.
        masks = sorted(key.split(":", 1)[1] for key in entity_presence
                       if key.startswith(("mask:", "auto:")))
        click.echo("\nPick the mask that bounds each object and pass it as --object-mask: "
                   f"{', '.join(masks) if masks else '(this batch has no mask entities)'}")
    peak = max((estimate_peak_gb(d) for d in objects), default=0.0)
    workers = pipeline.worker_count(None, len(objects), peak)
    click.echo(f"\nSuggested --max-workers: {workers}   "
               f"(largest object needs ~{peak:.1f} GB)")
    if problems:
        click.echo(f"{problems} problem(s) found.")
        raise SystemExit(1)


@cli.command()
@click.argument("object_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--out-dir", "-o", required=True,
              type=click.Path(file_okay=False, path_type=Path),
              help="Where to write <object>/geometry.parquet.")
@click.option("--object-mask", default=None, metavar="NAME",
              help="Mask that bounds each object, e.g. pm. Optional, as for 'process'.")
@click.option("--voxel-size-um", default=None, metavar="Z,Y,X",
              help="Voxel size in µm. Inferred from the source TIFF metadata when omitted.")
@click.option("--no-clip", "no_clip", is_flag=True,
              help="Measure outside the object mask too; entities are clipped to it by "
                   "default.")
@click.option("--geometry-as", "geometry_as", default=None, metavar="NAME=KIND,...",
              help="How each structure's surface is stored: mesh, ellipsoid or tube, "
                   "and/or skeleton for a centre line over it, combined with '+', e.g. "
                   "mito=mesh+skeleton. Unnamed structures are decided from their shape.")
@click.option("--contact-max-um", type=float, default=None, metavar="T",
              help="Gap threshold for the contact rows the 3D viewer groups by (default: 0.5).")
@click.option("--no-contacts", is_flag=True, help="Leave the contact rows out of the CSV.")
@_mesh_flags
def mesh(
    object_dir: Path, out_dir: Path, object_mask: str | None,
    voxel_size_um: str | None, no_clip: bool,
    geometry_as: str | None, contact_max_um: float | None,
    no_contacts: bool,
    mesh_smooth_sigma: float | None, mesh_step_size: int | None,
    mesh_target_reduction: float | None, mesh_level: float | None,
    mesh_workers: int | None, reuse_geometry: bool,
) -> None:
    """Write per-object geometry for the 3D views and the Blender export.

    Meshes and skeletons for a volume; for a plane, outlines and skeletons, since a plane
    has no surface to mesh.

    The same geometry `process --with-mesh` writes, for when you already have a report and
    only want the 3D files - or want to re-mesh with different settings.
    """
    from label_anatomy.analysis.meshes import mesh_rows_for_object, write_geometry

    objects = find_object_dirs(object_dir)
    if not objects:
        raise click.ClickException(f"No object folders found under {object_dir}.")
    _apply_analysis_env(object_mask, None, voxel_size_um, no_clip, False, contact_max_um,
                        None, None, geometry_as=geometry_as)
    _apply_mesh_env(None, mesh_smooth_sigma, mesh_step_size, mesh_target_reduction,
                    mesh_level, mesh_workers=mesh_workers,
                    reuse_geometry=reuse_geometry)
    options = mesh_options(**({"contact_max_um": None} if no_contacts else {}))

    for folder in objects:
        stack = load_object(folder)
        rows = mesh_rows_for_object(
            stack.volumes(),
            stack.kinds,
            stack.sample_size,
            object_id=stack.object_id,
            object_mask_name=stack.object_mask_name,
            options=options,
        )
        path = write_geometry(out_dir / stack.object_id, rows)
        # A plane is outlined, not meshed.
        drawable = "outline" if stack.spatial_dims == 2 else "mesh"
        drawn = sum(1 for row in rows if row.get(drawable))
        click.echo(f"{stack.object_id}: {drawn}/{len(rows)} "
                   f"{'outlined' if drawable == 'outline' else 'meshed'} → {path} "
                   f"({path.stat().st_size / 1024**2:.1f} MB)")


@cli.command()
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--port", type=int, default=8052, show_default=True)
@click.option("--no-browser", is_flag=True, help="Print the URL instead of opening it.")
def view(report: Path, port: int, no_browser: bool) -> None:
    """Open a report in the Anatomy report page.

    The page is one standalone HTML file and reads the parquet in the browser, so it needs
    no server at all: `label-anatomy page` prints where it is, and dropping a report
    onto it is the whole workflow. What this command adds is the geometry - it sits beside
    the report as its own parquet per object, and a page opened from a file:// URL cannot
    reach it. Serving both from one origin, and pointing the page at them, is all this is.
    """
    url = report_page.open_url(report.resolve(), port)
    click.echo(f"Serving {report} at {url}")
    click.echo("Ctrl-C to stop.")
    report_page.serve(report, port=port, open_browser=not no_browser)


@cli.command()
def page() -> None:
    """Print the path of the standalone report page.

    Open it in a browser and drop a report.parquet on it; add the geometry folder for the
    3D views. Copy it anywhere - it depends on nothing in this install.
    """
    click.echo(report_page.report_page())


if __name__ == "__main__":  # pragma: no cover
    cli()
