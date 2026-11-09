import numpy as np
import pytest
from skimage.measure import marching_cubes

from organella.geometry import multires, workers
from organella.geometry.payloads import quantised_payload


def _double(value):
    return value * 2


def _is_worker():
    return workers.in_worker()


@pytest.fixture()
def pool():
    workers.configure(2)
    yield
    workers.configure(0)
    workers.shutdown()


def test_without_workers_a_step_runs_here_and_with_them_in_another_process(pool):
    workers.configure(0)
    assert workers.run(_double, 21) == 42 and workers.run(_is_worker) is False
    workers.configure(2)
    assert workers.run(_double, 21) == 42 and workers.run(_is_worker) is True


def test_levels_come_out_the_same_whether_the_meshes_were_simplified_here_or_in_workers(tmp_path):
    rng = np.random.default_rng(3)
    rows = []
    for _ in range(3):
        field = rng.normal(size=(34, 34, 34)).astype(np.float32)
        from scipy import ndimage
        vertices, faces, _, _ = marching_cubes(ndimage.gaussian_filter(field, 2.5), level=0.0)
        rows.append({"surface": quantised_payload(vertices[:, ::-1].astype(np.float32) * 0.1, faces.astype(np.uint32)), "surface_kind": "mesh"})
    made = {}
    for count in (0, 2):
        workers.configure(count)
        try:
            copy = [dict(r) for r in rows]
            folder = tmp_path / f"w{count}"
            assert multires.give_levels(copy, folder, min_vertices=500, fragment_vertices=400, coarsest_vertices=150) == 3
            made[count] = ([(r["multires"], r["surface"]) for r in copy], (folder / multires.FILENAME).read_bytes())
        finally:
            workers.configure(0)
            workers.shutdown()
    assert made[0] == made[2]
