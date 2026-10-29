from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import click
import dask

from organella.app.process import process
from organella.cli import main
from organella.cli.options import (
    distance_options, geometry_into, geometry_options, measure_options, report_options, settings_from, sink_to, source_from,
    source_options, statistics_options,
)
from organella.measure import DEFAULT, Statistics
from organella.sinks.null import NullSink
from organella.sinks.parts import ParquetParts


def parts_for(output: Path, resume: bool) -> ParquetParts:
    """Each object's rows are always kept as it finishes, since a run that dies cannot know it will be resumed; --resume is
    what makes a later run read them back."""
    return ParquetParts(output.with_name(output.stem + "_parts"), reuse=resume)


threads_option = click.option("--threads", type=click.IntRange(min=1), help="Most threads the run uses. Default: one per core.")


def _finish(failures: dict[str, str]) -> None:
    for sample_id, reason in failures.items():
        click.echo(f"failed {sample_id}: {reason}", err=True)
    raise SystemExit(1 if failures else 0)


@main.command("process")
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(path_type=Path), help="Where the report goes.")
@click.option("--with-mesh", is_flag=True, help="Write the 3D geometry the viewer draws, beside the report.")
@click.option("--mesh-dir", type=click.Path(path_type=Path), help="Where the geometry goes. Default: <output>_meshes.")
@click.option("--resume", is_flag=True, help="Skip objects an interrupted run already measured under the same settings; each object's rows are kept in <output>_parts/ as it finishes.")
@threads_option
@source_options
@distance_options
@measure_options
@statistics_options
@geometry_options
@report_options
def process_command(input: Path, output: Path, with_mesh: bool, mesh_dir: Path | None, resume: bool, threads: int | None, **options) -> None:
    """Measure a folder of objects, or a Zarr or N5 store described by a manifest, and write one report."""
    geometry = geometry_into(mesh_dir or output.with_name(output.stem + "_meshes"), options) if with_mesh else None
    measurements = (Statistics(),) if options["no_instances"] else DEFAULT
    with dask.config.set(num_workers=threads) if threads else nullcontext():
        failures = process(source_from(input, options), measurements, sink_to(output, input, options),
                           settings_from(options, geometry=with_mesh), geometry, parts_for(output, resume))
    _finish(failures)


@main.command("mesh")
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--out-dir", required=True, type=click.Path(path_type=Path), help="Where <object>/geometry.parquet goes.")
@threads_option
@source_options
@measure_options
@geometry_options
def mesh_command(input: Path, out_dir: Path, threads: int | None, **options) -> None:
    """Write the 3D geometry alone, for a report you already have: it measures only what the geometry carries."""
    with dask.config.set(num_workers=threads) if threads else nullcontext():
        failures = process(source_from(input, options), (Statistics(),), NullSink(), settings_from(options, geometry=True),
                           geometry_into(out_dir, options))
    _finish(failures)
