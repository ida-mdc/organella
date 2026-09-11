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
    """Object-row values, and the deeper rows, from one measurer.

    ``tables`` is columnar - one list per column, all of them the same length - because that
    is the shape the measurers already build and the shape that stays affordable. A real
    object has ~8000 instances and five times as many instance-target distances; as a list of
    per-row dicts a batch of twenty is tens of millions of Python objects, where the same data
    columnar is a few hundred lists.
    """

    columns: Dict[str, Any] = field(default_factory=dict)
    tables: Dict[str, Dict[str, List[Any]]] = field(default_factory=dict)
