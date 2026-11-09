"""Make the synthetic cells, measure them and open the report in the browser.

    python examples/synthetic.py                  # 3D, into ./synthetic_demo, then serve the report
    python examples/synthetic.py --2d --no-browser
    python examples/synthetic.py --scale 3        # 3D cells sampled 3x finer: big meshes, so the viewer's levels of detail show
    python examples/synthetic.py --out /some/folder

The cells are drawn by ``tests/synthetic.py`` (seeded, so the same every time; not biology). The measuring step is the
same command line as for real data: ``organella process ... --with-mesh``, then ``organella view``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import tifffile
from click.testing import CliRunner
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import numpy as np                                # noqa: E402
import synthetic                                      # noqa: E402  (tests/synthetic.py)

from organella.cli import main                        # noqa: E402


def make_scaled(root: Path, factor: int) -> None:
    """The 3D batch with every voxel cut into ``factor`` per axis (smooth masks, labels copied), the voxel size divided to match."""
    spec = synthetic.SPEC[3]
    voxel = [v / factor for v in spec["voxel"]]
    for group, names in synthetic.OBJECTS.items():
        for name in names:
            folder = root / group / name
            folder.mkdir(parents=True, exist_ok=True)
            shape = tuple(n * factor for n in spec["shape"])
            tifffile.imwrite(folder / "sample.tif", np.zeros(shape, np.uint8), imagej=True, resolution=(1 / voxel[2], 1 / voxel[1]),
                             metadata={"unit": "micron", "spacing": voxel[0]})
            for structure, array in synthetic.arrays_of(3, group, name).items():
                if structure in synthetic.LABELLED:
                    big = ndimage.zoom(array, factor, order=0)
                else:
                    big = (ndimage.zoom(ndimage.gaussian_filter(array.astype(np.float32), 1.0), factor, order=1) > 0.5).astype(np.uint8)
                suffix = "label" if structure in synthetic.LABELLED else "mask"
                tifffile.imwrite(folder / f"sample_{structure}_{suffix}.tif", big)


def run(arguments: list[str]) -> None:
    result = CliRunner().invoke(main, arguments, catch_exceptions=False)
    print(result.output, end="")
    if result.exit_code:
        raise SystemExit(result.exit_code)


parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--out", type=Path, default=Path("synthetic_demo"))
parser.add_argument("--2d", dest="flat", action="store_true", help="the 2D batch instead of the 3D one")
parser.add_argument("--scale", type=int, default=4, help="3D only: sample each axis this many times finer (3 gives meshes with levels of detail)")
parser.add_argument("--no-browser", action="store_true", help="serve without opening a browser tab")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()

ndim = 2 if args.flat else 3
batch, report = args.out / f"batch{ndim}d", args.out / f"report{ndim}d.parquet"
if args.scale > 1 and ndim == 3:
    make_scaled(batch, args.scale)
else:
    synthetic.make_batch(batch, ndim)
run(["process", str(batch), "-p", "control", "-p", "treated", "-o", str(report),
     "--object-mask", "pm", "--object-noun", "cell", "--skeletons", "mito", "--with-mesh"])
view = ["view", str(report), "--port", str(args.port)] + (["--no-browser"] if args.no_browser else [])
run(view)
