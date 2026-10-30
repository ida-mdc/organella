"""Two small batches that look like cells, with known answers, and the check that a report measured from one is right.

    python tests/synthetic.py make 3d batch        # four objects: batch/control/object_{a,b}, batch/treated/object_{c,d}
    python tests/synthetic.py make 2d batch2d
    python tests/synthetic.py check 3d report.parquet

Each object is a cell with a ragged outline (``pm``, the object mask), an irregular nucleus, a Golgi crescent beside it,
curved mitochondria and a scatter of granules in the cytoplasm. The treated group has more, and fatter, mitochondria
and fewer granules, so the charts have something to compare. Everything is seeded: the same call makes the same cells.

The expected numbers are not written down here. ``check`` rebuilds the arrays and takes every one from numpy and scipy:
volumes, centroids, the distance of each instance to each other structure, and the contacts between instances, by
brute force. The pytest suite and the install job of CI both run it; the Pages workflow publishes the reports.

Needs numpy, scipy and tifffile (the installed package brings all three).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import tifffile
from scipy import ndimage

OBJECTS = {"control": ("object_a", "object_b"), "treated": ("object_c", "object_d")}
REACH_UM = 1.0
CONTACT_MAX_UM = 0.5

# Voxel size, shape and (per group) how many mitochondria and granules a cell has and how fat a mitochondrion is, in voxels.
SPEC = {
    3: dict(voxel=(0.1, 0.08, 0.08), shape=(40, 128, 128), cell=(15, 54, 46), nucleus=(8, 21, 19), nucleus_at=(0, -4, 20),
            counts={"control": (22, 70, 3.0), "treated": (32, 45, 3.6)}),
    2: dict(voxel=(0.05, 0.05), shape=(192, 192), cell=(78, 68), nucleus=(30, 26), nucleus_at=(-6, 28),
            counts={"control": (24, 60, 4.5), "treated": (34, 38, 5.5)}),
}


def _smooth_noise(rng: np.random.Generator, shape, sigma: float) -> np.ndarray:
    field = ndimage.gaussian_filter(rng.normal(size=shape), sigma)
    return field / np.abs(field).max()


def _radius(grid, centre, semi_axes) -> np.ndarray:
    """Distance from ``centre`` in units of the semi-axes: 1 on the ellipsoid."""
    return np.sqrt(sum(((g - c) / a) ** 2 for g, c, a in zip(grid, centre, semi_axes)))


def _paint_capsules(labels: np.ndarray, region: np.ndarray, rng, count: int, length: tuple[int, int], radius: float,
                    bend: float, flat: float) -> None:
    """``count`` rods with round ends, straight or bent, each label painted only where nothing is yet, inside ``region``."""
    ndim, placed = labels.ndim, 0
    inside = np.argwhere(region)
    while placed < count:
        start = inside[rng.integers(len(inside))].astype(float)
        direction = rng.normal(size=ndim)
        direction[0] *= flat if ndim == 3 else 1.0       # in a section, rods lie mostly in the plane
        direction /= np.linalg.norm(direction)
        points, step = [start], rng.integers(*length) / 2
        for _ in range(2):
            points.append(points[-1] + direction * step)
            turned = direction + rng.normal(size=ndim) * bend
            direction = turned / np.linalg.norm(turned)
        r = radius * rng.uniform(0.8, 1.25)
        low = np.maximum(np.floor(np.min(points, axis=0) - r - 1), 0).astype(int)
        high = np.minimum(np.ceil(np.max(points, axis=0) + r + 2), labels.shape).astype(int)
        box = tuple(slice(a, b) for a, b in zip(low, high))
        grid = np.indices(tuple(b - a for a, b in zip(low, high))).astype(float) + low.reshape((-1,) + (1,) * ndim)
        nearest = np.full(grid.shape[1:], np.inf)
        for a, b in zip(points[:-1], points[1:]):
            along = b - a
            t = np.clip(sum((g - p) * d for g, p, d in zip(grid, a, along)) / max(float(along @ along), 1e-9), 0, 1)
            nearest = np.minimum(nearest, np.sqrt(sum((g - (p + t * d)) ** 2 for g, p, d in zip(grid, a, along))))
        shape = (nearest <= r) & region[box] & (labels[box] == 0)
        if shape.sum() >= 12:
            placed += 1
            labels[box][shape] = placed


@lru_cache(maxsize=None)
def arrays_of(ndim: int, group: str, name: str) -> dict[str, np.ndarray]:
    """The five structures of one object, with everything clipped to the cell."""
    spec = SPEC[ndim]
    rng = np.random.default_rng(1000 * (ndim == 3) + 10 * list(OBJECTS).index(group) + OBJECTS[group].index(name) + 7)
    shape, centre = spec["shape"], np.array(spec["shape"]) / 2
    grid = np.indices(shape).astype(float)
    pm = (_radius(grid, centre, spec["cell"]) <= 1 + 0.16 * _smooth_noise(rng, shape, 7)).astype(np.uint8)
    at = centre + np.array(spec["nucleus_at"]) * rng.uniform(0.7, 1.1)
    nucleus = ((_radius(grid, at, spec["nucleus"]) <= 1 + 0.13 * _smooth_noise(rng, shape, 5)) & (pm > 0)).astype(np.uint8)
    # a crescent hugging one side of the nucleus: a shell of it, within a wedge of directions
    out = np.array([g - c for g, c in zip(grid, at)])[-2:]
    unit = out / (np.linalg.norm(out, axis=0) + 1e-9)
    around = _radius(grid, at, spec["nucleus"])
    side = np.array([-0.5, 1.0]) / np.hypot(-0.5, 1.0)
    golgi = ((around > 1.12) & (around < 1.5) & (unit[0] * side[0] + unit[1] * side[1] > 0.55) & (pm > 0)).astype(np.uint8)
    if ndim == 3:
        golgi[np.abs(grid[0] - centre[0]) > 0.45 * spec["cell"][0]] = 0
    cytoplasm = (pm > 0) & (nucleus == 0) & (golgi == 0)
    mitochondria, granules, fat = spec["counts"][group]
    mito = np.zeros(shape, np.uint16)
    _paint_capsules(mito, cytoplasm, rng, mitochondria + int(rng.integers(-2, 3)), (16, 44), fat, bend=0.55, flat=0.3)
    granule = np.zeros(shape, np.uint16)
    _paint_capsules(granule, cytoplasm & (mito == 0), rng, granules + int(rng.integers(-5, 6)), (2, 3), 2.6 if ndim == 3 else 3.6,
                    bend=0.0, flat=1.0)
    return {"pm": pm, "nucleus": nucleus, "golgi": golgi, "mito": mito, "granule": granule}


def make_batch(root: Path, ndim: int) -> None:
    spec = SPEC[ndim]
    for group, names in OBJECTS.items():
        for name in names:
            folder = root / group / name
            folder.mkdir(parents=True, exist_ok=True)
            y_um, x_um = spec["voxel"][-2:]
            tifffile.imwrite(folder / "sample.tif", np.zeros(spec["shape"], np.uint8), imagej=True, resolution=(1 / x_um, 1 / y_um),
                             metadata={"unit": "micron", **({"spacing": spec["voxel"][0]} if ndim == 3 else {})})
            for structure, array in arrays_of(ndim, group, name).items():
                tifffile.imwrite(folder / f"sample_{structure}_{'label' if structure in LABELLED else 'mask'}.tif", array)


LABELLED = ("mito", "granule")


def _contacts(labels: np.ndarray, voxel: np.ndarray) -> dict[tuple[int, int], float]:
    """Gap of every pair of instances closer than the contact distance: a distance transform of each, in its own padded box."""
    pad = np.ceil(CONTACT_MAX_UM / voxel).astype(int) + 1
    found = {}
    for label, where in enumerate(ndimage.find_objects(labels), start=1):
        if where is None:
            continue
        box = tuple(slice(max(s.start - p, 0), s.stop + p) for s, p in zip(where, pad))
        window = labels[box]
        to_this = ndimage.distance_transform_edt(window != label, sampling=voxel)
        for other in np.unique(window[(window > 0) & (window != label)]):
            gap = float(to_this[window == other].min())
            if gap <= CONTACT_MAX_UM + 1e-4:
                key = (min(label, int(other)), max(label, int(other)))
                found[key] = min(found.get(key, np.inf), gap)
    return found


def check(report: Path, ndim: int) -> None:
    """Assert that ``report``, measured from this batch with ``--object-mask pm --skeletons mito`` and geometry, says what the
    arrays say."""
    import polars as pl

    spec, rows = SPEC[ndim], pl.read_parquet(report)
    voxel, cell = np.array(spec["voxel"]), float(np.prod(spec["voxel"]))
    axes, size = "zyx"[-ndim:], "area_um2" if ndim == 2 else "volume_um3"
    of = lambda kind: rows.filter(pl.col("row_type") == kind)
    expected = [(group, name) for group, names in OBJECTS.items() for name in names]
    objects = of("object").sort("object_id")
    assert objects["object_id"].to_list() == sorted(n for _, n in expected), objects["object_id"].to_list()
    for group, name in expected:
        arrays = arrays_of(ndim, group, name)
        mine = lambda frame: frame.filter(pl.col("object_id") == name)
        obj = mine(objects).row(0, named=True)
        assert obj["imported_path_short"] == group, f"{name}: group {obj['imported_path_short']}"
        assert [obj[f"size_{a}"] for a in axes] == list(spec["shape"]), f"{name}: shape"
        assert np.allclose([obj[f"pixel_size_{a}"] for a in axes], voxel, rtol=1e-5), f"{name}: voxel size"
        assert obj["spatial_dims"] == ndim, f"{name}: dimensions"
        assert abs(obj[f"object_{size}"] - arrays["pm"].sum() * cell) < 1e-9, f"{name}: object {size}"

        entities = mine(of("entity"))
        for structure, array in arrays.items():
            total = entities.filter(pl.col("entity_name") == structure)[f"total_{size}"][0]
            assert abs(total - (array > 0).sum() * cell) < 1e-9, f"{name}/{structure}: total {size}"

        away = {t: ndimage.distance_transform_edt(arrays[t] == 0, sampling=voxel) for t in ("nucleus", "golgi", *LABELLED)}
        away["pm"] = ndimage.distance_transform_edt(arrays["pm"] > 0, sampling=voxel)          # depth inside the cell
        for structure in LABELLED:
            labels = arrays[structure]
            ids = [int(i) for i in np.unique(labels) if i]
            instances = mine(of("instance")).filter(pl.col("instance_entity") == structure).sort("instance_label")
            assert instances["instance_label"].to_list() == ids, f"{name}/{structure}: instances"
            counts = ndimage.sum(labels > 0, labels, ids)
            assert np.allclose(instances[f"instance_{size}"].to_numpy(), np.array(counts) * cell), f"{name}/{structure}: {size}"
            centres = np.array(ndimage.center_of_mass(labels > 0, labels, ids)) * voxel
            got = np.stack([instances[f"instance_centroid_{a}_um"].to_numpy() for a in axes], axis=1)
            assert np.allclose(got, centres, atol=1e-6), f"{name}/{structure}: centroids"

            # Distance to every other structure: exact below the reach, the reach where it is farther.
            for target in ("pm", "nucleus", "golgi", *(t for t in LABELLED if t != structure)):
                found = mine(of("distance")).filter((pl.col("distance_entity") == structure)
                                                    & (pl.col("distance_target") == target)).sort("distance_label")
                want = [min(float(ndimage.minimum(away[target], labels, i)), REACH_UM) for i in ids]
                assert found["distance_label"].to_list() == ids, f"{name}/{structure} to {target}: instances"
                assert np.allclose(found["distance_um"].to_numpy(), want, atol=1e-5), f"{name}/{structure}: distance to {target}"

            # Contacts: every pair the brute force finds, each at its gap, and no other (a pair within rounding of the
            # contact distance may go either way).
            truth = _contacts(labels, voxel)
            seen = mine(of("contact")).filter(pl.col("contact_entity") == structure)
            got_pairs = {(a, b): g for a, b, g in zip(seen["contact_label_a"], seen["contact_label_b"], seen["contact_gap_um"])}
            clear = {k for k, g in truth.items() if g < CONTACT_MAX_UM - 1e-4}
            assert clear <= got_pairs.keys() <= truth.keys(), f"{name}/{structure}: contact pairs differ by {set(got_pairs) ^ set(truth)}"
            assert all(abs(got_pairs[k] - truth[k]) < 1e-5 for k in got_pairs), f"{name}/{structure}: contact gaps"

        # Skeletons were asked for on the mitochondria: a path about as long as the rod is long, at the very least.
        rods = mine(of("instance")).filter((pl.col("instance_entity") == "mito") & (pl.col(f"instance_{size}") > 200 * cell))
        extent = {int(i): float(np.ptp(np.argwhere(arrays["mito"] == i) * voxel, axis=0).max()) for i in rods["instance_label"]}
        for label, length, branches in zip(rods["instance_label"], rods["instance_length_um"], rods["instance_branches"]):
            assert branches >= 1 and length >= 0.5 * extent[int(label)], f"{name}: skeleton of mito {label}: {branches}, {length}"

        # Geometry beside the report, a drawable row for nearly every instance.
        drawn = "outline" if ndim == 2 else "surface"
        geometry = pl.read_parquet(obj["mesh_geometry_file"]).filter(pl.col("row_type") == "instance")
        for structure in LABELLED:
            have = geometry.filter(pl.col("entity_name") == structure)
            assert have.height == len(np.unique(arrays[structure])) - 1, f"{name}/{structure}: geometry rows"
            assert have[drawn].null_count() <= 0.1 * have.height, f"{name}/{structure}: undrawable instances"


if __name__ == "__main__":
    command, dimension, target = (sys.argv[1:4] + [None] * 3)[:3]
    if command not in ("make", "check") or dimension not in ("2d", "3d") or not target:
        raise SystemExit(__doc__)
    ndim = int(dimension[0])
    if command == "make":
        make_batch(Path(target), ndim)
        print(f"wrote {target}/{{control,treated}}/object_* ({dimension})")
    else:
        check(Path(target), ndim)
        print(f"{target}: every number matches the arrays ({dimension})")
