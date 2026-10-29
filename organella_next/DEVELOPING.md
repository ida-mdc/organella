# Developing organella (next)

```text
src/organella/
  core/       the nouns: VoxelSize, Structure, Sample, row types. No files, no formats.
  app/        what a run does: Settings, the ports (SampleSource, Measurement, ReportSink), process().
  measure/    one module per thing measured; labelled.py and fields.py are the chunked primitives.
  sources/    folders of TIFFs, Zarr/N5 stores -> Sample
  sinks/      parquet
  viewer/     the report page, see below
  cli.py
```

Dependencies point inward: `core` <- `app` <- `measure`, `sources`, `sinks`, `viewer` <- `cli`.

A new input format is a `SampleSource`. A new measurement is a `Measurement` added to `measure.DEFAULT`.

```bash
uv venv && uv pip install -e '.[test]'
organella process cells/ -o report.parquet --object-mask cell
organella view report.parquet
python -m organella.viewer.build page.html   # the standalone page
pytest
```

## The viewer

`src/organella/viewer/src/`, lowest layer first (`layers.txt`); a file reads only its own layer or one below.

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

The files are still plain scripts joined in the order of `sections.txt`. `build.py` makes the one-file page.
`tools/viewer/` (`npm install` there once) holds the checks the tests run: `dependencies.mjs --check` fails on an
undeclared name, a duplicate, a read of a higher layer, a cycle between files, or a read before initialisation;
`load-check.mjs` runs the built script with no DOM.
