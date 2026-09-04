"""One real pipeline run, shared by the tests that inspect its output, and one object
built in memory, shared by the tests that measure a single object."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Sequence, Tuple

import numpy as np
import polars as pl
import pytest

from label_anatomy import pipeline, report_io
from label_anatomy.cli import FLAVOR
from label_anatomy.measure import find_object_dirs
from label_anatomy.model import ObjectStack
from label_anatomy.analysis.cache import CACHE
from label_anatomy.report_page import report_page
from synthetic import make_dataset, make_dataset_2d

REPORT_PAGE_CHECKER = Path(__file__).parent / "report_page_check.mjs"


# The synthetic objects are bounded by a mask called "pm". Nothing is guessed, so every
# test that loads one names it, the same way a run does.
OBJECT_MASK = "pm"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    """No LABEL_ANATOMY_* setting and no cached per-object work crosses a test boundary.

    Plugin options travel through the environment and the per-object cache is module-level,
    so without this the suite's result depends on the order it happens to run in. The object
    mask is then set back, because it is not a tuning knob: without it nothing loads at all.
    """
    for key in [k for k in os.environ if k.startswith("LABEL_ANATOMY_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LABEL_ANATOMY_OBJECT_MASK", OBJECT_MASK)
    CACHE.clear()
    yield
    CACHE.clear()


# One structure gets a colour from a settings file and the others do not, so both halves of
# every widget's colour lookup are exercised by the shared report.
MITO_COLOUR = "#d62728"


def _run(root: Path, out: Path) -> Path:
    """One batch through the real pipeline, exactly as `process` runs it."""
    os.environ["LABEL_ANATOMY_OBJECT_MASK"] = OBJECT_MASK
    # The per-voxel distance distributions, because one section of the report is about them
    # and without them the shared report cannot exercise it at all.
    os.environ["LABEL_ANATOMY_DISTANCE_HISTOGRAMS"] = "1"
    # And skeletons for the filaments, which is what a real run names: skeletonising is
    # opt-in, so without this the report carries no branches, length or tortuosity.
    os.environ["LABEL_ANATOMY_SKELETON_ENTITIES"] = "mito"
    paths = ["control", "treated"]
    report = pipeline.analyse(find_object_dirs(root), root, paths, workers=1)
    assert not report.failures, report.failures
    written = report_io.write(report, out, root=root, paths=paths, flavor=FLAVOR)
    # `process --colours` does exactly this, and so does the `colours` command afterwards.
    report_io.recolour(written, {"mito": MITO_COLOUR})
    return written


@pytest.fixture(scope="session")
def report_path(tmp_path_factory) -> Path:
    root = make_dataset(tmp_path_factory.mktemp("objects"))
    return _run(root, root.parent / "report.parquet")


@pytest.fixture(scope="session")
def table(report_path) -> pl.DataFrame:
    df, _ = report_io.read(report_path)
    return df


@pytest.fixture(scope="session")
def report_path_2d(tmp_path_factory) -> Path:
    """The same batch shape as report_path, in a plane rather than a volume."""
    root = make_dataset_2d(tmp_path_factory.mktemp("flat_objects"))
    return _run(root, root.parent / "report_2d.parquet")


@pytest.fixture(scope="session")
def table_2d(report_path_2d) -> pl.DataFrame:
    df, _ = report_io.read(report_path_2d)
    return df


def object_stack(
    entities: Dict[str, Tuple[np.ndarray, str]],
    *,
    voxel_size: Sequence[float],
    object_id: str = "object_a",
    object_mask: str | None = OBJECT_MASK,
    object_shape: Sequence[int] | None = None,
    center: Sequence[float] | None = None,
) -> ObjectStack:
    """One object built in memory, the way the loader would hand it over.

    ``entities`` is ``{name: (volume, kind)}`` in channel order; a 3D volume gives a CZYX
    stack and a 2D one CYX, which is the only difference between the two anywhere here.
    ``object_shape`` is normally the stack's own extent - pass a different one to build the
    fragment a whole-object measurement has to refuse.
    """
    names = list(entities)
    data = np.stack([entities[name][0] for name in names], axis=0)
    axes = "ZYX" if data.ndim == 4 else "YX"
    meta: Dict[str, object] = {
        "channel_names": names,
        "entity_kinds": [entities[name][1] for name in names],
        "object_id": object_id,
        "object_mask_name": object_mask if object_mask in names else None,
        "object_shape": list(object_shape if object_shape is not None else data.shape[1:]),
        "spatial_dims": len(axes),
        **{f"pixel_size_{ax}": float(size) for ax, size in zip(axes, voxel_size)},
    }
    if center is not None:
        meta.update({f"object_center_{ax.lower()}_um": float(value)
                     for ax, value in zip(axes, center)})
    return ObjectStack(data, "C" + axes, meta)


def run_report_page(job: Dict[str, object]) -> Dict[str, object]:
    """Hand the report page a job and get back what its own functions made of it.

    The page is a standalone HTML file, so there is nothing to import: the checker lifts
    its ``<script>`` out and runs it in node. Tests that would otherwise mirror one of its
    functions or one of its queries by hand go through here instead, because a mirror can
    be right while the page is wrong - which is exactly how a query naming a column its own
    source did not expose once reached the browser.
    """
    node = shutil.which("node")
    assert node, "node is required: the report page is JavaScript"
    out = subprocess.run(
        [node, str(REPORT_PAGE_CHECKER), str(report_page()), "/dev/stdin"],
        input=json.dumps(job, default=str), capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def report_rows(report: Path) -> list:
    """Every row of a report as the page receives it: JSON, one object per row."""
    import duckdb

    result = duckdb.connect().execute(f"SELECT * FROM read_parquet('{report}')")
    names = [d[0] for d in result.description]
    return [dict(zip(names, row)) for row in result.fetchall()]


def report_column_help(report: Path) -> Dict[str, str]:
    """The column descriptions the page reads out of a report's footer."""
    return report_io.column_descriptions(report)


@pytest.fixture(scope="session")
def page_sql() -> Dict[str, object]:
    """The SQL fragments the page's geometry sections are built from."""
    return run_report_page({})["constants"]
