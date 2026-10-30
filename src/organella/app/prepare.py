"""What every sample goes through before it is measured."""
from __future__ import annotations

from dataclasses import replace

import dask.array as da

from organella.app.settings import Settings
from organella.core import Sample


def prepared(sample: Sample, settings: Settings) -> Sample:
    sample = _on_one_chunk_grid(sample, settings.chunk_edge)
    return _clipped_to_region(sample) if settings.clip_to_object_mask else sample


def _on_one_chunk_grid(sample: Sample, edge: int) -> Sample:
    """Lazy arrays only combine when their chunks line up."""
    return replace(sample, structures={
        name: replace(s, data=s.data.rechunk(edge)) for name, s in sample.structures.items()
    })


def _clipped_to_region(sample: Sample) -> Sample:
    """Measured structures stop at the object boundary; the mask itself is left as it is."""
    if sample.object_mask is None:
        return sample
    region = sample.region
    return replace(sample, structures={
        name: s if name == sample.object_mask else replace(s, data=da.where(region, s.data, 0))
        for name, s in sample.structures.items()
    })
