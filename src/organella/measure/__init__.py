"""Reading an object, and measuring it. Nothing here knows about the report format.

One module per thing that gets measured, each producing the rows of one depth:

    loading      an object folder -> one ObjectStack
    morphology   one entity  -> its entity row
    instances    one object  -> its instance and distance rows
    contacts     one object  -> its contact rows
    mesh         one object  -> its geometry file, and the path to it on the object row

A measurer is a plain class: construct it, hand it an object, get back what it measured.
:mod:`organella.pipeline` is what runs them and turns their answers into a table,
and :mod:`organella.column_schema` is what gathers, for the report writer, which
columns they produce - the only place the two vocabularies meet.
"""

from organella.measure.contacts import ContactMeasurer
from organella.measure.instances import InstanceMeasurer
from organella.measure.loading import (
    find_object_dirs,
    is_object_dir,
    load_object,
)
from organella.measure.geometry import GeometryWriter
from organella.measure.morphology import MorphologyMeasurer

__all__ = [
    "ContactMeasurer",
    "GeometryWriter",
    "InstanceMeasurer",
    "MorphologyMeasurer",
    "find_object_dirs",
    "is_object_dir",
    "load_object",
]
