"""Open an image file or a chunked store as a lazy dask array."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import dask.array as da
import SimpleITK as sitk
import tensorstore as ts
import tifffile

TIFF_SUFFIXES = (".tif", ".tiff")
# Longest first, so ``.nii.gz`` is recognised before ``.gz`` could be.
ITK_SUFFIXES = (".nii.gz", ".nii", ".nrrd", ".nhdr", ".mha", ".mhd", ".mgz")
IMAGE_SUFFIXES = ITK_SUFFIXES + TIFF_SUFFIXES


def image_suffix(path: Path) -> str | None:
    name = path.name.lower()
    return next((s for s in IMAGE_SUFFIXES if name.endswith(s)), None)


def image_stem(path: Path) -> str:
    """The file name without its image suffix: ``a.nii.gz`` is ``a``, not ``a.nii``."""
    suffix = image_suffix(path)
    return path.name[: -len(suffix)] if suffix else path.stem


def image_header(path: Path) -> tuple[tuple[int, ...], str]:
    """Shape and dtype from the file's header, without reading the pixels. A 3D file of one slice is a plane."""
    if image_suffix(path) in TIFF_SUFFIXES:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            return tuple(int(n) for n in series.shape), str(series.dtype)
    reader = sitk.ImageFileReader()
    reader.SetFileName(str(path))
    reader.ReadImageInformation()
    shape = tuple(int(n) for n in reversed(reader.GetSize()))        # (x, y, z) to (z, y, x)
    return (shape[1:] if len(shape) == 3 and shape[0] == 1 else shape), sitk.GetPixelIDValueAsString(reader.GetPixelID())


def open_image(path: Path) -> da.Array:
    return open_tiff(path) if image_suffix(path) in TIFF_SUFFIXES else open_itk(path)


def open_itk(path: Path) -> da.Array:
    """NIfTI, NRRD or MetaImage, read whole through SimpleITK: none of them can be read in part. A 3D file of one
    slice is a plane, and measuring it as a volume one voxel deep would apply the wrong formulas to every metric."""
    pixels = sitk.GetArrayFromImage(sitk.ReadImage(str(path)))
    return da.from_array(pixels[0] if pixels.ndim == 3 and pixels.shape[0] == 1 else pixels, chunks=128)

DEFAULT_CACHE_BYTES = 2 << 30


def cache_context(cache_bytes: int = DEFAULT_CACHE_BYTES) -> ts.Context:
    """Decoded chunks kept for reuse, shared by every array opened with it: windows cut from the same stored
    chunk would otherwise each fetch and decode it again. The cap bounds the memory."""
    return ts.Context({"cache_pool": {"total_bytes_limit": cache_bytes}})


def open_tiff(path: Path) -> da.Array:
    """Read lazily by the operating system when the file allows it, else whole: a compressed TIFF cannot be sliced."""
    try:
        pixels = tifffile.memmap(path, mode="r")
    except ValueError:
        pixels = tifffile.imread(path)
    return da.from_array(pixels, chunks=128)


def kvstore_spec(url: str) -> dict:
    """Where a store lives. ``s3://`` is read anonymously over HTTPS, which is all a public dataset needs."""
    parsed = urlparse(url)
    if parsed.scheme == "s3":
        key = parsed.path.strip("/")
        return {"driver": "http", "base_url": f"https://{parsed.netloc}.s3.amazonaws.com/{key}/"}
    if parsed.scheme in ("http", "https"):
        return {"driver": "http", "base_url": url.rstrip("/") + "/"}
    return {"driver": "file", "path": str(Path(url).resolve()) + "/"}


def _is_n5(url: str) -> bool:
    return url.rstrip("/").endswith(".n5")


def open_store_array(url: str, array_path: str, context: ts.Context | None = None) -> da.Array:
    """Always in C order (z, y, x): N5 lists dimensions fastest-varying first, and tensorstore passes that order on."""
    drivers = ["n5"] if _is_n5(url) else ["zarr3", "zarr"]
    for driver in drivers:
        try:
            # A run measures one snapshot, so a cached chunk is not revalidated with the server on every read.
            spec = {"driver": driver, "kvstore": kvstore_spec(url), "path": array_path, "recheck_cached_data": False}
            store = ts.open(spec, context=context or cache_context()).result()
            break
        except ValueError:
            continue
    else:
        raise FileNotFoundError(f"no {' or '.join(drivers)} array '{array_path}' in {url}")
    if _is_n5(url):
        store = store.T
    return da.from_array(_Readable(store), chunks=tuple(store.chunk_layout.read_chunk.shape), asarray=False)


class _Readable:
    """What dask needs of an array: a shape, a dtype and slicing that returns numpy."""

    def __init__(self, store: ts.TensorStore) -> None:
        self._store = store
        self.shape, self.ndim, self.dtype = tuple(store.shape), store.ndim, store.dtype.numpy_dtype

    def __getitem__(self, index):
        return self._store[index].read().result()
