#!/bin/sh
# Re-run the published β-cell report (Müller et al., J Cell Biol 2021) from the public data.
#
# Download each cell's folder from https://betaseg.github.io/ into DATA/low/low_c1, ... /high/high_c4,
# each holding <cell>.tif (the EM) and <cell>_<structure>_{mask,labels}.tif. The masks are
# 16 nm isotropic, which the TIFFs do not say, so the voxel size has to be given.
#
#   sh examples/betacells.sh DATA [OUT.parquet]
set -eu
data=${1:?folder holding low/ and high/}
out=${2:-betacells.parquet}

organella process "$data" -o "$out" --object-mask membrane --object-noun cell \
    --with-mesh --distance-histograms --voxel-size-um 0.016,0.016,0.016 \
    --baseline-exclude nucleus --geometry-as microtubules=tube+skeleton
