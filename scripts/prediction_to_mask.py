#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "zarr<3", "matplotlib"]
# ///
"""Cut a prediction (probabilities, distances, logits) in a zarr dataset into a mask zarr group beside it.

organella measures segmentations: integer label ids or a 0/1 mask. A prediction has no such
reading until someone picks a threshold, which is a decision about the data, so it is made here, with
a histogram to look at, and not inside `organella process`.

    uv run scripts/prediction_to_mask.py DATASET.zarr membrane --threshold-below 0
    uv run scripts/prediction_to_mask.py DATASET.zarr membrane       # histograms only, nothing written

Per level of the prediction zarr group (all levels unless --levels), a histogram is written to
DATASET/<prediction-zarr-group>_histograms/<level>.png, and the value range and foreground fraction are
printed. With --threshold-below or --threshold-above, the mask (uint8, 1 where the value is below / above
the threshold) is written as DATASET/<output-zarr-group> (default <prediction-zarr-group>_mask), a zarr
group with the same levels, chunking and multiscales metadata. Existing zarr groups are refused unless
--overwrite. Point the organella manifest at the new zarr group.
"""
import argparse
import copy
import logging
from pathlib import Path
from typing import Iterator

import matplotlib
import numpy as np
import zarr

matplotlib.use("Agg")
import matplotlib.pyplot as plt

logger = logging.getLogger("prediction_to_mask")

HISTOGRAM_BINS = 200


def get_and_check_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zarr_path", type=Path, metavar="zarr-path", help="Zarr directory")
    parser.add_argument(
        "prediction_group_name",
        metavar="prediction-zarr-group",
        help="zarr group holding the prediction, e.g. 'membrane'",
    )
    threshold_side = parser.add_mutually_exclusive_group()
    threshold_side.add_argument("--threshold-below", type=float, help="mask = value < this")
    threshold_side.add_argument("--threshold-above", type=float, help="mask = value > this")
    parser.add_argument("--levels", help="comma-separated, default every level in the multiscales metadata")
    parser.add_argument(
        "--output-zarr-group", help="name of the mask zarr group (default: <prediction-zarr-group>_mask)"
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    assert args.zarr_path.is_dir(), ("Zarr directory not found!", args.zarr_path)
    return args


def iterate_row_blocks(zarr_array: zarr.Array) -> Iterator[tuple[slice]]:
    """Slices of one chunk along the first axis, so a level is never fully in memory."""
    rows_per_block = zarr_array.chunks[0]
    n_rows = zarr_array.shape[0]
    for first_row in range(0, n_rows, rows_per_block):
        yield (slice(first_row, min(first_row + rows_per_block, n_rows)),)


def find_value_range(zarr_array: zarr.Array) -> tuple[float, float]:
    lowest, highest = np.inf, -np.inf
    for row_block in iterate_row_blocks(zarr_array):
        values = zarr_array[row_block]
        finite_values = values[np.isfinite(values)]
        if finite_values.size:
            lowest = min(lowest, float(finite_values.min()))
            highest = max(highest, float(finite_values.max()))
    return lowest, highest


def count_values_per_bin(zarr_array: zarr.Array, lowest: float, highest: float) -> np.ndarray:
    counts = np.zeros(HISTOGRAM_BINS, dtype=np.int64)
    for row_block in iterate_row_blocks(zarr_array):
        values = zarr_array[row_block]
        counts += np.histogram(values[np.isfinite(values)], bins=HISTOGRAM_BINS, range=(lowest, highest))[0]
    return counts


def save_histogram_plot(
    counts: np.ndarray, lowest: float, highest: float, threshold: float | None, title: str, path: Path
) -> None:
    bin_edges = np.linspace(lowest, highest, HISTOGRAM_BINS + 1)
    figure, axes = plt.subplots(figsize=(8, 4))
    axes.stairs(counts, bin_edges, fill=True)
    axes.set_yscale("log")
    axes.set_xlabel("value")
    axes.set_ylabel("voxels (log)")
    axes.set_title(title)
    if threshold is not None:
        axes.axvline(threshold, color="red", label=f"threshold {threshold:g}")
        axes.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def write_mask_level(
    prediction_level: zarr.Array, mask_level: zarr.Array, threshold: float, keep_below: bool
) -> int:
    """Write the mask of one level, and return how many voxels are foreground."""
    n_foreground = 0
    for row_block in iterate_row_blocks(prediction_level):
        values = prediction_level[row_block]
        is_foreground = (values < threshold) if keep_below else (values > threshold)
        mask_level[row_block] = is_foreground.astype(np.uint8)
        n_foreground += int(is_foreground.sum())
    return n_foreground


def read_level_names(prediction_zarr_group: zarr.Group) -> list[str]:
    multiscales_levels = prediction_zarr_group.attrs["multiscales"][0]["datasets"]
    return [level["path"] for level in multiscales_levels]


def create_mask_zarr_group(
    zarr_dataset: zarr.Group, prediction_zarr_group: zarr.Group, mask_name: str, overwrite: bool
) -> zarr.Group:
    """An empty zarr group that describes the same pyramid as the prediction, under its own name."""
    attributes_to_write = copy.deepcopy(prediction_zarr_group.attrs.asdict())
    attributes_to_write["multiscales"][0]["name"] = mask_name
    mask_zarr_group = zarr_dataset.require_group(mask_name, overwrite=overwrite)
    mask_zarr_group.attrs.update(attributes_to_write)
    return mask_zarr_group


def main() -> None:
    args = get_and_check_args()
    zarr_dataset = zarr.open_group(str(args.zarr_path), mode="a")
    prediction_zarr_group = zarr_dataset[args.prediction_group_name]
    level_names = args.levels.split(",") if args.levels else read_level_names(prediction_zarr_group)

    threshold = args.threshold_below if args.threshold_below is not None else args.threshold_above
    keep_below = args.threshold_below is not None
    mask_name = args.output_zarr_group or f"{args.prediction_group_name}_mask"
    assert threshold is None or args.overwrite or mask_name not in zarr_dataset, (
        "already exists, use --overwrite or --output-zarr-group",
        mask_name,
    )

    histogram_directory = args.zarr_path / f"{args.prediction_group_name}_histograms"
    histogram_directory.mkdir(exist_ok=True)
    mask_zarr_group = None
    if threshold is not None:
        mask_zarr_group = create_mask_zarr_group(zarr_dataset, prediction_zarr_group, mask_name, args.overwrite)

    for level_name in level_names:
        prediction_level = prediction_zarr_group[level_name]
        lowest, highest = find_value_range(prediction_level)
        counts = count_values_per_bin(prediction_level, lowest, highest)
        title = f"{args.prediction_group_name}/{level_name}"
        save_histogram_plot(counts, lowest, highest, threshold, title, histogram_directory / f"{level_name}.png")
        message = f"{title}: {prediction_level.dtype}, range {lowest:.4g}..{highest:.4g}"

        if threshold is not None:
            mask_level = mask_zarr_group.create_dataset(
                level_name,
                shape=prediction_level.shape,
                chunks=prediction_level.chunks,
                dtype=np.uint8,
                overwrite=args.overwrite,
            )
            n_foreground = write_mask_level(prediction_level, mask_level, threshold, keep_below)
            message += f", {n_foreground / prediction_level.size:.1%} foreground -> {mask_name}/{level_name}"
        logger.info(message)
    logger.info("histograms in %s", histogram_directory)


if __name__ == "__main__":
    main()
