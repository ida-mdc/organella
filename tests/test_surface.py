import dask.array as da
import numpy as np
import SimpleITK as sitk

from organella.measure.surface import lazy_surface
from organella.measure.windows import compute_in_batches as measure_surfaces


def itk_on_the_whole_volume(labels, spacing):
    image = sitk.GetImageFromArray(labels.astype(np.uint32))
    image.SetSpacing(tuple(reversed(spacing)))
    statistics = sitk.LabelShapeStatisticsImageFilter()
    statistics.SetComputePerimeter(True)
    statistics.Execute(image)
    return {int(i): (statistics.GetPerimeter(i), statistics.GetRoundness(i)) for i in statistics.GetLabels()}


def test_a_window_gives_the_estimate_ITK_gives_on_the_whole_volume_including_at_the_edge():
    rng = np.random.default_rng(2)
    labels = np.zeros((24, 40, 40), np.uint16)
    labels[0:10, 5:20, 5:20] = 1                    # touches the volume's z edge
    labels[12:20, 22:38, 3:15] = 2
    labels[12:20, 22:38, 15:25] = 3                 # a neighbour sharing a face with 2
    labels *= rng.random(labels.shape) > 0.03
    spacing = (0.1, 0.04, 0.05)
    expected = itk_on_the_whole_volume(labels, spacing)
    data = da.from_array(labels, chunks=9)
    lazies = []
    for label in (1, 2, 3):
        coords = np.argwhere(labels == label)
        lazies.append(lazy_surface(data, coords.min(axis=0), coords.max(axis=0), label, spacing, 10**9))
    for label, frame in zip((1, 2, 3), measure_surfaces(lazies, batch=2)):
        np.testing.assert_allclose([frame["boundary"][0], frame["roundness"][0]], expected[label], rtol=1e-9)


def test_a_box_over_the_limit_is_read_coarser_and_still_measured():
    labels = np.zeros((40, 40, 40), np.uint8)
    labels[5:35, 5:35, 5:35] = 1
    frame, = measure_surfaces([lazy_surface(da.from_array(labels, chunks=16), np.array([5, 5, 5]), np.array([34, 34, 34]),
                                            None, (0.1, 0.1, 0.1), 4000)], batch=1)
    assert 0.7 < frame["roundness"][0] <= 1.05      # a cube is somewhat round, and the estimate is finite
