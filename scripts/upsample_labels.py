#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "zarr<3"]
# ///
"""Put a label or mask array of coarser or offset grid onto the grid of another array in the same zarr dataset.

organella needs every entity of an object on one grid: the same shape and the same voxel size. When
one array was predicted at a coarser scale (nuclei at 32 nm beside organelles at 16 nm), this writes
a copy on the reference grid by nearest neighbour, so label ids stay intact, as a new zarr group beside it.

    uv run scripts/upsample_labels.py DATASET.zarr nuclei --reference-zarr-group-name mito

For every level of the reference zarr group, the finest level of the labels zarr group that is not finer
than it is read nearest-neighbour at the position of each reference voxel's centre, using the translations
in both zarr groups' multiscales metadata. That repeats voxels where the labels are coarser, copies where
the sizes are equal, and shifts by the offset between the two zarr groups; label ids stay intact, and
positions outside the labels are 0. The result is DATASET/<output-zarr-group> (default
<labels-zarr-group>_upsample_to_<reference-zarr-group-name>), with the reference's multiscales metadata,
so the manifest can name it like any other array. Existing zarr groups are refused unless --overwrite.
The log goes to stderr; --debug adds the per-level index ranges and per-slab progress.
"""
import argparse
import copy
import logging
from pathlib import Path
from typing import NamedTuple

import numpy as np
import zarr

logger = logging.getLogger("upsample_labels")

AXIS_NAMES = "zyx"


class LevelGrid(NamedTuple):
    """Where the voxels of one pyramid level lie, per axis (z, y, x), in nanometres."""

    voxel_size_nm: np.ndarray
    first_voxel_center_nm: np.ndarray


def get_and_check_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zarr_path", type=Path, metavar="zarr-path", help="Zarr directory")
    parser.add_argument("labels_group_name", metavar="labels-group", help="zarr group to bring onto the reference grid")
    parser.add_argument("--reference-group-name", required=True, help="zarr group whose grid to match")
    parser.add_argument(
        "--output-zarr-group", help="name of the new zarr group (default: <labels>_upsample_to_<reference>)"
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--debug", action="store_true", help="log per-level index ranges and per-slab progress")
    args = parser.parse_args()
    log_format = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format=log_format)
    assert args.zarr_path.is_dir(), ("Zarr directory not found!", args.zarr_path)
    return args


def read_level_grids(zarr_group: zarr.Group) -> dict[str, LevelGrid]:
    """The grid of every pyramid level of ``zarr_group``, by level name, from its multiscales metadata."""
    grids = {}
    for level in zarr_group.attrs["multiscales"][0]["datasets"]:
        transformations = {step["type"]: step for step in level["coordinateTransformations"]}
        voxel_size_nm = np.array(transformations["scale"]["scale"], dtype=float)
        translation_nm = transformations.get("translation", {}).get("translation", [0.0] * len(voxel_size_nm))
        grids[level["path"]] = LevelGrid(voxel_size_nm, np.array(translation_nm, dtype=float))
    return grids


def choose_labels_level(labels_grids: dict[str, LevelGrid], reference_grid: LevelGrid) -> str:
    """The finest level of the labels whose voxels are not smaller than the reference's on any axis."""
    usable_levels = [
        (float(grid.voxel_size_nm[0]), level_name)
        for level_name, grid in labels_grids.items()
        if np.all(grid.voxel_size_nm >= reference_grid.voxel_size_nm - 1e-6)
    ]
    assert usable_levels, ("every labels level is finer than the reference", reference_grid.voxel_size_nm.tolist())
    finest_voxel_size, finest_level_name = min(usable_levels)
    return finest_level_name


def find_label_voxel_per_reference_voxel(
    labels_grid: LevelGrid, reference_grid: LevelGrid, reference_shape: tuple[int, ...]
) -> list[np.ndarray]:
    """Per axis, the index of the labels voxel lying at the centre of each reference voxel.

    Looking each reference voxel up by position, and not repeating voxels, is what makes an offset between
    the two grids come out right. Indices outside the labels array are not clipped; they are -1 or too large.
    """
    voxel_indices = []
    for axis, reference_length in enumerate(reference_shape):
        reference_voxel_numbers = np.arange(reference_length)
        reference_centers_nm = (
            reference_grid.first_voxel_center_nm[axis] + reference_grid.voxel_size_nm[axis] * reference_voxel_numbers
        )
        distance_from_first_label_voxel = reference_centers_nm - labels_grid.first_voxel_center_nm[axis]
        nearest_label_voxel = np.floor(distance_from_first_label_voxel / labels_grid.voxel_size_nm[axis] + 0.5)
        voxel_indices.append(nearest_label_voxel.astype(np.int64))
    return voxel_indices


def read_reference_rows_from_labels(
    labels_level: zarr.Array,
    voxel_indices: list[np.ndarray],
    row_range: tuple[int, int],
    reference_shape: tuple[int, ...],
) -> np.ndarray:
    """The z rows ``row_range`` of the reference grid, taken nearest-neighbour from the labels; 0 outside them."""
    first_row, end_row = row_range
    z_indices = voxel_indices[0][first_row:end_row]
    y_indices = voxel_indices[1]
    x_indices = voxel_indices[2]
    z_inside = (z_indices >= 0) & (z_indices < labels_level.shape[0])
    y_inside = (y_indices >= 0) & (y_indices < labels_level.shape[1])
    x_inside = (x_indices >= 0) & (x_indices < labels_level.shape[2])

    rows = np.zeros((end_row - first_row, *reference_shape[1:]), dtype=labels_level.dtype)
    if not (z_inside.any() and y_inside.any() and x_inside.any()):
        return rows

    needed_z = z_indices[z_inside]
    first_needed_z = needed_z.min()
    labels_block = np.asarray(labels_level[first_needed_z : needed_z.max() + 1])
    picked_labels = labels_block[np.ix_(needed_z - first_needed_z, y_indices[y_inside], x_indices[x_inside])]
    rows[np.ix_(z_inside, y_inside, x_inside)] = picked_labels
    return rows


def copy_multiscales_metadata(
    reference_zarr_group: zarr.Group, output_zarr_group: zarr.Group, output_name: str
) -> None:
    """Give the output the reference's pyramid description, under its own name."""
    attributes_to_write = copy.deepcopy(reference_zarr_group.attrs.asdict())
    attributes_to_write["multiscales"][0]["name"] = output_name
    output_zarr_group.attrs.update(attributes_to_write)


def debug_log_grids(zarr_group_name: str, grids: dict[str, LevelGrid]) -> None:
    for level_name, grid in grids.items():
        logger.debug(
            "%s/%s: voxel size %s nm, first voxel center %s nm",
            zarr_group_name,
            level_name,
            grid.voxel_size_nm.tolist(),
            grid.first_voxel_center_nm.tolist(),
        )


def log_label_voxels_used(level_name: str, voxel_indices: list[np.ndarray], labels_shape: tuple[int, ...]) -> None:
    for axis_name, indices, labels_length in zip(AXIS_NAMES, voxel_indices, labels_shape):
        n_outside = int(((indices < 0) | (indices >= labels_length)).sum())
        logger.debug(
            "%s: axis %s uses labels voxels %d..%d (%d of %d reference voxels lie outside the labels)",
            level_name, axis_name, indices.min(), indices.max(), n_outside, len(indices),
        )  # fmt: skip


def upsample_level(
    labels_level: zarr.Array,
    labels_grid: LevelGrid,
    reference_level: zarr.Array,
    reference_grid: LevelGrid,
    output_zarr_group: zarr.Group,
    level_name: str,
    overwrite: bool,
) -> None:
    """Write ``level_name`` of the output zarr group: the labels on the grid of ``reference_level``."""
    voxel_indices = find_label_voxel_per_reference_voxel(labels_grid, reference_grid, reference_level.shape)
    log_label_voxels_used(level_name, voxel_indices, labels_level.shape)
    output_level = output_zarr_group.create_dataset(
        level_name,
        shape=reference_level.shape,
        chunks=reference_level.chunks,
        dtype=labels_level.dtype,
        overwrite=overwrite,
    )
    rows_per_step = reference_level.chunks[0]
    n_rows = reference_level.shape[0]
    for first_row in range(0, n_rows, rows_per_step):
        end_row = min(first_row + rows_per_step, n_rows)
        output_level[first_row:end_row] = read_reference_rows_from_labels(
            labels_level, voxel_indices, (first_row, end_row), reference_level.shape
        )
        logger.debug("%s: wrote rows %d:%d of %d", level_name, first_row, end_row, n_rows)


def main() -> None:
    args = get_and_check_args()
    zarr_dataset = zarr.open_group(str(args.zarr_path), mode="a")
    labels_zarr_group = zarr_dataset[args.labels_group_name]
    reference_zarr_group = zarr_dataset[args.reference_group_name]
    default_output_name = f"{args.labels_group_name}_upsample_to_{args.reference_group_name}"
    output_name = args.output_zarr_group or default_output_name
    assert args.overwrite or output_name not in zarr_dataset, (
        "already exists, use --overwrite or --output-zarr-group",
        output_name,
    )
    logger.info(
        "upsampling %s onto the grid of %s -> %s",
        args.labels_group_name,
        args.reference_group_name,
        output_name,
    )

    labels_grids = read_level_grids(labels_zarr_group)
    reference_grids = read_level_grids(reference_zarr_group)
    debug_log_grids(args.labels_group_name, labels_grids)
    debug_log_grids(args.reference_group_name, reference_grids)

    output_zarr_group = zarr_dataset.require_group(output_name, overwrite=args.overwrite)
    copy_multiscales_metadata(reference_zarr_group, output_zarr_group, output_name)

    for level_name, reference_grid in reference_grids.items():
        labels_level_name = choose_labels_level(labels_grids, reference_grid)
        labels_level = labels_zarr_group[labels_level_name]
        reference_level = reference_zarr_group[level_name]
        logger.info(
            "%s: %s/%s %s -> %s/%s %s",
            level_name, args.labels_group_name, labels_level_name, labels_level.shape,
            output_name, level_name, reference_level.shape,
        )  # fmt: skip
        upsample_level(
            labels_level,
            labels_grids[labels_level_name],
            reference_level,
            reference_grid,
            output_zarr_group,
            level_name,
            args.overwrite,
        )


if __name__ == "__main__":
    main()
