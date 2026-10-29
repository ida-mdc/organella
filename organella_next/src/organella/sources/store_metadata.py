from __future__ import annotations

import json

import tensorstore as ts

from organella.core import VoxelSize
from organella.sources.arrays import kvstore_spec

_TO_UM = {"nm": 1e-3, "um": 1.0, "µm": 1.0, "mm": 1e3}
# Where each format keeps an array's attributes, and the key they sit under in that file.
_ATTRIBUTE_FILES = (("attributes.json", None), ("zarr.json", "attributes"), (".zattrs", None))


def _attributes(url: str, array_path: str) -> dict:
    store = ts.KvStore.open(kvstore_spec(url)).result()
    for name, key in _ATTRIBUTE_FILES:
        found = store.read(f"{array_path.strip('/')}/{name}").result()
        if found.state == "value":
            document = json.loads(found.value)
            return document.get(key, {}) if key else document
    return {}


def voxel_size_from_store(url: str, array_path: str) -> VoxelSize | None:
    """Voxel size from the array's ``transform`` attribute (axes in array order), or None when it has none."""
    transform = _attributes(url, array_path).get("transform")
    if not transform:
        return None
    units = transform.get("units") or ["um"] * len(transform["scale"])
    try:
        return VoxelSize(tuple(float(s) * _TO_UM[u] for s, u in zip(transform["scale"], units)))
    except KeyError:
        return None
