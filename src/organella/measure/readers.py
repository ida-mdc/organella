"""Reading one volume, whatever it is stored as.

This project's own data is TIFFs, but the rest of the field publishes NIfTI, NRRD and
MetaImage. Those are one ``SimpleITK.ReadImage`` away and SimpleITK is already a dependency,
so the loader reads them rather than asking for a conversion pass that would rewrite
gigabytes to change nothing but the container.

Every reader returns ``(Z, Y, X)`` for a volume and ``(Y, X)`` for a plane, the order the
rest of the package measures in. SimpleITK's *spacing* is the other way round, ``(x, y, z)``,
and is reversed here once so nothing downstream has to remember which it is holding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import tifffile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArrayRef:
    """One array inside a chunked store, and the window of it to read.

    An object described by a manifest is made of these rather than of files. The window is
    the point: a chunked array is read block by block, so a 512³ window of a 122-gigavoxel
    OpenOrganelle array is 8 of its 1248 chunks - which is why a whole cell never has to be
    downloaded to measure part of it at full resolution.
    """
    store: str
    path: str
    box: Optional[Tuple[slice, ...]] = None
    label: str = ""

    @property
    def name(self) -> str:
        """What to call it in a message or on the object row."""
        return self.label or f"{self.path}"


def parse_box(crop: str | None) -> Optional[Tuple[slice, ...]]:
    """``"0:512,0:512,0:512"`` as slices, in array order, or None for the whole array."""
    if not crop or not str(crop).strip():
        return None
    parts = []
    for axis in str(crop).split(","):
        lo, _, hi = axis.partition(":")
        if not _:
            raise ValueError(f"--crop needs 'lo:hi' per axis, got {axis!r}")
        parts.append(slice(int(lo), int(hi)))
    return tuple(parts)


@lru_cache(maxsize=8)
def open_store(store: str):
    """The store at this URL, opened once per process.

    N5 and Zarr, local or over S3, from the ``remote`` extra - so a missing one is a
    message saying to install it rather than a traceback.
    """
    try:
        import zarr
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImportError(
            "reading a chunked store needs zarr and s3fs: "
            "pip install 'organella[remote]'"
        ) from exc
    if store.rstrip("/").endswith(".n5"):
        # zarr 3 dropped N5 with no replacement, which is why the extra pins zarr<3. Its
        # deprecation warning is not something a reader of a published N5 can act on.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            from zarr.n5 import N5FSStore

            return zarr.open(N5FSStore(store, anon=True), mode="r")
    if store.startswith("s3://"):
        # Without credentials, as the N5 branch does: s3fs otherwise asks botocore for an
        # identity nobody reading a public dataset has, and fails as "nothing found at
        # path ''", which points at the path rather than at the credentials.
        return zarr.open(store, mode="r", storage_options={"anon": True})
    return zarr.open(store, mode="r")


def _array(ref: ArrayRef):
    node = open_store(ref.store)
    try:
        return node[ref.path]
    except KeyError as exc:
        raise FileNotFoundError(f"{ref.store} has no array '{ref.path}'") from exc

TIFF_SUFFIXES = (".tif", ".tiff")

# Longest-first, so ``.nii.gz`` is recognised before ``.gz`` could be. Analyze
# ``.hdr``/``.img`` pairs are left out: two files are not one path.
ITK_SUFFIXES = (".nii.gz", ".nii", ".nrrd", ".nhdr", ".mha", ".mhd", ".mgz")

IMAGE_SUFFIXES = TIFF_SUFFIXES + ITK_SUFFIXES

# NIfTI spacing is millimetres unless it says otherwise, and its unit field is very often
# 0 ("unknown"), as in TotalSegmentator. Assuming mm is what every reader of these does;
# --voxel-size-um overrides it.
_ITK_SPACING_TO_UM = 1000.0


def image_suffix(path: Path) -> str | None:
    """The longest known image suffix ``path`` ends with, lower-cased, or None."""
    name = path.name.lower()
    for suffix in sorted(IMAGE_SUFFIXES, key=len, reverse=True):
        if name.endswith(suffix):
            return suffix
    return None


def is_image(path: Path) -> bool:
    return image_suffix(path) is not None


def image_stem(path: Path) -> str:
    """The file name without its image suffix - ``a.nii.gz`` is ``a``, not ``a.nii``."""
    suffix = image_suffix(path)
    return path.name[: -len(suffix)] if suffix else path.stem


def is_tiff(path: Path) -> bool:
    return (image_suffix(path) or "") in TIFF_SUFFIXES


def _itk_dtype(pixel_id: int) -> np.dtype:
    """numpy dtype for a SimpleITK pixel id, without reading the pixels.

    Spelled out rather than taken from the private ``sitk.extra``, which is the one part
    of the ``dry-run`` header path a SimpleITK release could quietly move.
    """
    import SimpleITK as sitk

    by_id = {
        sitk.sitkUInt8: np.uint8, sitk.sitkInt8: np.int8,
        sitk.sitkUInt16: np.uint16, sitk.sitkInt16: np.int16,
        sitk.sitkUInt32: np.uint32, sitk.sitkInt32: np.int32,
        sitk.sitkUInt64: np.uint64, sitk.sitkInt64: np.int64,
        sitk.sitkFloat32: np.float32, sitk.sitkFloat64: np.float64,
    }
    return np.dtype(by_id.get(pixel_id, np.float64))


def _check_ndim(shape: Tuple[int, ...], path: Path) -> None:
    if len(shape) not in (2, 3):
        raise ValueError(f"Expected a 2D or 3D image in {path.name}, got shape {shape}")


def read_volume(path: Path | ArrayRef) -> np.ndarray:
    """One entity's pixels: a 3D volume or a 2D plane, exactly as stored."""
    if isinstance(path, ArrayRef):
        array = _array(path)
        arr = np.asarray(array[path.box] if path.box else array[:])
        if arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]
        _check_ndim(arr.shape, Path(path.name))
        return arr
    if is_tiff(path):
        arr = tifffile.imread(path)
    else:
        import SimpleITK as sitk

        arr = sitk.GetArrayFromImage(sitk.ReadImage(str(path)))
        # A 3D file holding one slice is a plane, and measuring it as a volume one voxel
        # deep would apply the wrong formulas to every metric it has.
        if arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]
    _check_ndim(arr.shape, path)
    return arr


def read_header(path: Path | ArrayRef) -> Tuple[Tuple[int, ...], str]:
    """Spatial shape and dtype without reading the pixels.

    ``dry-run`` calls this for every file in a batch, so it stays a header read:
    ``ReadImageInformation`` is ~6 ms on a 42-megavoxel NIfTI where the pixels are ~500 ms.
    For a store it is the array metadata, so a remote object is surveyed without fetching a
    chunk.
    """
    if isinstance(path, ArrayRef):
        array = _array(path)
        if path.box:
            shape = tuple(len(range(*s.indices(n)))
                          for s, n in zip(path.box, array.shape))
        else:
            shape = tuple(int(s) for s in array.shape)
        if len(shape) == 3 and shape[0] == 1:
            shape = shape[1:]
        _check_ndim(shape, Path(path.name))
        return shape, str(np.dtype(array.dtype))
    if is_tiff(path):
        with tifffile.TiffFile(path) as tf:
            series = tf.series[0]
            shape = tuple(int(s) for s in series.shape)
            dtype = str(np.dtype(series.dtype))
    else:
        import SimpleITK as sitk

        reader = sitk.ImageFileReader()
        reader.SetFileName(str(path))
        reader.ReadImageInformation()
        shape = tuple(int(s) for s in reversed(reader.GetSize()))  # (x,y,z) -> (z,y,x)
        if len(shape) == 3 and shape[0] == 1:
            shape = shape[1:]
        dtype = str(_itk_dtype(reader.GetPixelID()))
    _check_ndim(shape, path)
    return shape, dtype


def _tiff_voxel_size_um(source_path: Path, ndim: int) -> Tuple[float, ...]:
    """Sample size in µm along each spatial axis, in array order, from TIFF metadata.

    A 2D source has no Z spacing to read and none is invented: the returned tuple is
    (y, x), and everything downstream measures areas rather than volumes because of it.
    """
    with tifffile.TiffFile(source_path) as tf:
        page = tf.pages[0]
        ij = tf.imagej_metadata or {}

        z_um = float(ij["spacing"]) if "spacing" in ij else None

        x_um = None
        y_um = None
        xres_tag = page.tags.get("XResolution")
        yres_tag = page.tags.get("YResolution")
        unit_tag = page.tags.get("ResolutionUnit")
        unit_code = int(unit_tag.value) if unit_tag is not None else 1
        unit_to_um = {2: 25400.0, 3: 10000.0}.get(unit_code)
        if unit_to_um and xres_tag is not None and yres_tag is not None:
            x_num, x_den = xres_tag.value
            y_num, y_den = yres_tag.value
            if x_num:
                x_um = unit_to_um * float(x_den) / float(x_num)
            if y_num:
                y_um = unit_to_um * float(y_den) / float(y_num)
        if x_um is None or y_um is None:
            if xres_tag is not None and yres_tag is not None:
                x_num, x_den = xres_tag.value
                y_num, y_den = yres_tag.value
                if x_num:
                    x_um = float(x_den) / float(x_num)
                if y_num:
                    y_um = float(y_den) / float(y_num)

        missing = x_um is None or y_um is None or (ndim == 3 and z_um is None)
        if missing:
            wanted = "voxel size" if ndim == 3 else "pixel size"
            raise ValueError(
                f"Could not infer {wanted} from source metadata: {source_path.name}"
            )

    return (z_um, y_um, x_um) if ndim == 3 else (y_um, x_um)


def _itk_voxel_size_um(source_path: Path, ndim: int) -> Tuple[float, ...]:
    """Sample size in µm from an ITK-readable header, whose spacing is (x, y, z) in mm."""
    import SimpleITK as sitk

    reader = sitk.ImageFileReader()
    reader.SetFileName(str(source_path))
    reader.ReadImageInformation()
    spacing = tuple(float(s) for s in reversed(reader.GetSpacing()))  # -> (z, y, x)
    if len(spacing) == 3 and ndim == 2:
        spacing = spacing[1:]
    if len(spacing) != ndim:
        wanted = "voxel size" if ndim == 3 else "pixel size"
        raise ValueError(
            f"Could not infer {wanted} from {source_path.name}: header spacing has "
            f"{len(spacing)} axes, image has {ndim}"
        )
    if any(s <= 0 for s in spacing):
        raise ValueError(
            f"Could not infer voxel size from {source_path.name}: spacing {spacing} "
            "has a non-positive axis"
        )
    return tuple(s * _ITK_SPACING_TO_UM for s in spacing)


def _ref_voxel_size_um(ref: ArrayRef, ndim: int) -> Tuple[float, ...]:
    """Sample size in µm from the store's own metadata, which records nanometres.

    Two conventions, both OpenOrganelle's: the ``multiscales`` transform on the parent
    group, which is per scale level and so the one that matters for a downsampled array,
    and ``pixelResolution`` on the array. A store with neither is an error naming
    --voxel-size-um.
    """
    parent, _, level = ref.path.rpartition("/")
    scale = None
    if parent:
        try:
            attrs = dict(open_store(ref.store)[parent].attrs)
        except Exception:  # noqa: BLE001 - no such group, or no attributes on it
            attrs = {}
        for entry in (attrs.get("multiscales") or [{}])[0].get("datasets", []):
            if entry.get("path") == level:
                scale = (entry.get("transform") or {}).get("scale")
                break
    if scale is None:
        res = dict(_array(ref).attrs).get("pixelResolution") or {}
        dims = res.get("dimensions")
        if dims:                       # stored (x, y, z); this package measures (z, y, x)
            scale = list(reversed([float(v) for v in dims]))
    if scale is None:
        raise ValueError(
            f"{ref.store} records no voxel size for '{ref.path}'. Give one as "
            "\"voxel_size_um\" in the manifest, or with --voxel-size-um."
        )
    values = tuple(float(v) / 1000.0 for v in scale)      # nanometres -> µm
    if len(values) == 3 and ndim == 2:
        values = values[1:]
    if len(values) != ndim:
        raise ValueError(
            f"{ref.store} records {len(values)} axes for '{ref.path}', image is {ndim}D"
        )
    return values


def read_voxel_size_um(source_path: Path | ArrayRef, ndim: int = 3) -> Tuple[float, ...]:
    """Sample size in µm per spatial axis, in array order, or ValueError naming the file."""
    if isinstance(source_path, ArrayRef):
        return _ref_voxel_size_um(source_path, ndim)
    if is_tiff(source_path):
        return _tiff_voxel_size_um(source_path, ndim)
    return _itk_voxel_size_um(source_path, ndim)


def voxel_size_source(source_path: Path | ArrayRef) -> str:
    """What ``voxel_size_source`` records when the size was read off the file."""
    if isinstance(source_path, ArrayRef):
        return "store-metadata-nm"
    return "tiff-metadata" if is_tiff(source_path) else "image-header-mm"
