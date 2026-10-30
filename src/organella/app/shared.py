"""What measurements of one sample leave for each other and for the geometry."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl


@dataclass(frozen=True)
class Findings:
    """What the statistics pass found, for the geometry to draw from."""

    stats: dict[str, pl.DataFrame]       # per structure: id, count, centroid, covariance, bounding box
    entities: pl.DataFrame
    instances: dict[str, pl.DataFrame]   # per labelled structure
    skeletons: dict[str, list]           # per skeletonised structure, one Traced per instance
    contacts: pl.DataFrame | None


@dataclass
class Shared:
    """One sample's scratchpad, passed to every measurement in turn: ``Statistics`` fills it, ``Distances`` and the
    geometry read it."""

    findings: Findings | None = None
    tips: dict[str, dict[int, np.ndarray]] = field(default_factory=dict)   # skeleton tips in µm: structure -> label -> (n, ndim)
