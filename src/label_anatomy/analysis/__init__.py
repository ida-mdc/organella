"""The measuring itself: what a shape, a distance, a gap and a mesh are, in arrays.

Everything here takes numpy arrays and a sample size and returns numbers or payloads. It
knows nothing about objects, rows, reports or files - what to record, and where, is
:mod:`label_anatomy.measure`.

    shapes.py      extent, boundary, roundness, PCA axes, skeleton graph metrics
    distances.py   distance transforms, and the direction one thing lies in from another
    gaps.py        the surface-to-surface gap between every pair of instances
    cache.py       measuring each object's expensive intermediates once
    meshes.py      marching-cubes meshes, 2D outlines, skeleton curves, and their file
    parallel.py    the process pool the meshes are built in, and how wide it should be
"""
