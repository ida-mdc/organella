#!/bin/sh
# The 11 Jurkat cells of OpenOrganelle's jrc_jurkat-1, read straight from S3: nothing is
# downloaded, and the only files written are one small manifest and mask per cell, the report
# and its geometry. Most cells are cut off by the edge of the volume, so a volume here is a
# lower bound; the manifests say which cells are.
#
#   sh examples/jurkat.sh [WORKDIR]       # about 4 minutes on 22 cores; up to ~20 GB RAM per worker
set -eu
work=${1:-jurkat_example}
store=s3://janelia-cosem-datasets/jrc_jurkat-1/jrc_jurkat-1.n5

organella cells "$store" "$work/cells" --prefix jurkat --source em/fibsem-uint16 \
    --membrane labels/pm_seg --outside labels/ecs_seg --seed labels/nucleus_seg \
    --scale s3 --mask-scale s4 \
    --entity mito=labels/mito_seg --entity mito-mem=labels/mito-mem_seg \
    --entity er=labels/er_seg --entity golgi=labels/golgi_seg --entity lyso=labels/lyso_seg \
    --entity ld=labels/ld_seg --entity endo=labels/endo_seg --entity nucleus=labels/nucleus_seg \
    --instances mito,mito-mem,er,golgi,lyso,ld,endo,nucleus

organella process "$work/cells" -o "$work/jurkat.parquet" --object-noun cell \
    --skeletons mito --with-mesh

echo "open it with: organella view $work/jurkat.parquet"
