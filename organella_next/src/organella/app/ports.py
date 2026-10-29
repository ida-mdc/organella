from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol

import polars as pl

from organella.app.settings import Settings
from organella.core import RowType, Sample


@dataclass(frozen=True)
class SampleRef:
    """A sample named before it is opened, so one that cannot be opened is a failure of its own."""

    id: str
    open: Callable[[], Sample]


class SampleSource(Protocol):
    """Where samples come from. Opening one is cheap: its arrays stay lazy."""

    def samples(self) -> Iterable[SampleRef]: ...


class Measurement(Protocol):
    """One thing measured on a sample, as rows by type. One pass may feed several row types."""

    def measure(self, sample: Sample, settings: Settings, shared: dict) -> Mapping[RowType, pl.DataFrame]: ...
    # ``shared`` is the sample's scratchpad: what an earlier measurement found that a later one reads.


class GeometrySink(Protocol):
    """Where an object's drawable geometry goes; returns the file it wrote, which the object row records."""

    def write(self, sample: Sample, shared: dict, settings: Settings) -> Path: ...


class PartStore(Protocol):
    """Each sample's rows kept as it finishes, so an interrupted run can be resumed."""

    def load(self, sample_id: str, fingerprint: str) -> list[pl.DataFrame] | None: ...

    def save(self, sample_id: str, fingerprint: str, frames: list[pl.DataFrame]) -> None: ...

    def discard(self) -> None: ...


@dataclass(frozen=True)
class RunInfo:
    """How the run went: what the report's footer records."""

    seconds: float
    per_object_seconds: dict[str, float]
    failures: dict[str, str]

    @property
    def n_objects(self) -> int:
        return len(self.per_object_seconds)


class ReportSink(Protocol):
    def write(self, rows: pl.DataFrame, run: RunInfo) -> None: ...
