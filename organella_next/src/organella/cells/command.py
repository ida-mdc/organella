from __future__ import annotations

from pathlib import Path

import click

from organella.cli import main


@main.command("cells")
@click.argument("store")
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--source", required=True, metavar="ARRAY", help="The image array, without a scale level, e.g. em/fibsem-uint16.")
@click.option("--entity", "entities", multiple=True, required=True, metavar="NAME=ARRAY",
              help="A structure to measure in every cell, e.g. mito=labels/mito_seg (repeatable), without a scale level.")
@click.option("--instances", metavar="NAMES", help="Entities that are sets of instances, comma separated; labels in every cell.")
@click.option("--masks", "mask_names", metavar="NAMES", help="Entities that are one region each, comma separated.")
@click.option("--membrane", required=True, metavar="ARRAY", help="The plasma membrane, e.g. labels/pm_seg. A thin surface is enough.")
@click.option("--seed", required=True, metavar="ARRAY", help="One blob per cell, e.g. labels/nucleus_seg.")
@click.option("--outside", metavar="ARRAY", help="The space around the cells, e.g. labels/ecs_seg. Optional.")
@click.option("--scale", required=True, metavar="LEVEL", help="The scale level every cell is measured at, e.g. s3.")
@click.option("--mask-scale", metavar="LEVEL", help="A coarser level to derive the cells at (default: --scale); it is the one that has to fit in memory.")
@click.option("--name", "object_name", default="cell", show_default=True, help="What the derived mask is called.")
@click.option("--min-seed-um3", type=float, default=40.0, show_default=True, help="Smallest seed that counts as a cell, in µm³.")
@click.option("--max-membrane-um", type=float, default=0.0, show_default=True,
              help="Nothing further than this from a membrane is part of a cell. Off by default: it clips the middle of a cut-off cell.")
@click.option("--prefix", default="cell", show_default=True, help="Folder names are <prefix>_01, <prefix>_02, ...")
@click.option("--wall-voxels", type=int, default=3, show_default=True, help="How far the membrane is thickened, in voxels at --mask-scale.")
@click.option("--drop-edge", is_flag=True, help="Leave out cells the volume cuts off. They are kept, and marked in their manifest, by default.")
def cells_command(store, out_dir, source, entities, instances, mask_names, membrane, seed, outside, scale, mask_scale, object_name,
                  min_seed_um3, max_membrane_um, prefix, wall_voxels, drop_edge) -> None:
    """Cut the cells of a chunked volume apart, one object folder each.

    STORE is an N5 or Zarr store, local or on S3. The mask is derived, not published: the membrane and the extracellular
    space bound it, a nucleus seeds it, and touching cells are cut apart by a watershed.
    """
    from organella.cells.cut import cut_cells

    named = {}
    for item in entities:
        key, separator, array = item.partition("=")
        if not separator or not key.strip() or not array.strip():
            raise click.ClickException(f"--entity needs NAME=ARRAY, got {item!r}")
        named[key.strip()] = array.strip().strip("/")
    try:
        cut_cells(store, out_dir, source=source, entities=named, instances=instances, mask_names=mask_names, membrane=membrane,
                  seed=seed, outside=outside, scale=scale, mask_scale=mask_scale, object_name=object_name,
                  min_seed_um3=min_seed_um3, max_membrane_um=max_membrane_um or None, prefix=prefix, wall_voxels=wall_voxels,
                  drop_edge=drop_edge, say=click.echo)
    except (ValueError, FileNotFoundError) as error:
        raise click.ClickException(str(error)) from error
