# Developing organella

```text
src/organella/
  core/       the nouns: VoxelSize, Structure, Sample, row types. No files, no formats.
  app/        what a run does: Settings, the ports (SampleSource, Measurement, ReportSink), process().
  measure/    one module per thing measured; per_label.py and distance_field.py are the chunked primitives.
  sources/    folders of TIFFs, Zarr/N5 stores, and the cell masks `organella cells` writes -> Sample
  cells/      cutting the cells of a volume apart, and the object folders that describe them
  geometry/   surfaces, skeleton and tube payloads, the geometry rows and files
  report/     the report file: footer, column descriptions, merging reports
  sinks/      parquet
  viewer/     the report page, see below
  cli/        the commands: options.py (shared option groups), process.py, tools.py, survey.py
tools/viewer/ node checks the viewer tests run
examples/     the Jurkat, HeLa, liver and beta-cell batches, and the landing and overview pages
```

Dependencies point inward: `core` <- `app` <- `measure`, `sources`, `sinks`, `viewer` <- `cli`.

A new input format is a `SampleSource`. A run is `process(source, measurements, sink, settings)`: each sample is read,
clipped to its object mask, handed to every `Measurement` in turn, and the rows they return are stamped and written.
`Statistics` runs first and leaves what it found in the `Shared` scratchpad for `Distances` and the geometry.

## Where a number comes from

| Columns | Computed in | Assembled in |
| --- | --- | --- |
| volume, centroid, bounding box, aspect ratio | `measure/per_label.py` (moments per chunk, merged), `polarity.aspect_ratio` | `statistics.py` |
| surface area, sphericity (perimeter, circularity) | `measure/boundary.py` (ITK Crofton estimate) | `statistics.py` |
| `polar_*`, polar spread | `measure/polarity.py` | `statistics.py` |
| `polar_depth_um` | `measure/depth.py` | `statistics.py` |
| branches, length, tortuosity, tips | `measure/skeletons.py` | `statistics.py` |
| `contact_*` | `measure/contacts.py` | `statistics.py` |
| `distance_*`, `baseline_*` | `measure/distances.py` on `distance_field.py` | `distances.py` |

What each column means for a reader is in `report/column_descriptions.py`; `docs/metrics.md` is generated from it
(`python -m organella.report.column_descriptions docs/metrics.md`).

## Adding a metric per instance

1. In `measure/<name>.py`, write `lazy_<name>(data, low, high, label, ...)`, a `dask.delayed` function of the box around
   one instance (`polarity.lazy_spread` is the smallest example), and a function `<name>s(sample, per_structure,
   settings)` that runs it with `over_instances` (`measure/windows.py`).
2. Call it in `Statistics.measure` and put its column in `_instance_rows`.
3. Describe the column in `column_descriptions.py`: a test fails for a column without a description.
4. If it takes an option, add a field to `Settings` and a click option of the same name in `cli/options.py`; nothing
   else maps one to the other. The report page lists every numeric instance column by itself.
5. Test the function on a tiny array with a known answer, next to the module's other tests.

A measurement that needs its own pass over the volume, as `Distances` does, is a `Measurement` added to
`measure.DEFAULT` instead.

```bash
uv venv && uv pip install -e '.[test]'
(cd tools/viewer && npm ci)                   # node checks for the viewer tests
organella process cells/ -o report.parquet --object-mask cell
organella view report.parquet
python -m organella.viewer.build page.html   # the standalone page
pytest
```

## Tests

| File | What it pins down |
| --- | --- |
| `test_measurements.py` | Every measurement on small arrays with a known answer, against scipy where one exists |
| `test_per_label.py`, `test_contacts.py`, `test_boundary.py`, `test_skeletons.py`, `test_polarity.py`, `test_transform.py` | The chunked primitives against the in-memory answer, across chunk borders |
| `test_process.py`, `test_resume.py`, `test_report_file.py`, `test_commands.py` | A run end to end: the report, its footer, resuming, the commands |
| `test_formats.py`, `test_store_source.py`, `test_cells.py` | Reading input: TIFF, NIfTI, label maps, N5 and Zarr, cells cut from a volume |
| `test_geometry.py`, `test_surfaces.py`, `test_payloads.py` | The geometry file and the surfaces in it |
| `test_viewer.py` | The page builds, its files respect their layers, and a report draws in headless Chrome |

`conftest.py` makes one small object folder and the report measured from it; `tests/synthetic.py` writes the same kind of batch for CI's install job.

## The viewer

`src/organella/viewer/src/`, lowest layer first (`layer-order.txt`); a file reads only its own layer or one below.

```text
model/    state, the report as read, columns and their labels, scope (what filters select), stats/
io/       DuckDB, the SQL for geometry, where geometry files come from
ui/       text, colour, theme, navigation
  viewer3d/   the three.js stage, surfaces, instance gallery, the docked column and its chart specs
  charts/     the panels: distributions, pairs, significance marks
  sections/   the report's own sections (overview, structures, contacts, polarity, files, glance)
  app/        loading a file, filters, layout, boot
```

The files are plain scripts joined in the order of `load-order.txt`. `build.py` makes the one-file page.
`tools/viewer/` (`npm ci` there once) holds the checks the tests run: `dependencies.mjs --check` fails on an
undeclared name, a duplicate, a read of a higher layer, a cycle between files, or a read before initialisation;
`load-check.mjs` runs the built script with no DOM.
