import dask.array as da
import numpy as np
import polars as pl

from organella.app.ports import SampleRef
from organella.app.process import process
from organella.app.settings import Settings
from organella.core import Kind, RowType, Sample, Structure, VoxelSize
from organella.sinks import ParquetSink
from organella.sinks.parts import ParquetParts


class Counting:
    """A measurement that counts the samples it was asked about, and can be told to fail on one."""

    def __init__(self, fail_on=()):
        self.seen, self.fail_on = [], set(fail_on)

    def measure(self, sample, settings, shared):
        if sample.id in self.fail_on:
            raise RuntimeError("broken on purpose")
        self.seen.append(sample.id)
        return {RowType.OBJECT: pl.DataFrame({"size_x": [sample.shape[-1]]})}


class Source:
    def __init__(self, *ids):
        self.ids = ids

    def samples(self):
        for i in self.ids:
            data = da.from_array(np.zeros((4, 4, 4), np.uint8), chunks=4)
            sample = Sample(i, None, VoxelSize((0.1,) * 3), {"m": Structure("m", Kind.MASK, data)})
            yield SampleRef(i, lambda sample=sample: sample)


def run(tmp_path, source, measurement, *, resume, settings=Settings(), name="r"):
    return process(source, [measurement], ParquetSink(tmp_path / f"{name}.parquet"), settings, None,
                   ParquetParts(tmp_path / f"{name}_parts", reuse=resume))


def test_a_failed_run_keeps_what_it_measured_and_resuming_measures_only_the_rest(tmp_path):
    first = Counting(fail_on={"b"})
    assert list(run(tmp_path, Source("a", "b", "c"), first, resume=False)) == ["b"]
    assert first.seen == ["a", "c"] and (tmp_path / "r_parts" / "a.parquet").is_file() and not (tmp_path / "r_parts" / "b.parquet").exists()
    second = Counting()
    assert run(tmp_path, Source("a", "b", "c"), second, resume=True) == {}
    assert second.seen == ["b"]                                                 # a and c were not measured again
    assert sorted(pl.read_parquet(tmp_path / "r.parquet")["object_id"]) == ["a", "b", "c"]
    assert not (tmp_path / "r_parts").exists()                                   # the report exists, so the insurance is gone


def test_without_resume_every_sample_is_measured_again(tmp_path):
    run(tmp_path, Source("a"), Counting(fail_on={"zzz"}), resume=False)          # leaves a part behind only if something failed
    run(tmp_path, Source("a", "b"), Counting(fail_on={"b"}), resume=False)
    again = Counting()
    run(tmp_path, Source("a", "b"), again, resume=False)
    assert again.seen == ["a", "b"]


def test_a_part_made_under_other_settings_is_not_trusted(tmp_path):
    run(tmp_path, Source("a", "b"), Counting(fail_on={"b"}), resume=False, settings=Settings(reach_um=1.0))
    changed = Counting()
    run(tmp_path, Source("a", "b"), changed, resume=True, settings=Settings(reach_um=2.0))
    assert changed.seen == ["a", "b"]


def test_an_unreadable_or_empty_part_is_measured_again(tmp_path):
    parts = ParquetParts(tmp_path / "p", reuse=True)
    parts.folder.mkdir()
    (parts.folder / "a.parquet").write_bytes(b"not parquet")
    pl.DataFrame(schema={"x": pl.Int64, "_organella_settings": pl.String}).write_parquet(parts.folder / "b.parquet")
    assert parts.load("a", "f") is None and parts.load("b", "f") is None and parts.load("missing", "f") is None
