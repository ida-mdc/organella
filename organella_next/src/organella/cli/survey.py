from __future__ import annotations

from pathlib import Path

import click

from organella.cli import main
from organella.cli.options import _names, _voxel_size
from organella.core import Kind, normalised
from organella.sources.survey import survey


def _list(names: list[str], mask: str | None) -> str:
    return ", ".join(n + ("*" if n == mask else "") for n in names) or "-"


@main.command("dry-run")
@click.argument("input", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-p", "--paths", "groups", multiple=True, help="Subfolder to import as a group, repeatable.")
@click.option("--object-mask", help="With it, every object is checked to have it; without, the masks are listed to pick from.")
@click.option("--voxel-size-um", callback=_voxel_size, help="Z,Y,X in µm, as you would give to process.")
def dry_run_command(input: Path, groups, object_mask: str | None, voxel_size_um) -> None:
    """Show what would be analysed, reading file headers only, so it is fast for a large batch.

    Exits 1 if any object cannot be analysed.
    """
    objects = survey(input, groups, object_mask, voxel_size_um is not None)
    if not objects:
        click.echo(f"No object folders found under {input}.")
        raise SystemExit(1)
    presence: dict[str, int] = {}
    problems = 0
    for entry in objects:
        click.echo(f"\n{entry.name}")
        click.echo(f"  source  {entry.source or '(none)'}   [{entry.voxels / 1e6:,.1f} Mvoxels]")
        for kind, title in ((Kind.LABEL, "labels"), (Kind.MASK, "masks")):
            click.echo(f"  {title:7s} {_list(entry.names(kind), object_mask)}")
        if entry.names(None):
            click.echo(f"  auto    {_list(entry.names(None), object_mask)}   [label or mask, decided from content]")
        for name, (kind, _, _) in entry.entities.items():
            key = f"{kind.value if kind else 'auto'}:{name}"
            presence[key] = presence.get(key, 0) + 1
        for warning in entry.warnings:
            click.echo(f"  warn    {warning}")
        for error in entry.errors:
            click.echo(f"  ERROR   {error}")
            problems += 1
    click.echo(f"\n===== {len(objects)} object folder(s) ====={'  (* = object mask)' if object_mask else ''}")
    for key, count in sorted(presence.items()):
        click.echo(f"  {key:24s} {count}/{len(objects)}{'' if count == len(objects) else '   <- missing in some objects'}")
    if not object_mask:
        masks = sorted(k.split(":", 1)[1] for k in presence if k.startswith(("mask:", "auto:")))
        click.echo(f"\nPick the mask that bounds each object and pass it as --object-mask: {', '.join(masks) or '(this batch has no mask structures)'}")
    if problems:
        click.echo(f"{problems} problem(s) found.")
        raise SystemExit(1)
