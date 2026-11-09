from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path

import click
import dask

from organella.app.process import process
from organella.cli import main
from organella.geometry import workers
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
mesh_workers_option = click.option("--mesh-workers", type=click.IntRange(min=0), help="Worker processes that mesh and simplify surfaces, which threads "
                                   "cannot do in parallel. Default: one per thread; 0 does it all in this process.")


scheduler_option = click.option("--scheduler", metavar="ADDRESS", help="Run the work on a dask.distributed cluster whose scheduler is at "
                                "ADDRESS (tcp://host:8786) instead of on this machine's threads. Needs the 'distributed' extra: "
                                "uv sync --extra distributed.")


@contextmanager
def _scheduler(address: str | None):
    """The run's dask work goes to the scheduler at ``address``, if there is one, and the connection is closed after."""
    if not address:
        yield
        return
    try:
        from distributed import Client
    except ImportError:
        raise click.UsageError("--scheduler needs the distributed package: uv sync --extra distributed") from None
    with Client(address) as client:
        click.echo(f"running on {len(client.scheduler_info()['workers'])} worker(s) of {address}")
        yield


@contextmanager
def _mesh_workers(count: int | None, threads: int | None):
    """Surfaces are simplified in ``count`` worker processes (by default as many as there are threads) for the run."""
    workers.configure(count if count is not None else (threads or workers.available_cores()))
    try:
        yield
    finally:
        workers.shutdown()


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
@mesh_workers_option
@scheduler_option
@source_options
@distance_options
@measure_options
@statistics_options
@geometry_options
@report_options
def process_command(input: Path, output: Path, with_mesh: bool, mesh_dir: Path | None, resume: bool, threads: int | None, mesh_workers: int | None, scheduler: str | None,
                    **options) -> None:
    """Measure a folder of objects, or a Zarr or N5 store described by a manifest, and write one report."""
    geometry = geometry_into(mesh_dir or output.with_name(output.stem + "_meshes"), options) if with_mesh else None
    measurements = (Statistics(),) if options["no_instances"] else DEFAULT
    with dask.config.set(num_workers=threads) if threads else nullcontext(), _mesh_workers(mesh_workers, threads), _scheduler(scheduler):
        failures = process(source_from(input, options), measurements, sink_to(output, input, options),
                           settings_from(options, geometry=with_mesh), geometry, parts_for(output, resume))
    _finish(failures)


@main.command("multires")
@click.argument("geometry", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-o", "--out-dir", required=True, type=click.Path(path_type=Path), help="Where the converted <object>/ folders go. Not the folder they are made from.")
@click.option("--min-vertices", type=click.IntRange(min=1000), default=6_000, show_default=True, help="Meshes with fewer vertices keep their single payload.")
@click.option("--fragment-vertices", type=click.IntRange(min=500), default=12_000, show_default=True, help="Vertices a fragment aims at.")
@click.option("--coarsest-vertices", type=click.IntRange(min=100), default=2_000, show_default=True, help="Where the levels stop.")
@threads_option
@mesh_workers_option
def multires_command(geometry: Path, out_dir: Path, min_vertices: int, fragment_vertices: int, coarsest_vertices: int,
                     threads: int | None, mesh_workers: int | None) -> None:
    """Give the big meshes of existing geometry levels of detail the viewer loads by range, without meshing again.

    GEOMETRY is a <report>_meshes folder. Its geometry is left as it is; the converted copy is smaller to open and
    holds the levels in meshes.bin beside each geometry.parquet.
    """
    from organella.geometry.convert import convert_folder

    with _mesh_workers(mesh_workers, threads):
        made = convert_folder(geometry, out_dir, min_vertices=min_vertices, fragment_vertices=fragment_vertices,
                              coarsest_vertices=coarsest_vertices)
    for name, count in made.items():
        click.echo(f"{name}: {count} mesh(es) given levels of detail")
    click.echo(f"written to {out_dir}")


@main.command("mesh")
@click.argument("input", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--out-dir", required=True, type=click.Path(path_type=Path), help="Where <object>/geometry.parquet goes.")
@threads_option
@mesh_workers_option
@scheduler_option
@source_options
@measure_options
@geometry_options
def mesh_command(input: Path, out_dir: Path, threads: int | None, mesh_workers: int | None, scheduler: str | None, **options) -> None:
    """Write the 3D geometry alone, for a report you already have: it measures only what the geometry carries."""
    with dask.config.set(num_workers=threads) if threads else nullcontext(), _mesh_workers(mesh_workers, threads), _scheduler(scheduler):
        failures = process(source_from(input, options), (Statistics(),), NullSink(), settings_from(options, geometry=True),
                           geometry_into(out_dir, options))
    _finish(failures)
