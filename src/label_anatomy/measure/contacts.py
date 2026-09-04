"""Which instances of a structure touch each other, and how closely.

A contact is a property of an instance *pair*, so it belongs to no single instance: each
pair becomes one row of its own (``row_type='contact'``, see
:mod:`label_anatomy.model.rows`), and the object row keeps the count.

    SELECT object_id, contact_entity, contact_label_a, contact_label_b, contact_gap_um
    FROM pp_all WHERE row_type = 'contact' AND contact_gap_um <= 0.1

Both instances of a pair are of the *same* structure, and only structures with instances -
label entities - take part at all. Two mitochondria touching is a mitochondrial network;
a mitochondrion touching the nucleus is a *distance* to the nucleus, and that is what the
distance rows say. A mask has no instances to pair up in the first place.

The pairs used to ride on the object row as parallel list columns, which meant a reader
loaded every contact of every object into memory to answer any question at all. As rows they
are read when a widget asks for them, and the gap threshold is a WHERE rather than an unnest.

Skip it with ``label-anatomy process --no-contacts``, which is what a batch that only
wants morphology should do: finding the pairs is minutes on a large object.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np

from label_anatomy.config import AnatomyConfig
from label_anatomy.model import CONTACT_ROW, ObjectMeasurement, ObjectStack
from label_anatomy.analysis.cache import contacts_for

logger = logging.getLogger(__name__)

# What the object row carries about contacts. The count and nothing more: it is what a
# per-object comparison and an overview line need, and the pairs themselves are rows.
_OBJECT_COLUMNS: Dict[str, Any] = {
    "contact_count": np.int64,
}

# One row per touching pair. One entity column, not two: both instances are of the same
# structure, which is the only kind of pair there is.
_CONTACT_COLUMNS: Dict[str, Any] = {
    "contact_entity": str,
    "contact_label_a": np.int64,
    "contact_label_b": np.int64,
    "contact_gap_um": np.float64,
}

_DESCRIPTIONS: Dict[str, str] = {
    "contact_count": "Number of instance pairs of one structure within the recorded gap threshold; on the object row.",
    "contact_entity": "Structure both instances of this pair belong to; a pair is always within one structure.",
    "contact_label_a": "Label id of the first instance of the pair, the lower of the two.",
    "contact_label_b": "Label id of the second instance of the pair.",
    "contact_gap_um": "Surface-to-surface gap of the pair in µm, measured in whole voxel steps, so instances sharing a face read one voxel step rather than zero.",
}


class ContactMeasurer:
    """Pairwise surface-to-surface gaps between the instances of one object."""

    NAME = "anatomy-contacts"
    DESCRIPTION = (
        "Records which instances of a structure lie within a gap threshold of each other, one "
        "row per pair with its surface-to-surface gap in µm. Pairs are within one structure and "
        "only for structures that have instances: how close an instance gets to a different "
        "structure is a distance, which anatomy-instances measures."
    )

    # What lands on the object row, and what lands on rows of its own. The pipeline reads
    # both: the first to fill the object row, the second to type the contact rows.
    OBJECT_COLUMNS: Dict[str, Any] = dict(_OBJECT_COLUMNS)
    ROW_SCHEMAS: Dict[str, Dict[str, Any]] = {CONTACT_ROW: dict(_CONTACT_COLUMNS)}
    COLUMN_DESCRIPTIONS: Dict[str, str] = dict(_DESCRIPTIONS)

    def __init__(self) -> None:
        self._config = AnatomyConfig.from_env()

    def measure(self, stack: ObjectStack) -> ObjectMeasurement:
        # Views, not copies: a real entity volume is hundreds of megabytes.
        contacts = contacts_for(
            stack.object_id, stack.volumes(), stack.kinds, stack.sample_size,
            self._config.contact_max_um,
        )
        logger.info(
            "anatomy: %s: %d instance pairs within %.3g µm",
            stack.object_id, len(contacts), self._config.contact_max_um,
        )
        table = {
            "contact_entity": [c[0] for c in contacts],
            "contact_label_a": [c[1] for c in contacts],
            "contact_label_b": [c[2] for c in contacts],
            "contact_gap_um": [c[3] for c in contacts],
        }
        return ObjectMeasurement(columns={"contact_count": len(contacts)},
                                 tables={CONTACT_ROW: table})
