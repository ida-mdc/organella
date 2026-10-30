from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Mapping

from organella.core import normalised

SURFACES = ("mesh", "ellipsoid", "tube")
EVERYTHING = "*"


def parse_geometry_as(raw: str | None) -> dict[str, str]:
    """``"mito=mesh+skeleton,vesicle=ellipsoid"`` as ``{name: choice}``; ``*`` stands for every structure not named.

    An unknown kind is an error naming the ones there are, because a typo silently meaning "decide for me" is how a run
    quietly stops doing what was asked.
    """
    chosen: dict[str, str] = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        name, separator, choice = part.partition("=")
        name, tokens = name.strip(), [t for t in choice.strip().lower().split("+") if t]
        if not separator or not name or not tokens:
            raise ValueError(f"--geometry-as takes NAME=KIND pairs, got {part.strip()!r}. Kinds: {', '.join(SURFACES)}, skeleton, joined by '+'.")
        unknown = [t for t in tokens if t not in (*SURFACES, "skeleton")]
        if unknown:
            raise ValueError(f"--geometry-as: no kind called {unknown[0]!r}. Kinds: {', '.join(SURFACES)}, skeleton, joined by '+'.")
        if len([t for t in tokens if t in SURFACES]) > 1:
            raise ValueError(f"--geometry-as: {name} cannot be more than one surface; an instance has one.")
        chosen[EVERYTHING if name == EVERYTHING else normalised(name)] = "+".join(tokens)
    return chosen


@dataclass(frozen=True)
class Settings:
    """What a run was asked to do."""

    reach_um: float = 1.0
    instances: bool = True  # a row per instance, their distances and baselines; off leaves the per-structure rows
    contacts: bool = True
    polarity_spread: bool = False  # the angular spread of each instance's voxels as seen from the object centre
    distance_histograms: bool = False  # per instance and target, a histogram of its voxels' distances, not only the nearest
    baseline_exclude: frozenset[str] = frozenset()  # normalised names of structures left out of the region the baseline is read over
    contact_max_um: float = 0.5  # the largest gap between two instances of one structure that still counts as touching
    skeletons: frozenset[str] = frozenset()  # normalised names of the structures to trace a skeleton of
    max_skeleton_voxels: int | None = 500_000  # instances larger than this are not traced
    skeleton_scale: float = 3.0  # reach around a traced path, in local radii
    skeleton_const_voxels: float = 6.0  # reach added to it, in voxels of the coarsest axis
    skeleton_prune: float = 2.0  # terminal branches shorter than this many radii are cut
    geometry_as: Mapping[str, str] = field(default_factory=dict)  # per structure: mesh, ellipsoid or tube, plus skeleton
    keep_skeletons: bool = False  # keep each traced skeleton whole, for the geometry that draws it
    mesh_smooth_sigma: float | tuple[float, ...] = 0.7  # in voxels of the finest axis, or one per axis (z, y, x)
    mesh_step_size: int = 1
    mesh_target_reduction: float = 0.8
    mesh_level: float | None = None
    mesh_max_vertices: int = 0  # 0 is no cap
    clip_to_object_mask: bool = True
    histogram_bins: int = 128
    instance_histogram_bins: int = 20
    chunk_edge: int = 128
    coarse_voxels: int = 200_000_000  # a whole-structure surface is read every n-th voxel past this many
    surface_batch: int = 100_000  # instances whose windows are read together

    def fingerprint(self, *extra) -> str:
        """A short digest of what a result was produced under: anything not identical means measuring again."""
        plain = {k: (sorted(v) if isinstance(v, frozenset) else v) for k, v in asdict(self).items()}
        return hashlib.sha256(json.dumps([plain, *extra], sort_keys=True, default=str).encode()).hexdigest()[:16]

    def _choice(self, name: str) -> frozenset[str]:
        """What the structure was told by its own name, else by ``*``; a name beats ``*`` whole."""
        for key, choice in self.geometry_as.items():
            if key != EVERYTHING and normalised(key) == normalised(name):
                return frozenset(choice.split("+"))
        return frozenset(self.geometry_as.get(EVERYTHING, "").split("+")) - {""}

    def wants_skeleton(self, name: str) -> bool:
        """Named by --skeletons, or drawn as a tube, which is a centre line."""
        return normalised(name) in self.skeletons or bool(self._choice(name) & {"tube", "skeleton"})

    def forced_surface(self, name: str) -> str | None:
        """The surface kind the structure was told to use, or None to decide from its shape."""
        return next((t for t in self._choice(name) if t in SURFACES), None)
