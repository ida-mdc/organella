"""Give geometry that is already written levels of detail, from the meshes it holds and nothing else.

No voxels and no measuring: each big mesh is decoded, decimated into levels and fragmented, its fragments go to a
``meshes.bin`` beside the new ``geometry.parquet``, and its ``surface`` becomes the coarsest level. The first level is the
mesh as it was stored, already decimated and quantised, so nothing finer than that comes back: only meshing again does.
"""
from __future__ import annotations

from pathlib import Path

import polars as pl

from organella.geometry import multires
from organella.geometry.writer import COLUMNS, FILENAME, SETTINGS_FILENAME, _BLOBS, _INTS, _TEXT, write_parquet


def convert_file(source: Path, destination: Path, min_vertices: int = multires.MIN_VERTICES,
                 fragment_vertices: int = multires.FRAGMENT_VERTICES, coarsest_vertices: int = multires.COARSEST_VERTICES) -> int:
    """One object's geometry.parquet rewritten with levels of detail for its big meshes; how many it gave them to."""
    rows = pl.read_parquet(source).to_dicts()
    done = multires.give_levels(rows, destination.parent, min_vertices, fragment_vertices, coarsest_vertices)
    write_parquet(destination, rows)
    return done


def convert_folder(source: Path, destination: Path, **options) -> dict[str, int]:
    """Every ``<object>/geometry.parquet`` under ``source``, rewritten under ``destination``; meshes given levels per object.

    The source is never touched: it is the only copy of geometry that took hours to make.
    """
    if source.resolve() == destination.resolve():
        raise ValueError("the converted geometry has to go somewhere else than the geometry it is made from")
    made = {}
    for file in sorted(source.glob(f"*/{FILENAME}")):
        target = destination / file.parent.name / FILENAME
        made[file.parent.name] = convert_file(file, target, **options)
        settings = file.parent / SETTINGS_FILENAME
        if settings.exists():
            (target.parent / SETTINGS_FILENAME).write_bytes(settings.read_bytes())
    return made
