from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import dask.array as da
import numpy as np

from organella.core import Kind, Structure, normalised


def read_label_map(path: Path) -> dict[int, str]:
    """``{"1": "liver", "2": "spleen"}`` as ``{1: "liver", 2: "spleen"}``."""
    return {int(value): str(name) for value, name in json.loads(path.read_text()).items()}


def split_label_map(structures: Mapping[str, Structure], label_map: Mapping[int, str], entity: str | None) -> dict[str, Structure]:
    """One volume whose ids each mean a different structure, as a mask per named id that is present.

    Only the ids named become structures. ``entity`` names the volume to split, needed only when there is more than one
    labelled structure.
    """
    labelled = [s for s in structures.values() if s.kind is Kind.LABEL]
    target = next((s for s in structures.values() if entity and normalised(s.name) == normalised(entity)), None)
    if target is None:
        if len(labelled) != 1:
            raise ValueError(f"--label-map needs to know which structure to split: there are {len(labelled)} labelled structures "
                             f"({', '.join(sorted(structures)) or 'none'}). Name one with --label-map-entity.")
        target = labelled[0]
    present = set(da.unique(target.data).compute().tolist())
    rest = {n: s for n, s in structures.items() if s is not target}
    for value, name in sorted(label_map.items()):
        clean = normalised(name)
        if clean and value in present and clean not in rest:
            rest[clean] = Structure(clean, Kind.MASK, (target.data == value).astype(np.uint8))
    if len(rest) == len(structures) - 1:
        raise ValueError(f"--label-map named no id present in {target.name} (ids in the volume: {sorted(v for v in present if v)[:10]})")
    return rest
