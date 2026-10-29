from __future__ import annotations

from pathlib import Path

import click

from organella.cli import main
from organella.sinks.colours import colours_from_file


@main.command("view")
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--port", default=8052, show_default=True)
@click.option("--no-browser", is_flag=True)
def view_command(report: Path, port: int, no_browser: bool) -> None:
    """Serve a report, its geometry and the page, and open it."""
    from organella.viewer.server import serve

    serve(report, port, open_browser=not no_browser)


@main.command("page")
@click.argument("output", type=click.Path(dir_okay=False, path_type=Path), default="organella_report.html")
def page_command(output: Path) -> None:
    """Write the standalone report page, one file that opens a report dropped on it, and say where it is."""
    from organella.viewer.build import build

    output.write_text(build())
    click.echo(output.resolve())


@main.command("colours")
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("palette", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def colours_command(report: Path, palette: Path) -> None:
    """Recolour the structures of an existing report from a JSON palette, without measuring anything again."""
    from organella.report.file import recolour

    click.echo(f"{recolour(report, colours_from_file(palette))} structure(s) coloured in {report}")


@main.command("merge")
@click.argument("reports", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Where the merged report goes.")
def merge_command(reports: tuple[Path, ...], output: Path) -> None:
    """Join reports of different objects, measured in separate runs, into one. An object may be in only one of them."""
    from organella.report.file import merge_reports

    click.echo(f"{merge_reports(reports, output)} object(s) from {len(reports)} report(s) written to {output}")


@main.command("describe")
@click.argument("report", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("text")
def describe_command(report: Path, text: str) -> None:
    """Add a description to an existing report: what the data is and who it credits, shown above its first section."""
    from organella.report.file import describe

    describe(report, text)
    click.echo(f"description written to {report}")


from organella.cells.command import cells_command  # noqa: E402,F401
