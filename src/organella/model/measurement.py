"""What one measurer hands back about one object.

Two halves, because a report has two places to put an answer: scalars describing the whole
object go on its row, and anything there are many of becomes rows of its own (see
:mod:`organella.model.rows`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ObjectMeasurement:
    """Object-row values, the per-entity ones, and the deeper rows, from one measurer.

    ``tables`` is columnar - one list per column, all of them the same length - because that
    is the shape the measurers already build and the shape that stays affordable. A real
    object has ~8000 instances and five times as many instance-target distances; as a list of
    per-row dicts a batch of twenty is tens of millions of Python objects, where the same data
    columnar is a few hundred lists.

    ``entity_columns`` is ``{structure: {column: value}}``, for the few things that belong on
    a structure's own row but can only be measured against the whole object - a structure's
    depth inside the bounding mask needs that mask's distance transform, which the per-entity
    measurer never sees.
    """

    columns: Dict[str, Any] = field(default_factory=dict)
    entity_columns: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tables: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
