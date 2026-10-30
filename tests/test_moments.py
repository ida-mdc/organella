import dask.array as da
import numpy as np

from organella.measure.per_label import counts_and_centroids


def covariance_columns(found, ndim):
    return {(i, j): found[f"v{i}{j}"].to_numpy() for i in range(ndim) for j in range(i, ndim)}


def test_second_moments_equal_numpy_across_chunk_borders():
    rng = np.random.default_rng(3)
    labels = np.zeros((30, 40, 36), np.uint16)
    labels[4:26, 3:37, 5:30] = 2
    labels[10:14, 10:30, 8:12] = 5
    labels *= rng.random(labels.shape) > 0.2        # ragged, so the covariance is not trivial
    found = counts_and_centroids(da.from_array(labels, chunks=8)).compute().sort("id")
    for row, label in enumerate(found["id"]):
        coords = np.argwhere(labels == label).astype(float)
        cov = np.cov(coords.T, bias=True)
        got = covariance_columns(found, 3)
        for (i, j), values in got.items():
            np.testing.assert_allclose(values[row], cov[i, j], rtol=1e-9)


def test_a_plane_has_a_two_by_two_covariance():
    labels = np.zeros((20, 24), np.uint8)
    labels[3:9, 4:20] = 1
    found = counts_and_centroids(da.from_array(labels, chunks=7)).compute()
    assert [c for c in found.columns if c.startswith("v")] == ["v00", "v01", "v11"]


def test_bounding_boxes_are_the_extreme_voxels_across_chunk_borders():
    labels = np.zeros((30, 40, 36), np.uint16)
    labels[4:26, 3:37, 5:30] = 2
    labels[10:14, 10:30, 8:12] = 5
    found = counts_and_centroids(da.from_array(labels, chunks=8)).compute().sort("id")
    for row, label in enumerate(found["id"]):
        coords = np.argwhere(labels == label)
        assert [found[f"lo{i}"][row] for i in range(3)] == coords.min(axis=0).tolist()
        assert [found[f"hi{i}"][row] for i in range(3)] == coords.max(axis=0).tolist()
