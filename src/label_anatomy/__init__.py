"""label-anatomy: the spatial anatomy of segmented objects.

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

A report is read by ``report/anatomy_report.html``, one standalone page that loads the
parquet in the browser and draws every chart from it; ``report_page.py`` finds it and can
serve it beside a report so the 3D views reach the geometry.

This package started as a PixelPatrol flavour and no longer depends on it. The measuring
was always its own - an object cannot be split across chunks, and every measurement needs
the whole of it at once - and the report is now written and read here too. A report written
under the old footer keys still reads; see :mod:`label_anatomy.report_io`.
"""

__version__ = "0.1.0"
