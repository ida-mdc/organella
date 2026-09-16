"""Organella: the spatial analysis of segmented objects.

Five layers, each one only using the ones above it:

    model/            what the things are: an object (``ObjectStack``), one entity of it,
                      a measurement of one, the kinds of row a report holds, the report.
                      Data and accessors, nothing else.
    analysis/         the measuring itself, in arrays: shapes, distances, gaps, skeletons,
                      meshes. Knows nothing about objects, rows or files.
    measure/          what to record about an object, and reading one off disk. One module
                      per depth of the report: entity rows, instance and distance rows,
                      contact rows, and the geometry file.
    pipeline/         runs the measurers over a batch: how wide to run, what is already
                      measured, and assembling the rows into one table.
    cli.py            the command line, with config.py for the options it sets.

And at the edges, the file itself:

    report_io.py      writes that table as the report parquet, and reads one back.
    column_schema.py  what every column means, gathered from the measurers that fill them.

A report is read by ``report/organella_report.html``, one standalone page that loads the
parquet in the browser and draws every chart from it; ``report_page.py`` finds it and can
serve it beside a report so the 3D views reach the geometry.

It started as a PixelPatrol flavour and no longer depends on it: the measuring was always
its own, since an object cannot be split across chunks, and the report is written and read
here too.
"""

# The one place the version is written. pyproject reads it from here to build the package,
# and report_io stamps it into every report's footer, so the file says which version
# measured it. Two copies drifted the first time one of them was bumped.
__version__ = "0.2.0"
