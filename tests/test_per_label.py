import dask
import dask.array as da
import numpy as np
import pytest
from scipy import ndimage

from organella.measure.per_label import chunks_with_origin, counts_and_centroids, minimised, minimum_in_chunk, smallest, tree_merged


def labels_across_chunks():
    labels = np.zeros((30, 40, 40), np.uint16)
    labels[5:25, 3:37, 3:37] = 7
    labels[2:6, 30:34, 30:34] = 3
    return labels


def test_counts_centroids_covariances_and_boxes_equal_numpy_across_chunk_borders():
    labels = labels_across_chunks()
    labels *= np.random.default_rng(3).random(labels.shape) > 0.2        # ragged, so the covariance is not trivial
    found = counts_and_centroids(da.from_array(labels, chunks=8)).compute()
    ids = found["id"].to_numpy()
    assert ids.tolist() == [3, 7]
    assert found["count"].to_list() == [(labels == i).sum() for i in ids]
    np.testing.assert_allclose(found.select("c0", "c1", "c2").to_numpy(), ndimage.center_of_mass(labels > 0, labels, ids))
    for row, label in enumerate(ids):
        coords = np.argwhere(labels == label)
        covariance = np.cov(coords.T.astype(float), bias=True)
        for i in range(3):
            for j in range(i, 3):
                assert found[f"v{i}{j}"][row] == pytest.approx(covariance[i, j], rel=1e-9)
        assert [found[f"lo{i}"][row] for i in range(3)] == coords.min(axis=0).tolist()
        assert [found[f"hi{i}"][row] for i in range(3)] == coords.max(axis=0).tolist()


def test_a_plane_has_a_two_by_two_covariance():
    found = counts_and_centroids(da.from_array(np.ones((20, 24), np.uint8), chunks=7)).compute()
    assert [c for c in found.columns if c.startswith("v")] == ["v00", "v01", "v11"]


def test_minimum_equals_the_in_memory_answer_across_chunk_borders():
    labels = labels_across_chunks()
    field = np.random.default_rng(0).random(labels.shape)
    chunks = chunks_with_origin(da.from_array(labels, chunks=8), da.from_array(field, chunks=8))
    reports = [dask.delayed(minimum_in_chunk)(chunk, f) for _, (chunk, f) in chunks]
    found = dask.delayed(minimised)(tree_merged(reports, smallest)).compute()
    ids = found["id"].to_numpy()
    np.testing.assert_allclose(found["min"].to_numpy(), ndimage.minimum(field, labels, ids))


def test_many_chunks_are_merged_in_a_tree():
    labels = np.zeros((64, 64, 64), np.uint16)
    labels[::7, ::5, ::3] = 1
    labels[1::9, 2::6, 1::4] = 2
    found = counts_and_centroids(da.from_array(labels, chunks=4)).compute()
    assert found["count"].to_list() == [(labels == i).sum() for i in found["id"]]


def test_no_instance_gives_empty_results():
    found = counts_and_centroids(da.zeros((8, 8, 8), np.uint8, chunks=4)).compute()
    assert found.height == 0 and {"id", "count", "c0", "c1", "c2", "v00", "v22", "lo0", "hi2"} <= set(found.columns)


def test_the_minimum_over_each_instance_matches_scipy_and_skips_the_background():
    from scipy import ndimage
    rng = np.random.default_rng(5)
    labels = np.where(rng.random((20, 22, 18)) < 0.15, rng.integers(1, 9, size=(20, 22, 18)), 0).astype(np.uint32)
    field = rng.random(labels.shape).astype(np.float32)
    found = minimum_in_chunk(labels, field).sort("id")
    present = np.unique(labels[labels > 0])
    assert found["id"].to_list() == present.tolist()
    assert np.array_equal(found["min"].to_numpy(), ndimage.minimum(field, labels, present).astype(np.float32))
    assert minimum_in_chunk(np.zeros_like(labels), field).height == 0
