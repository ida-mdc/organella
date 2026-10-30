# Developing organella

```text
src/organella/
  core/       the nouns: VoxelSize, Structure, Sample, row types. No files, no formats.
  app/        what a run does: Settings, the ports (SampleSource, Measurement, ReportSink), process().
  measure/    one module per thing measured; per_label.py and fields.py are the chunked primitives.
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

A new input format is a `SampleSource`. A new measurement is a `Measurement` added to `measure.DEFAULT`.

```bash
uv venv && uv pip install -e '.[test]'
(cd tools/viewer && npm ci)                   # node checks for the viewer tests
organella process cells/ -o report.parquet --object-mask cell
organella view report.parquet
python -m organella.viewer.build page.html   # the standalone page
pytest
```

## The viewer

`src/organella/viewer/src/`, lowest layer first (`layer-order.txt`); a file reads only its own layer or one below.

```text
model/    state, the report as read, columns and their labels, scope (what filters select), stats/
io/       DuckDB, the SQL for geometry, where geometry files come from
ui/       text, colour, theme, navigation
  viewer3d/   the three.js stage, surfaces, instance gallery
  explain/    the docked 3D column and the specs that say what a chart shows there
  charts/     the panels: distributions, pairs, significance marks
  sections/   the report's own sections (overview, structures, contacts, polarity, files, glance)
  app/        loading a file, filters, layout, boot
```

The files are plain scripts joined in the order of `load-order.txt`. `build.py` makes the one-file page.
`tools/viewer/` (`npm ci` there once) holds the checks the tests run: `dependencies.mjs --check` fails on an
undeclared name, a duplicate, a read of a higher layer, a cycle between files, or a read before initialisation;
`load-check.mjs` runs the built script with no DOM.
