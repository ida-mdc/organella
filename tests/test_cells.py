"""Cutting cells out of a volume from the membrane around them."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from organella.cli import cli
from organella.config import RunConfig
from organella.measure import load_object
from organella.measure.cells import (
    Cell,
    cells_at_scale,
    derive_cell_labels,
    write_cell_folder,
)
from organella.measure.discovery import inspect_object_dir

VOXEL = (1.0, 1.0, 1.0)


def _ball(shape, centre, radius):
    grid = np.indices(shape).astype(float)
    return sum((g - c) ** 2 for g, c in zip(grid, centre)) <= radius ** 2


def _shell(inside):
    """The one-voxel surface of a region - open wherever the region runs off the array."""
    from scipy import ndimage as ndi

    return inside & ~ndi.binary_erosion(inside, border_value=1)


def _two_cells(shape=(40, 60, 90)):
    """Two cells with a gap of empty space between them, each with a nucleus and a membrane."""
    a = _ball(shape, (20, 30, 25), 14)
    b = _ball(shape, (20, 30, 65), 14)
    membrane = _shell(a) | _shell(b)
    outside = ~(a | b)
    seed = _ball(shape, (20, 30, 25), 6) | _ball(shape, (20, 30, 65), 6)
    return a, b, membrane, outside, seed


def test_each_nucleus_gets_the_cell_around_it():
    a, b, membrane, outside, seed = _two_cells()

    labels = derive_cell_labels(membrane, outside, seed, VOXEL, min_seed_um3=10)

    assert sorted(np.unique(labels)) == [0, 1, 2]
    one, two = (labels == 1), (labels == 2)
    # Whichever is first, each label is one of the two cells and nothing else.
    for found in (one, two):
        assert (found & a).sum() == found.sum() or (found & b).sum() == found.sum()
    assert one.sum() + two.sum() > 0.9 * (a.sum() + b.sum())
    assert not (labels[outside] > 0).any(), "nothing the outside class holds is a cell"


def test_a_cell_cut_by_the_edge_of_the_volume_is_still_filled():
    shape = (40, 60, 60)
    whole = _ball(shape, (4, 30, 30), 18)          # centre near the z=0 face: cut off there
    membrane = _shell(whole)
    membrane[0] = False                             # no membrane where the volume ends
    seed = _ball(shape, (6, 30, 30), 7)

    labels = derive_cell_labels(membrane, ~whole, seed, VOXEL, min_seed_um3=10)

    assert (labels > 0)[0, 30, 30], "the face is the end of the array, not a way out"
    assert (labels > 0).sum() > 0.9 * whole.sum()


def test_cells_touching_with_no_membrane_between_them_are_cut_apart():
    shape = (40, 60, 90)
    left = _ball(shape, (20, 30, 33), 14)
    right = _ball(shape, (20, 30, 55), 14)
    both = left | right                              # one shell round the pair: no wall between
    seed = _ball(shape, (20, 30, 33), 6) | _ball(shape, (20, 30, 55), 6)

    labels = derive_cell_labels(_shell(both), ~both, seed, VOXEL, min_seed_um3=10)

    assert sorted(np.unique(labels)) == [0, 1, 2]
    assert labels[20, 30, 33] != labels[20, 30, 55]
    # The cut is at the neck, midway between the nuclei.
    neck = np.argwhere((labels == labels[20, 30, 33]))[:, 2].max()
    assert 40 <= neck <= 48


def test_a_seed_too_small_to_be_a_nucleus_is_not_a_cell():
    a, b, membrane, outside, seed = _two_cells()
    seed = seed | _ball(a.shape, (20, 30, 25), 1) | _ball(a.shape, (5, 5, 5), 1.5)

    labels = derive_cell_labels(membrane, outside, seed, VOXEL, min_seed_um3=50)

    assert labels.max() == 2


def test_no_seed_big_enough_gives_no_cells():
    a, b, membrane, outside, seed = _two_cells()

    assert derive_cell_labels(membrane, outside, seed, VOXEL, min_seed_um3=1e9).max() == 0


def test_a_cell_is_carried_up_to_the_working_scale_by_repetition():
    labels = np.zeros((4, 4, 4), np.uint16)
    labels[1:3, 1:3, 0:2] = 1

    [cell] = cells_at_scale(labels, (1.0, 1.0, 1.0), (0.5, 0.5, 0.5), (8, 8, 8))

    assert cell.window == (slice(2, 6), slice(2, 6), slice(0, 4))
    assert cell.mask.shape == (4, 4, 4) and cell.mask.all()
    assert cell.touches_edge == "x"
    assert cell.volume_um3 == pytest.approx(64 * 0.125)


def test_the_two_scales_need_not_be_a_whole_ratio_apart():
    """A pyramid whose z is downsampled by a different factor than x and y is common."""
    labels = np.zeros((4, 6, 6), np.uint16)
    labels[1:3, 1:5, 1:5] = 1

    [cell] = cells_at_scale(labels, (2.4, 1.0, 1.0), (1.0, 0.5, 0.5), (12, 12, 12))

    # 2 coarse z voxels of 2.4 are 4.8 µm: 4 or 5 working voxels of 1.0, not an error.
    assert cell.window[0].stop - cell.window[0].start in (4, 5)
    assert cell.mask.all()
    assert cell.volume_um3 == pytest.approx(cell.mask.sum() * 0.25)


def test_dropping_the_cut_off_cells_keeps_the_whole_ones():
    labels = np.zeros((10, 10, 10), np.uint16)
    labels[0:4, 3:6, 3:6] = 1
    labels[5:8, 3:6, 3:6] = 2

    kept = cells_at_scale(labels, VOXEL, VOXEL, (10, 10, 10), drop_edge=True)

    assert [c.number for c in kept] == [2]


# ── the folder it writes is an ordinary object ─────────────────────────

zarr = pytest.importorskip("zarr")


def _store(tmp_path: Path) -> Path:
    """A small N5 pyramid, s0 and a half-size s1, with two cells in it."""

    a, b, membrane, outside, seed = _two_cells()
    body = (a | b)
    path = tmp_path / "cells.n5"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        from zarr.n5 import N5Store

        root = zarr.open(N5Store(str(path)), mode="w")
        spots = np.zeros(a.shape, np.uint16)
        spots[_ball(a.shape, (20, 30, 25), 3)] = 1
        spots[_ball(a.shape, (20, 30, 65), 3)] = 2
        full = {"em/raw": (body * 200).astype(np.uint16), "labels/pm_seg": membrane.astype(np.uint8),
                "labels/ecs_seg": outside.astype(np.uint8), "labels/nucleus_seg": seed.astype(np.uint8),
                "labels/spots_seg": spots}
        for name, data in full.items():
            for level, factor in (("s0", 1), ("s1", 2)):
                arr = data if factor == 1 else data[::2, ::2, ::2]
                ds = root.create_dataset(f"{name}/{level}", data=arr, chunks=(10, 10, 10))
                # N5 stores x, y, z; sizes are nanometres.
                ds.attrs["pixelResolution"] = {"dimensions": [1000.0 * factor] * 3, "unit": "nm"}
    return path


def test_the_cells_command_writes_one_readable_folder_per_cell(tmp_path):
    store = _store(tmp_path)
    out = tmp_path / "group"

    result = CliRunner().invoke(cli, [
        "cells", str(store), str(out), "--source", "em/raw", "--entity", "spots=labels/spots_seg",
        "--membrane", "labels/pm_seg", "--outside", "labels/ecs_seg", "--seed", "labels/nucleus_seg",
        "--scale", "s0", "--mask-scale", "s1", "--min-seed-um3", "5", "--wall-voxels", "1"])

    assert result.exit_code == 0, result.output
    folders = sorted(p.name for p in out.iterdir())
    assert folders == ["cell_01", "cell_02"]

    manifest = json.loads((out / "cell_01" / "source.json").read_text())
    assert manifest["object_mask"] == "cell"
    assert manifest["derived"]["membrane"] == "labels/pm_seg"
    assert "crop" in manifest

    # No --object-mask: the manifest says what bounds it.
    found = inspect_object_dir(out / "cell_01")
    assert found.errors == [] and found.object_mask_name == "cell"
    record = load_object(out / "cell_01", RunConfig())
    assert record.object_mask_name == "cell"
    assert set(record.meta["channel_names"]) == {"spots", "cell"}
    # Clipped to the cell: one spot each, not two.
    spots = record.volumes()["spots"]
    assert len(np.unique(spots[spots > 0])) == 1


def test_a_named_object_mask_overrides_the_manifest(tmp_path):
    cell = Cell(1, (slice(0, 4),) * 3, np.ones((4, 4, 4), bool))
    folder = write_cell_folder(tmp_path / "c", cell, store=str(tmp_path / "x.n5"), scale="s0",
                               source="em/raw", entities={"a": "labels/a"}, object_name="cell",
                               derived_from={})
    (folder / "other_mask.tif").write_bytes((folder / "cell_mask.tif").read_bytes())
    manifest = json.loads((folder / "source.json").read_text())
    manifest["masks"]["other"] = "other_mask.tif"
    (folder / "source.json").write_text(json.dumps(manifest))

    assert inspect_object_dir(folder).object_mask_name == "cell"
    assert inspect_object_dir(folder, "other").object_mask_name == "other"


def test_a_manifest_mask_that_is_not_there_is_an_error_that_names_it(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    (folder / "source.json").write_text(json.dumps({
        "store": "x.n5", "source": "em/raw", "entities": {"a": "labels/a"},
        "masks": {"cell": "gone.tif"}}))

    found = inspect_object_dir(folder)

    assert "gone.tif" in found.errors[0]


def test_a_declared_kind_holds_in_every_cell_even_where_the_pixels_would_say_otherwise(tmp_path):
    """One nucleus per cell reads as a mask, so a group would split 'nucleus' in two."""
    store = _store(tmp_path)
    out = tmp_path / "group"
    common = ["cells", str(store), str(out), "--source", "em/raw", "--entity", "nuc=labels/nucleus_seg",
              "--membrane", "labels/pm_seg", "--outside", "labels/ecs_seg", "--seed", "labels/nucleus_seg",
              "--scale", "s0", "--mask-scale", "s1", "--min-seed-um3", "5", "--wall-voxels", "1"]

    assert CliRunner().invoke(cli, common).exit_code == 0
    undeclared = load_object(out / "cell_01", RunConfig())
    assert dict(zip(undeclared.meta["channel_names"], undeclared.meta["entity_kinds"]))["nuc"] == "mask"

    assert CliRunner().invoke(cli, common + ["--instances", "nuc"]).exit_code == 0
    declared = load_object(out / "cell_01", RunConfig())
    assert dict(zip(declared.meta["channel_names"], declared.meta["entity_kinds"]))["nuc"] == "label"
    nuc = declared.volumes()["nuc"]
    assert len(np.unique(nuc[nuc > 0])) == 1, "one connected nucleus is one instance"


def test_naming_an_entity_that_is_not_there_as_instances_is_an_error(tmp_path):
    store = _store(tmp_path)

    result = CliRunner().invoke(cli, [
        "cells", str(store), str(tmp_path / "g"), "--source", "em/raw", "--entity", "a=labels/spots_seg",
        "--membrane", "labels/pm_seg", "--seed", "labels/nucleus_seg", "--scale", "s0",
        "--instances", "b"])

    assert result.exit_code != 0 and "not one of the --entity names" in result.output
