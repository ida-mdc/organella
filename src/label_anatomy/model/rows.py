"""What kinds of row a report holds.

An object is measured at four depths, and each gets its own rows rather than being folded
into the row above it:

    obs_level=0  row_type='object'                          one row per object folder
    obs_level=1  row_type='entity'                          one row per structure, by name
    obs_level=2  row_type='instance'|'distance'|'contact'    one row per instance, per
                                                            instance-target pair, per touching pair

Instances, distances and contacts used to ride on the object row as parallel list columns,
unnested in SQL. That is what made them expensive: the viewer materialises ``obs_level=0``
as an in-memory table (``pp_data``) and leaves everything else as a lazy view over the
parquet (``pp_all``), so an edge list on the object row was loaded in full for every report,
whatever widget the reader opened. As their own rows they are read when a widget asks for
them, column-pruned, and a query over them is plain SQL rather than several ``unnest()``
calls that have to stay row-aligned.

The columns keep their prefixes (``instance_*``, ``distance_*``, ``contact_*``): they say
which row kind a column belongs to, which matters more now that one table holds all of them.

A deep row carries only what identifies it plus what was measured - no group, no provenance.
The object row has those, and ``object_id`` joins back to it, which is also how a widget picks
up whatever the reader is currently grouping and filtering by.
"""

from __future__ import annotations

# The column that says what a row is. Every row has one, so a query never has to infer a
# row's kind from which columns happen to be filled.
ROW_TYPE = "row_type"

OBJECT_ROW = "object"
ENTITY_ROW = "entity"
INSTANCE_ROW = "instance"
DISTANCE_ROW = "distance"
CONTACT_ROW = "contact"

# Below the entity rows. obs_level counts how many dimensions a row fixes, and a deep row
# fixes none of them - it is one instance, or one pair. 2 keeps it out of pp_data (level 0)
# and out of the per-channel breakdown the viewer's own widgets query (level 1), which is
# what the level is for here: the depth at which a widget looks.
DEEP_OBS_LEVEL = 2
