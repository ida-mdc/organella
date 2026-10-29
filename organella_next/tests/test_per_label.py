import dask.array as da
import numpy as np
from scipy import ndimage

from organella.measure.per_label import counts_and_centroids, minimum_per_label


def labels_across_chunks():
    labels = np.zeros((30, 40, 40), np.uint16)
    labels[5:25, 3:37, 3:37] = 7
    labels[2:6, 30:34, 30:34] = 3
    return labels


def test_counts_and_centroids_equal_the_in_memory_answer_across_chunk_borders():
    labels = labels_across_chunks()
    found = counts_and_centroids(da.from_array(labels, chunks=8)).compute()
    ids = found["id"].to_numpy()
    assert ids.tolist() == [3, 7]
    assert found["count"].to_list() == [(labels == i).sum() for i in ids]
    np.testing.assert_allclose(found.select("c0", "c1", "c2").to_numpy(), ndimage.center_of_mass(labels > 0, labels, ids))


def test_minimum_equals_the_in_memory_answer_across_chunk_borders():
    labels = labels_across_chunks()
    field = np.random.default_rng(0).random(labels.shape)
    found = minimum_per_label(da.from_array(field, chunks=8), da.from_array(labels, chunks=8)).compute()
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
