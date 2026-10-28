#!/bin/bash
# One OpenOrganelle volume, every cell in it, measured at a fine scale with meshes: the HeLa cells of Heinrich et al.
# (Nature 2021) and the mouse-liver volumes of Parlakgül, Arruda et al. (Nature 2022).
#
#   sbatch examples/openorganelle_slurm.sh DATASET OUT
#
# DATASET is jrc_hela-2, jrc_hela-3, jrc_mus-liver-6 or jrc_mus-liver-7. Run from the root of the repository. OUT gets
# <OUT>/<dataset>/cells (a manifest and a mask per cell), the report <OUT>/<dataset>.parquet and its meshes
# <OUT>/<dataset>_meshes. Nothing is downloaded: the cells are read from S3 by the job. A job that is killed and
# submitted again carries on where it stopped (--resume), so a time limit costs only the cell it was in.
#
# One job per dataset, so four in all:  for d in jrc_hela-2 jrc_hela-3 jrc_mus-liver-6 jrc_mus-liver-7; do sbatch examples/openorganelle_slurm.sh $d OUT; done
#
# This needs no GPU, so it runs on the default partition, "normal". Pass --mem for the node you want, for example
#   sbatch --mem=500G examples/openorganelle_slurm.sh jrc_hela-2 $OUT
# and set them to match SCALE and WORKERS below.
#
# SCALE      the scale every cell is measured at; default s2 for both (HeLa 16 nm laterally and 21 nm in z, liver 32 nm).
#            The papers measured at 4 and 8 nm; see Memory for why that is not the default.
# MASK_SCALE a coarser scale the cells are cut apart at, read whole (default s3 for HeLa, s4 for liver).
# WORKERS    objects measured side by side (default 1, which is what 400G holds at s2); MESH_WORKERS the processes meshing inside each (default 8).
# MIN_UM3    liver only: cells smaller than this are the slivers of cells the volume cuts off (default 300).
#
# Memory. The largest cell sets it, per worker, and it grows with the voxel count: one scale finer is eight times
# the memory. `organella process` prints the figure for the largest cell as its first line. At s3 it printed 21 GB for
# the HeLa cells and 36 GB for the liver cells (the largest, 83,000 µm³, may be several cells merged), so s2 is about
# 170 GB and 285 GB, s1 about 1.3 TB and 2.3 TB. Those are why s2 is the default. For s1 or s0, bring the cells down
# first: WORKERS=1, a higher MIN_UM3, or sub-volumes, which this script does not cut. The contacts the papers report
# (10 nm for HeLa, 0-16 nm for the liver) need s1 or finer, so they need that work.
#
# Scale and report must stay together: a report written at one scale is not comparable with one at another.
#SBATCH --job-name=openorganelle
#SBATCH --partition=normal
#SBATCH --cpus-per-task=32
#SBATCH --mem=400G
#SBATCH --time=3-00:00:00
#SBATCH --output=openorganelle-%j.log
set -eu

dataset=${1:?usage: sbatch examples/openorganelle_slurm.sh DATASET OUT}
out=${2:?usage: sbatch examples/openorganelle_slurm.sh DATASET OUT}
workers=${WORKERS:-1}
mesh_workers=${MESH_WORKERS:-8}
bucket=s3://janelia-cosem-datasets

cd "${SLURM_SUBMIT_DIR:-.}"
mkdir -p "$out/$dataset"
uvrun="uv run --extra remote"

case "$dataset" in
jrc_hela-2|jrc_hela-3)
    scale=${SCALE:-s2}
    mask_scale=${MASK_SCALE:-s3}
    # The cells are derived: the membrane and the space around bound a cell, the nucleus seeds it, a watershed cuts
    # touching ones apart. The label arrays are instance maps, so every structure but the nucleus' own is an instance.
    $uvrun organella cells "$bucket/$dataset/$dataset.n5" "$out/$dataset/cells" --prefix "${dataset#jrc_}" \
        --source em/fibsem-uint16 --membrane labels/pm_seg --outside labels/ecs_seg --seed labels/nucleus_seg \
        --scale "$scale" --mask-scale "$mask_scale" \
        --entity mito=labels/mito_seg --entity mito-mem=labels/mito-mem_seg --entity er=labels/er_seg \
        --entity golgi=labels/golgi_seg --entity lyso=labels/lyso_seg --entity ld=labels/ld_seg \
        --entity endo=labels/endo_seg --entity vesicle=labels/vesicle_seg --entity nucleus=labels/nucleus_seg \
        --instances mito,mito-mem,er,golgi,lyso,ld,endo,vesicle,nucleus
    extra=""
    ;;
jrc_mus-liver-6|jrc_mus-liver-7)
    scale=${SCALE:-s2}
    mask_scale=${MASK_SCALE:-s4}
    # The volume ships a segmentation of its cells, so they are cut from that, not derived. Its structure arrays
    # are binary: liver_cells.py declares which are instances (mito, ld, nuc_mem) and leaves the ER one mask.
    $uvrun python examples/liver_cells.py "$dataset" "$out/$dataset/cells" \
        --scale "$scale" --mask-scale "$mask_scale" --min-um3 "${MIN_UM3:-300}"
    extra=""
    ;;
*)
    echo "unknown dataset $dataset (jrc_hela-2, jrc_hela-3, jrc_mus-liver-6, jrc_mus-liver-7)" >&2
    exit 2
    ;;
esac

# shellcheck disable=SC2086
$uvrun organella process "$out/$dataset/cells" -o "$out/$dataset.parquet" --object-noun cell --with-mesh \
    $extra --max-workers "$workers" --mesh-workers "$mesh_workers" --resume
echo "report: $out/$dataset.parquet"
