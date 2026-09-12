"""What every column of a report means, gathered in one place.

A report is self-describing: every column carries its description in the parquet's field
metadata, and the whole map goes in the footer as ``organella_column_descriptions`` as well.
The footer copy is the one the page reads, since a browser cannot get at field metadata -
DuckDB drops it - and it is why the explanation under a chart is the same sentence as the
one in the file.

The declarations live next to the code that fills them, in :mod:`organella.measure`; this
module gathers them and adds the few columns the pipeline itself writes.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from organella.measure.contacts import ContactMeasurer
from organella.measure.geometry import GeometryWriter
from organella.measure.instances import InstanceMeasurer
from organella.measure.loading import LOADED_DESCRIPTIONS
from organella.measure.morphology import MorphologyMeasurer

# What a row is, and where it came from: columns stamped on rather than measured.
ROW_DESCRIPTIONS: Dict[str, str] = {
    "obs_level": (
        "How deep in the report this row sits: 0 is the whole object, 1 is one structure of "
        "it, 2 is one instance, distance or contact below that."
    ),
    "row_type": (
        "What this row is: 'object' (obs_level 0), 'entity' (obs_level 1, one per structure), "
        "or one of 'instance', 'distance' and 'contact' below them."
    ),
    "imported_path_short": (
        "The experimental group this object was imported as, from the -p path it was found "
        "under. This is the axis a difference between conditions is read along."
    ),
}

# The object's own file facts, read off the folder while loading it.
FILE_DESCRIPTIONS: Dict[str, str] = {
    "path": "Path of the object folder, relative to the batch root.",
    "file_extension": "Lower-cased extension of the source image, without the leading dot.",
    "size_bytes": "Size on disk of everything read for this object, in bytes.",
    "modification_date": "When the source image was last modified.",
    "channel_names": (
        "The structures stacked along C for this object, in order - one channel each, "
        "which is how an object is read as a single stack."
    ),
    "num_pixels": "Number of samples in the analysed region of this object.",
}

# Columns whose name carries an axis. Described by pattern, so a 2D object's two and a 3D
# object's three are one entry each rather than five.
PATTERN_DESCRIPTIONS: List[Tuple[str, str]] = [
    (r"^pixel_size_[ZYX]$",
     "Physical size of one sample along this axis, in µm. Every measurement in the report "
     "is in µm because of it, so it is worth checking: it comes from the TIFF metadata "
     "unless --voxel-size-um overrode it, and voxel_size_source says which."),
    (r"^size_[ZYX]$",
     "Extent of the analysed region along this axis, in samples."),
]


def _measured() -> Dict[str, str]:
    """Every description the measurers declare."""
    out: Dict[str, str] = {}
    for measurer in (MorphologyMeasurer, InstanceMeasurer, ContactMeasurer, GeometryWriter):
        out.update(measurer.COLUMN_DESCRIPTIONS)
    return out


#: Column name -> what it means. The one map, for the field metadata and the footer alike.
COLUMN_DESCRIPTIONS: Dict[str, str] = {
    **ROW_DESCRIPTIONS,
    **FILE_DESCRIPTIONS,
    **LOADED_DESCRIPTIONS,
    **_measured(),
}


def describe(column: str) -> str:
    """What one column means: the exact entry, else a pattern, else nothing.

    Nothing rather than a guess: a column with no description says so by having none.
    """
    if column in COLUMN_DESCRIPTIONS:
        return COLUMN_DESCRIPTIONS[column]
    for pattern, description in PATTERN_DESCRIPTIONS:
        if re.match(pattern, column):
            return description
    return ""


def plural_of(noun: str) -> str:
    """The plural to use for a noun the run supplied.

    ``cell`` gives ``cells``; ``nucleus/nuclei`` gives ``nuclei``, because guessing a plural
    from English spelling is a losing game and the run already knows the answer.
    """
    singular, _, plural = noun.partition("/")
    if plural:
        return plural
    # A word already ending in s does not take another: "granuless" is what that gives.
    return singular if singular.endswith("s") else singular + "s"


def singular_of(noun: str) -> str:
    """The singular half of a ``singular/plural`` noun."""
    return noun.partition("/")[0]


def with_noun(description: str, noun: Optional[str]) -> str:
    """A description with "object" replaced by what the run calls one.

    Done when the file is written rather than by whatever reads it, so every reader gets
    the same sentence. Whole words only, so ``object_id`` in a sentence is left alone.
    """
    if not noun:
        return description
    singular, plural = singular_of(noun), plural_of(noun)
    description = re.sub(r"\bobjects\b", plural, description)
    return re.sub(r"\bobject\b", singular, description)


def descriptions_for(columns, noun: Optional[str] = None) -> Dict[str, str]:
    """The description of each of these columns that has one."""
    return {column: with_noun(describe(column), noun)
            for column in columns if describe(column)}
