"""What kinds of row a report holds.

An object is measured at four depths, and each gets its own rows rather than being folded
into the row above it:

    obs_level=0  row_type='object'                          one row per object folder
    obs_level=1  row_type='entity'                          one row per structure, by name
    obs_level=2  row_type='instance'|'distance'|'contact'    one row per instance, per
                                                            instance-target pair, per touching pair

Rows rather than list columns on the object row: the viewer materialises ``obs_level=0`` in
memory (``pp_data``) and leaves the rest a lazy view over the parquet (``pp_all``), so an
edge list up there was loaded in full for every report whatever widget was open. As rows they
are read column-pruned when a widget asks, and queried in plain SQL rather than several
``unnest()`` calls that have to stay row-aligned.

Columns keep their prefixes (``instance_*``, ``distance_*``, ``contact_*``) to say which row
kind they belong to, now that one table holds all of them.

A deep row carries only what identifies it plus what was measured - no group, no provenance.
``object_id`` joins back to the object row for those, which is also how a widget picks up
whatever the reader is grouping and filtering by.
"""

from __future__ import annotations

# Every row has one, so a query never infers a row's kind from which columns are filled.
ROW_TYPE = "row_type"

OBJECT_ROW = "object"
ENTITY_ROW = "entity"
INSTANCE_ROW = "instance"
DISTANCE_ROW = "distance"
CONTACT_ROW = "contact"

# Below the entity rows: obs_level is how deep a row sits, and 2 keeps a deep row out of
# both the object level (0) and the per-entity level (1).
DEEP_OBS_LEVEL = 2
