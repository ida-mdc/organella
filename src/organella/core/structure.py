from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

import dask.array as da


class Kind(StrEnum):
    LABEL = "label"
    MASK = "mask"


def normalised(name: str) -> str:
    """A name as two of them can be compared: one typed on the command line, one read off a file name."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass(frozen=True)
class Structure:
    """One segmented thing in a sample: ``label`` separates instances, ``mask`` is a single region."""

    name: str
    kind: Kind
    data: da.Array

    @property
    def foreground(self) -> da.Array:
        return self.data > 0
