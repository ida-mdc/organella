#!/bin/bash
# The 11 Jurkat cells of OpenOrganelle's jrc_jurkat-1, read straight from S3: cut apart, then measured with contacts,
# skeletons of the mitochondria and 3D geometry.
#
#   sbatch examples/jurkat_slurm.sh OUT
#
# Run from organella_next/. OUT gets <OUT>/cells (a manifest and a mask per cell), the report <OUT>/jurkat.parquet and
# its geometry <OUT>/jurkat_meshes. A job that is killed and submitted again carries on where it stopped (--resume).
#
# THREADS and CACHE_GB set --threads and --cache-gb. A cell peaks at about 5 GB with the defaults; the memory
# request below leaves room for the cache.
#SBATCH --job-name=jurkat
#SBATCH --partition=normal
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --output=jurkat-%j.log
set -eu

out=${1:?usage: sbatch examples/jurkat_slurm.sh OUT}
threads=${THREADS:-$SLURM_CPUS_PER_TASK}
cache_gb=${CACHE_GB:-4}
store=s3://janelia-cosem-datasets/jrc_jurkat-1/jrc_jurkat-1.n5

cd "${SLURM_SUBMIT_DIR:-.}"
mkdir -p "$out"

uv run organella cells "$store" "$out/cells" --prefix jurkat --source em/fibsem-uint16 \
    --membrane labels/pm_seg --outside labels/ecs_seg --seed labels/nucleus_seg \
    --scale s3 --mask-scale s4 \
    --entity mito=labels/mito_seg --entity mito-mem=labels/mito-mem_seg \
    --entity er=labels/er_seg --entity golgi=labels/golgi_seg --entity lyso=labels/lyso_seg \
    --entity ld=labels/ld_seg --entity endo=labels/endo_seg --entity nucleus=labels/nucleus_seg \
    --instances mito,mito-mem,er,golgi,lyso,ld,endo,nucleus

uv run organella process "$out/cells" -o "$out/jurkat.parquet" --object-noun cell \
    --skeletons mito --with-mesh --resume --threads "$threads" --cache-gb "$cache_gb"

echo "open it with: organella view $out/jurkat.parquet"
