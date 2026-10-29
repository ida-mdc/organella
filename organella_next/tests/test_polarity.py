import dask.array as da
import numpy as np
from scipy import ndimage

from organella.measure.depth import boundary_voxels, depth_um
from organella.measure.polarity import aspect_ratio, polarity


def test_a_point_straight_up_in_z_has_elevation_ninety_and_a_point_at_the_centre_has_no_direction():
    got = polarity(np.array([[2.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 3.0, 3.0]]))
    assert got["polar_dist_um"].tolist() == [2.0, 0.0, np.hypot(3, 3)]
    assert got["polar_el_deg"][0] == 90.0
    assert np.isnan(got["polar_nz"][1]) and np.isnan(got["polar_az_deg"][1])
    assert got["polar_az_deg"][2] == 45.0


def test_a_plane_has_one_angle():
    got = polarity(np.array([[0.0, 1.0], [1.0, 0.0]]))
    assert sorted(got) == ["polar_angle_deg", "polar_dist_um", "polar_nx", "polar_ny"]
    assert got["polar_angle_deg"].tolist() == [0.0, 90.0]


def test_aspect_ratio_is_the_square_root_of_the_eigenvalue_ratio():
    assert aspect_ratio(np.array([np.diag([16.0, 4.0, 1.0]), np.diag([1.0, 1.0, 1.0])])).tolist() == [4.0, 1.0]


def test_a_point_a_line_and_a_plane_have_no_aspect_ratio():
    point, line, plane = np.zeros((3, 3)), np.diag([9.0, 1e-17, 1e-17]), np.diag([4.0, 4.0, 1e-15])
    assert np.isnan(aspect_ratio(np.array([point, line, plane]))).all()


def test_depth_matches_the_distance_to_the_nearest_voxel_outside():
    mask = np.zeros((20, 30, 30), bool)
    mask[3:17, 4:26, 5:27] = True
    voxel = np.array([0.1, 0.05, 0.05])
    inside = np.argwhere(mask)[::37]
    expected = ndimage.distance_transform_edt(mask, sampling=voxel)[tuple(inside.T)]
    boundary = boundary_voxels(da.from_array(mask, chunks=7)).compute()
    np.testing.assert_allclose(depth_um(boundary, voxel, inside.astype(float)), expected, atol=1e-9)
