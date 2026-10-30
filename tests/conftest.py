from pathlib import Path

import numpy as np
import pytest
import tifffile

VOXEL_UM = (0.1, 0.02, 0.02)
SHAPE = (48, 96, 96)


def _write(path: Path, array: np.ndarray, with_scale: bool = False) -> None:
    kwargs = {}
    if with_scale:
        z, y, x = VOXEL_UM
        kwargs = dict(imagej=True, resolution=(1 / x, 1 / y), metadata={"spacing": z, "unit": "micron"})
    tifffile.imwrite(path, array, tile=None, **kwargs)


@pytest.fixture(scope="module")
def arrays() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(1)
    cell = np.zeros(SHAPE, np.uint8)
    cell[4:44, 8:88, 8:88] = 1
    mito = np.zeros(SHAPE, np.uint16)
    for label in range(1, 12):
        z, y, x = (int(rng.integers(8, n - 8)) for n in SHAPE)
        mito[z - 3:z + 3, y - 5:y + 5, x - 5:x + 5] = label
    mito[cell == 0] = 0
    wall = np.zeros(SHAPE, np.uint8)
    wall[:, 50:54, :] = 1
    return {"cell": cell, "mito": mito, "wall": wall}


@pytest.fixture(scope="module")
def object_folder(tmp_path_factory, arrays) -> Path:
    folder = tmp_path_factory.mktemp("data") / "batch" / "cell_a"
    folder.mkdir(parents=True)
    _write(folder / "sample.tif", np.zeros(SHAPE, np.uint8), with_scale=True)
    _write(folder / "sample_cell_mask.tif", arrays["cell"])
    _write(folder / "sample_mito_label.tif", arrays["mito"])
    _write(folder / "sample_wall_mask.tif", arrays["wall"])
    return folder


@pytest.fixture(scope="module")
def report_path(object_folder, tmp_path_factory) -> Path:
    from organella.app.process import process
    from organella.app.settings import Settings
    from organella.measure import DEFAULT
    from organella.sinks import ParquetSink
    from organella.sources import FolderSource

    out = tmp_path_factory.mktemp("report") / "report.parquet"
    assert process(FolderSource(object_folder.parent, object_mask="cell"), DEFAULT, ParquetSink(out), Settings(reach_um=0.4)) == {}
    return out
