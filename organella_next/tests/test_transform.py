import edt
import numpy as np
import pytest

from organella.measure.transform import distance_to_voxels


def shapes(rng):
    z, y, x = np.indices((40, 50, 60))
    blob = ((z - 20) ** 2 + (y - 25) ** 2 + (x - 30) ** 2) <= 12 ** 2
    hollow = blob & ~(((z - 20) ** 2 + (y - 25) ** 2 + (x - 30) ** 2) <= 8 ** 2)
    slab = np.zeros((40, 50, 60), bool)
    slab[:, 10:14, :] = True
    return {"solid blob": blob, "hollow shell": hollow, "slab to the edge": slab, "scattered points": rng.random((40, 50, 60)) > 0.995,
            "border voxels": np.pad(np.zeros((38, 48, 58), bool), 1, constant_values=True)}


@pytest.mark.parametrize("spacing", [(0.1, 0.1, 0.1), (0.1, 0.02, 0.05), (0.02752, 0.032, 0.032)])
def test_the_transform_equals_the_one_edt_gives_on_awkward_shapes(spacing):
    for name, target in shapes(np.random.default_rng(3)).items():
        expected = edt.edt(~target, anisotropy=spacing)
        np.testing.assert_allclose(distance_to_voxels(target, spacing), expected, atol=1e-5, err_msg=name)


def test_it_works_in_a_plane():
    target = np.zeros((30, 40), bool)
    target[10:14, 5:30] = True
    np.testing.assert_allclose(distance_to_voxels(target, (0.1, 0.2)), edt.edt(~target, anisotropy=(0.1, 0.2)), atol=1e-5)
