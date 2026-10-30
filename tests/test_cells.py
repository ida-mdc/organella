import json

import numpy as np
import pytest
import zarr

from organella.cells.cut import cut_cells, declared_entities
from organella.cells.derive import cells_at_scale, derive_cell_labels
from organella.sources import open_source


def two_touching_cells(shape=(24, 40, 80)):
    """Two cells side by side, each a box with a membrane round it and a nucleus in the middle; the volume cuts them
    off at its z faces."""
    membrane = np.zeros(shape, np.uint8)
    nuclei = np.zeros(shape, np.uint8)
    for x0, x1 in ((4, 38), (40, 76)):
        membrane[:, 4, x0:x1 + 1] = membrane[:, 35, x0:x1 + 1] = 1
        membrane[:, 4:36, x0] = membrane[:, 4:36, x1] = 1
        nuclei[6:18, 14:26, (x0 + x1) // 2 - 5:(x0 + x1) // 2 + 5] = 1
    return membrane, nuclei


def test_touching_cells_are_cut_apart_numbered_by_nucleus_and_found_cut_off_by_the_volume():
    membrane, nuclei = two_touching_cells()
    labels = derive_cell_labels(membrane, None, nuclei, (0.1,) * 3, min_seed_um3=0.1, wall_voxels=1)
    assert sorted(np.unique(labels)) == [0, 1, 2]
    left, right = labels == 1, labels == 2
    assert left[:, 20, 10].all() or right[:, 20, 10].all()
    assert not (left & right).any() and (left | right).sum() > 0.7 * 24 * 32 * 72
    cells = cells_at_scale(labels, (0.1,) * 3, (0.1,) * 3, labels.shape)
    assert [c.touches_edge for c in cells] == ["z", "z"] and all(c.mask.any() for c in cells)
    assert not cells_at_scale(labels, (0.1,) * 3, (0.1,) * 3, labels.shape, drop_edge=True)


def test_a_seed_smaller_than_the_minimum_is_no_cell():
    membrane, nuclei = two_touching_cells()
    assert not derive_cell_labels(membrane, None, nuclei, (0.1,) * 3, min_seed_um3=1e6).any()


def test_the_scale_a_cell_is_cut_at_can_be_coarser_than_the_one_it_is_measured_at():
    coarse = np.zeros((10, 20, 20), np.uint16)
    coarse[2:8, 4:16, 4:16] = 1
    (cell,) = cells_at_scale(coarse, (0.2, 0.2, 0.2), (0.1, 0.1, 0.1), (20, 40, 40))
    assert cell.window == (slice(4, 16), slice(8, 32), slice(8, 32)) and cell.mask.shape == (12, 24, 24)
    assert cell.volume_um3 == pytest.approx(cell.mask.sum() * 1e-3)


def test_entities_are_declared_once_so_every_cell_reads_them_the_same_way():
    named = {"mito": "labels/mito", "nucleus": "labels/nuc", "er": "labels/er"}
    declared = declared_entities(named, "mito,er", "nucleus")
    assert declared == {"mito": {"array": "labels/mito", "kind": "label"}, "nucleus": {"array": "labels/nuc", "kind": "mask"},
                        "er": {"array": "labels/er", "kind": "label"}}
    with pytest.raises(ValueError, match="not one of"):
        declared_entities(named, "ghost", None)
    with pytest.raises(ValueError, match="both"):
        declared_entities(named, "mito", "mito")


def test_cutting_cells_from_a_store_writes_folders_the_process_command_can_open(tmp_path):
    membrane, nuclei = two_touching_cells()
    root = zarr.open_group(tmp_path / "volume.zarr", mode="w")
    for path, data in (("labels/pm", membrane), ("labels/nuc", nuclei), ("labels/mito", nuclei.astype(np.uint16)), ("em", np.zeros_like(membrane))):
        array = root.create_array(f"{path}/s0", data=data, chunks=(12, 20, 40))
        array.attrs["transform"] = {"axes": ["z", "y", "x"], "scale": [100.0] * 3, "units": ["nm"] * 3}
    say = []
    cells = cut_cells(str(tmp_path / "volume.zarr"), tmp_path / "cells", source="em", entities={"mito": "labels/mito"},
                      membrane="labels/pm", seed="labels/nuc", scale="s0", min_seed_um3=0.1, wall_voxels=1, prefix="vol", say=say.append)
    assert [c.number for c in cells] == [1, 2] and sorted(p.name for p in (tmp_path / "cells").iterdir()) == ["vol_01", "vol_02"]
    manifest = json.loads((tmp_path / "cells" / "vol_01" / "source.json").read_text())
    assert manifest["object_mask"] == "cell" and manifest["derived"]["touches_volume_edge"] == "z" and manifest["scale"] == "s0"
    sample = [ref.open() for ref in open_source(tmp_path / "cells").samples()][0]
    assert sorted(sample.structures) == ["cell", "mito"] and sample.object_mask == "cell"
    assert sample.shape == tuple(s.stop - s.start for s in cells[0].window)
    assert any("2 cell(s)" in line for line in say)
