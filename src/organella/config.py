"""Run configuration.

Each object is measured in its own worker process, and a measurer is constructed with no
arguments, so the knobs travel as environment variables - ``ORGANELLA_`` followed by the
field name, read once per process and set by the CLI from its own flags. ``from_env`` below
is the whole list; the fields carry why each default is what it is.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

_TRUE = {"1", "true", "yes", "on"}


def normalize_name(s: str) -> str:
    """A name in the form two of them can be compared in.

    Names arrive from two directions - typed on the command line, and read off a file name -
    so both are folded to lower case with the punctuation collapsed. That is what makes
    ``--skeletons ER`` match the entity discovered from ``sample_ER_label.tif``.
    """
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


# The entities named, and no others. None and the empty set both mean none: skeletonising
# is opt-in, so there is no value of this that means "all".
EntityFilter = Optional[FrozenSet[str]]


# What --geometry-as accepts per structure. Three name the surface an instance is stored as.
# `skeleton` is a different question, asked by --skeletons, and is still accepted here -
# combined with a plus, `mito=mesh+skeleton` is --skeletons mito and a mesh to draw it over.
GEOMETRY_SURFACES = ("mesh", "ellipsoid", "tube")
GEOMETRY_CHOICES = GEOMETRY_SURFACES + ("skeleton",)

# A tube *is* a centre line, so asking for one asks for the skeleton too.
_NEEDS_SKELETON = frozenset({"tube", "skeleton"})


def wants_skeletons(entity: str, geometry_as: Mapping[str, str],
                    skeletons: EntityFilter = None) -> bool:
    """Whether this entity needs a centre line: named by --skeletons, or drawn as a tube.

    Opt-in, because branches, length and tortuosity mean something for a filament and
    nothing for a granule, whose skeleton is one branch the length of its diameter - and
    because skeletonising is the most expensive thing in a run: on one real object it was
    103 s of two minutes, most of it spent on structures nobody was going to read it for.

    Names are compared normalised, so ``--geometry-as ER=tube`` matches the entity
    discovered from ``sample_ER_label.tif`` as ``er``.
    """
    if skeletons and normalize_name(entity) in {normalize_name(n) for n in skeletons}:
        return True
    return bool(_tokens_for(entity, geometry_as) & _NEEDS_SKELETON)


def forced_surface(entity: str, geometry_as: Mapping[str, str]) -> Optional[str]:
    """The surface kind this entity was told to use, or None to decide from its shape.

    Naming only ``skeleton`` forces nothing: the centre line is drawn over whatever the
    shape itself calls for, which - now that a centre line exists - may be a tube.
    """
    for token in _tokens_for(entity, geometry_as):
        if token in GEOMETRY_SURFACES:
            return token
    return None


def _tokens_for(entity: str, geometry_as: Mapping[str, str]) -> FrozenSet[str]:
    if not geometry_as:
        return frozenset()
    wanted = normalize_name(entity)
    for name, choice in geometry_as.items():
        if normalize_name(name) == wanted:
            return frozenset(str(choice).split("+"))
    return frozenset()


def parse_geometry_as(raw: Optional[str]) -> Dict[str, str]:
    """``"mito=mesh+skeleton,vesicle=ellipsoid"`` into ``{entity: choice}``.

    An unknown kind is an error naming the ones there are: a typo silently meaning "decide
    for me" is how a run quietly stops doing what was asked.
    """
    if raw is None or not str(raw).strip():
        return {}
    out: Dict[str, str] = {}
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, choice = part.partition("=")
        name, choice = name.strip(), choice.strip().lower()
        if not sep or not name or not choice:
            raise ValueError(
                f"--geometry-as takes NAME=KIND pairs, got {part!r}. "
                f"Kinds: {', '.join(GEOMETRY_CHOICES)}, combined with '+'."
            )
        tokens = [t for t in choice.split("+") if t]
        unknown = [t for t in tokens if t not in GEOMETRY_CHOICES]
        if unknown:
            raise ValueError(
                f"--geometry-as: no kind called {unknown[0]!r}. "
                f"Kinds: {', '.join(GEOMETRY_CHOICES)}, combined with '+'."
            )
        surfaces = [t for t in tokens if t in GEOMETRY_SURFACES]
        if len(surfaces) > 1:
            raise ValueError(
                f"--geometry-as: {name} cannot be both {' and '.join(surfaces)}; "
                "an instance has one surface."
            )
        out[normalize_name(name)] = "+".join(tokens)
    return out


def parse_entity_filter(raw: Optional[str]) -> EntityFilter:
    """A comma-separated list into an entity filter; nothing given means no entity.

    No separate way to say "none": not naming anything already is one.
    """
    if raw is None or not raw.strip():
        return None
    return frozenset(part for part in (p.strip() for p in raw.split(",")) if part)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in _TRUE


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _colours_from(path: Path, name: str) -> Dict[str, str]:
    """Structure colours from a JSON file: ``{"mito": "#d62728", "er": "#2ca02c"}``.

    The report carries them, so a shared parquet arrives already coloured. Names the batch
    does not have are ignored, so one file can cover a project; structures the file does not
    name keep their place in the built-in palette.
    """
    try:
        loaded = json.loads(path.read_text())
    except FileNotFoundError:
        raise ValueError(f"{name}: no such file: {path}") from None
    except json.JSONDecodeError as error:
        raise ValueError(f"{name}: {path} is not valid JSON: {error}") from None
    if not isinstance(loaded, dict):
        raise ValueError(f"{name}: {path} must hold an object of structure: colour pairs")

    colours: Dict[str, str] = {}
    for entity, colour in loaded.items():
        if not isinstance(colour, str) or not _HEX.match(colour):
            raise ValueError(
                f"{name}: {path} gives {entity!r} the colour {colour!r}; it has to be a hex "
                "colour like '#d62728' or '#d62'"
            )
        # One form only, so two spellings of the same colour compare equal.
        short = len(colour) == 4
        colours[str(entity)] = (
            "#" + "".join(c * 2 for c in colour[1:]).lower() if short else colour.lower()
        )
    return colours


def _env_colours(name: str) -> Dict[str, str]:
    """The colours the settings file gives, read once per process."""
    raw = os.environ.get(name)
    return dict(_colours_from(Path(raw.strip()), name)) if raw and raw.strip() else {}


def _env_label_map(name: str) -> Dict[int, str]:
    """``{id: structure}`` from a JSON file: ``{"1": "liver", "2": "spleen"}``.

    Keys are label ids, so a key that is not an integer is named as a mistake rather than
    becoming a structure that never matches anything.
    """
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return {}
    path = Path(raw.strip())
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read the label map {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"The label map {path} must be an object of id: name pairs")
    out: Dict[int, str] = {}
    for key, value in loaded.items():
        try:
            out[int(key)] = str(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"The label map {path} has a key that is not a label id: {key!r}"
            ) from exc
    return out


def _env_voxel_size(name: str) -> Optional[Tuple[float, ...]]:
    """Sample size in µm: 'z,y,x' for volumes, 'y,x' for planes.

    A length that does not match the images is refused rather than padded or truncated,
    which would rescale every measurement.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    parts = [p for p in raw.replace(" ", "").split(",") if p]
    if len(parts) not in (2, 3):
        raise ValueError(f"{name} must be 'z,y,x' (3D) or 'y,x' (2D) in µm, got {raw!r}")
    return tuple(float(p) for p in parts)


@dataclass(frozen=True)
class RunConfig:
    # Never guessed: the region is cropped and clipped to this mask and polarity is measured
    # from its centroid. None means nothing bounds the object, and the columns that would
    # need one are not written.
    object_mask: Optional[str] = None
    object_noun: Optional[str] = None
    # 'z,y,x' for a volume, 'y,x' for a plane. A length that does not match the images is
    # refused rather than padded or truncated.
    voxel_size_um: Optional[Tuple[float, ...]] = None
    # On: naming a bounding mask and then measuring outside it is not what --object-mask
    # says. Off with --no-clip, for data already confined to the object, or when truncating
    # what straddles the boundary is worse than including it.
    clip: bool = True
    auto_label_masks: bool = False
    # None = every entity in the folder. Each one is another full-size channel of the
    # stack, so naming a few is how a 117-structure subject is measured at all.
    entities: Optional[FrozenSet[str]] = None
    # Label id -> structure name, for a single volume whose ids each mean a different
    # structure. Empty means the volume is one entity, as its file name says.
    label_map: Dict[int, str] = field(default_factory=dict)
    # Which entity the label map splits. None and exactly one label entity in the folder
    # means that one; None and several is an error naming them, never a pick.
    label_map_entity: Optional[str] = None
    # Structure name -> "#rrggbb", from a settings file. Empty means the built-in palette.
    entity_colours: Dict[str, str] = field(default_factory=dict)
    max_skeleton_voxels: int = 500_000
    # Which structures get a curve skeleton. Opt-in: it dominates a run.
    skeletons: EntityFilter = None
    # Structure -> how to store its surface: mesh, ellipsoid or tube. Unset for a structure
    # means decide from its measured shape.
    geometry_as: Dict[str, str] = field(default_factory=dict)
    # 1, not 0: an object already has a worker process, so a kimimaro pool inside it costs
    # a failed fork per object.
    num_threads: int = 1
    # Separate from num_threads: edt is a C++ loop with no subprocesses, so it can use
    # all cores even inside a Dask worker, where kimimaro's process pool cannot.
    edt_threads: int = 0
    contact_max_um: float = 0.5
    # Both walk every voxel of every instance, so they are opt-in.
    polarity_spread: bool = False
    distance_histograms: bool = False
    # Structures left out of the region a distance is read against: the chance distribution
    # is over everywhere an instance could have been, so a structure it could never sit in
    # does not belong in it. None leaves in everything but the target itself, which is
    # excluded either way because its own distance to itself is zero.
    baseline_exclude: EntityFilter = None
    # Geometry is written beside the report, never into it; unset means no meshing at all.
    mesh_dir: Optional[str] = None
    mesh_smooth_sigma: float = 0.7
    mesh_step_size: int = 2
    mesh_target_reduction: float = 0.8
    mesh_level: Optional[float] = None
    # Processes to mesh instances with. 0 = work it out from the cores left over.
    mesh_workers: int = 0
    # Most vertices any one surface keeps; 0 lifts the cap.
    mesh_max_vertices: int = 200_000
    # "marching-cubes" or "surface-nets".
    mesh_surface_method: str = "marching-cubes"
    # Keep an object's geometry.parquet rather than meshing it again. Meshing dominates a
    # run, so a batch that died partway finishes in minutes.
    reuse_geometry: bool = False

    # Settings that change what a run produces. A list rather than "everything except", so
    # a new option is only reused across runs once someone has thought about it.
    _RESULT_AFFECTING = (
        "object_mask", "voxel_size_um", "clip", "auto_label_masks", "entities",
        "label_map", "label_map_entity", "skeletons", "geometry_as",
        "max_skeleton_voxels",
        "contact_max_um", "polarity_spread", "distance_histograms", "baseline_exclude",
        "mesh_smooth_sigma", "mesh_step_size", "mesh_target_reduction", "mesh_level",
    )

    def fingerprint(self, *extra: Any) -> str:
        """A short digest of the settings a cached result was produced under.

        --reuse-geometry and --resume hand back work from an earlier run, and keying on the
        object's name alone meant changing --no-clip or the voxel size returned the old
        answer regardless. Anything not byte-identical here means measuring again.
        """
        import hashlib
        import json

        def plain(value: Any) -> Any:
            if isinstance(value, (frozenset, set)):
                return sorted(value)
            if isinstance(value, tuple):
                return list(value)
            return value

        payload = {name: plain(getattr(self, name)) for name in self._RESULT_AFFECTING}
        if extra:
            payload["extra"] = [plain(e) for e in extra]
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    @classmethod
    def from_env(cls) -> "RunConfig":
        return cls(
            object_mask=os.environ.get("ORGANELLA_OBJECT_MASK") or None,
            object_noun=os.environ.get("ORGANELLA_OBJECT_NOUN") or None,
            voxel_size_um=_env_voxel_size("ORGANELLA_VOXEL_SIZE_UM"),
            clip=not _env_flag("ORGANELLA_NO_CLIP"),
            auto_label_masks=_env_flag("ORGANELLA_AUTO_LABEL_MASKS"),
            entities=parse_entity_filter(os.environ.get("ORGANELLA_ENTITIES")),
            label_map=_env_label_map("ORGANELLA_LABEL_MAP"),
            label_map_entity=(os.environ.get("ORGANELLA_LABEL_MAP_ENTITY") or None),
            entity_colours=_env_colours("ORGANELLA_ENTITY_COLOURS"),
            max_skeleton_voxels=_env_int("ORGANELLA_MAX_SKELETON_VOXELS", 500_000),
            skeletons=parse_entity_filter(os.environ.get("ORGANELLA_SKELETONS")),
            geometry_as=parse_geometry_as(os.environ.get("ORGANELLA_GEOMETRY_AS")),
            num_threads=_env_int("ORGANELLA_NUM_THREADS", 1),
            edt_threads=_env_int("ORGANELLA_EDT_THREADS", 0),
            contact_max_um=_env_float("ORGANELLA_CONTACT_MAX_UM", 0.5),
            polarity_spread=_env_flag("ORGANELLA_POLARITY_SPREAD"),
            distance_histograms=_env_flag("ORGANELLA_DISTANCE_HISTOGRAMS"),
            baseline_exclude=parse_entity_filter(
                os.environ.get("ORGANELLA_BASELINE_EXCLUDE")),
            mesh_dir=os.environ.get("ORGANELLA_MESH_DIR") or None,
            mesh_smooth_sigma=_env_float("ORGANELLA_MESH_SMOOTH_SIGMA", 0.7),
            mesh_step_size=_env_int("ORGANELLA_MESH_STEP_SIZE", 2),
            mesh_target_reduction=_env_float("ORGANELLA_MESH_TARGET_REDUCTION", 0.8),
            mesh_level=(
                _env_float("ORGANELLA_MESH_LEVEL", 0.0)
                if os.environ.get("ORGANELLA_MESH_LEVEL") else None
            ),
            mesh_workers=_env_int("ORGANELLA_MESH_WORKERS", 0),
            mesh_max_vertices=_env_int("ORGANELLA_MESH_MAX_VERTICES", 200_000),
            mesh_surface_method=(os.environ.get("ORGANELLA_MESH_SURFACE_METHOD")
                                 or "marching-cubes"),
            reuse_geometry=_env_flag("ORGANELLA_REUSE_GEOMETRY"),
        )
