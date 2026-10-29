#!/bin/bash
# The 11 Jurkat cells of OpenOrganelle's jrc_jurkat-1, read straight from S3: cut apart, then measured with contacts,
# skeletons of the mitochondria and 3D geometry.
#
#   sbatch examples/jurkat_slurm.sh OUT
#   SCALE=s1 ONLY=jurkat_08 sbatch --mem=128G --time=1-00:00:00 examples/jurkat_slurm.sh OUT_s1
#
# Run from organella_next/. OUT gets <OUT>/cells (a manifest and a mask per cell), the report <OUT>/jurkat.parquet and
# its geometry <OUT>/jurkat_meshes. A job that is killed and submitted again carries on where it stopped (--resume).
# One OUT per scale: a report at one scale is not comparable with one at another.
#
# SCALE      the scale level every cell is measured at (default s3: 27.5 x 32 x 32 nm; s2 is 8 times the voxels, s1 64).
# ONLY       comma-separated cells to measure, e.g. jurkat_08; default all 11. All 11 are still cut apart.
# CHUNK_EDGE sets --chunk-edge: the reach (1 um) must fit in it, which is 73 voxels at s2 and 146 at s1, so s1 needs
# 192 or more; memory per thread grows with the cube of it, so give THREADS fewer then.
# THREADS and CACHE_GB set --threads and --cache-gb. At s3 a cell peaks at about 5 GB, at s2 about 13 GB for cell 8
# (the others are bigger, and it grows with the voxels); the memory request below is for s3.
#SBATCH --job-name=jurkat
#SBATCH --partition=normal
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --output=jurkat-%j.log
set -eu

out=${1:?usage: sbatch examples/jurkat_slurm.sh OUT}
scale=${SCALE:-s3}
threads=${THREADS:-$SLURM_CPUS_PER_TASK}
cache_gb=${CACHE_GB:-4}
chunk_edge=${CHUNK_EDGE:-128}
store=s3://janelia-cosem-datasets/jrc_jurkat-1/jrc_jurkat-1.n5

cd "${SLURM_SUBMIT_DIR:-.}"
mkdir -p "$out"

uv run organella cells "$store" "$out/cells" --prefix jurkat --source em/fibsem-uint16 \
    --membrane labels/pm_seg --outside labels/ecs_seg --seed labels/nucleus_seg \
    --scale "$scale" --mask-scale s4 \
    --entity mito=labels/mito_seg --entity mito-mem=labels/mito-mem_seg \
    --entity er=labels/er_seg --entity golgi=labels/golgi_seg --entity lyso=labels/lyso_seg \
    --entity ld=labels/ld_seg --entity endo=labels/endo_seg --entity nucleus=labels/nucleus_seg \
    --instances mito,mito-mem,er,golgi,lyso,ld,endo,nucleus

measure="$out/cells"
if [ -n "${ONLY:-}" ]; then
    measure="$out/selected"
    mkdir -p "$measure"
    for cell in $(echo "$ONLY" | tr ',' ' '); do
        ln -sfn "$(cd "$out/cells/$cell" && pwd)" "$measure/$cell"
    done
fi

uv run organella process "$measure" -o "$out/jurkat.parquet" --object-noun cell \
    --skeletons mito --with-mesh --resume --threads "$threads" --cache-gb "$cache_gb" \
    --chunk-edge "$chunk_edge"

echo "open it with: organella view $out/jurkat.parquet"
