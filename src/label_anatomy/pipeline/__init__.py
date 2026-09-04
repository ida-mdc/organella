"""Measure a batch of object folders and assemble one report table.

    objects.py   measuring one object folder into the rows a report holds
    pool.py      running them wide, and surviving a worker that is killed
    parts.py     each object's rows on disk as it finishes, for --resume
    table.py     rows into the one table a report is written from
    batch.py     analyse(): the batch, from those four

Objects are measured one whole object at a time, which is why this exists rather than
a pipeline that chunks: that would split a record which outgrows a memory budget, and
every measurement here is cross-entity or whole-region, so a chunk holding two of five
entities can answer almost nothing.

Row layout, as :mod:`label_anatomy.model.rows` describes it:
  obs_level = 0   one row per object      (row_type = 'object')
  obs_level = 1   one row per entity      (row_type = 'entity'), named by entity_name
  obs_level = 2   one row per instance, instance-target distance and contact
"""

from label_anatomy.model.report import Report
from label_anatomy.pipeline.batch import analyse, group_of
from label_anatomy.pipeline.objects import ObjectResult, measure_object
from label_anatomy.pipeline.parts import discard_parts, settings_fingerprint
from label_anatomy.pipeline.pool import (
    measure_every_object,
    measure_in_pool,
    worker_count,
)
from label_anatomy.pipeline.table import one_table

__all__ = [
    "ObjectResult",
    "Report",
    "analyse",
    "discard_parts",
    "group_of",
    "measure_every_object",
    "measure_in_pool",
    "measure_object",
    "one_table",
    "settings_fingerprint",
    "worker_count",
]
