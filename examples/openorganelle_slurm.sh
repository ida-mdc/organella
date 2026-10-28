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
#   sbatch --mem=256G examples/openorganelle_slurm.sh jrc_hela-2 $OUT
# and set it to match WORKERS and MESH_WORKERS below.
#
# SCALE      the scale every cell is measured at; default s2 for both (HeLa 16 nm laterally and 21 nm in z, liver 32 nm).
#            The papers measured at 4 and 8 nm (s0); s1 and s0 work the same way and take longer, see Time.
# MASK_SCALE a coarser scale the cells are cut apart at, read whole (default s3 for HeLa, s4 for liver).
# WORKERS    objects measured side by side (default 2); MESH_WORKERS the processes working on the instances inside each
#            (default 15, which with two objects fills the 32 cores).
# MIN_UM3    liver only: cells smaller than this are the slivers of cells the volume cuts off (default 300).
# COARSE_VOXELS, REACH_UM   what --coarse-voxels and --window-reach-um are set to (200000000 and 1).
#
# Memory. The cells are measured a window at a time (--windowed), so memory does not grow with the scale: about 17 GB
# per object for the coarse view the whole-object measurements run on, and about 1 GB for each process working on
# its instances. `organella process` prints the figure for the largest cell as its first line. The cell mask is a TIFF
# and is read whole by every process, one byte per voxel: 1.7 GB at s2 and 14 GB at s1 for the HeLa cells, per process.
# Fewer MESH_WORKERS if that is what a finer scale needs.
#
# Time. Reading is most of it: every structure of a cell is read from S3 a few times over, and a scale finer is eight
# times the voxels, so eight times the transfer and the measuring (which the processes share). Not measured here at any
# scale finer than s3. What the contacts the papers report need (10 nm for HeLa, 0-16 nm for the liver) is s1 or finer.
#
# What a windowed run does and does not give is in the README under "Objects too big to hold". In short: the instance,
# distance and contact rows are at the scale given, and the chance distributions, how deep a structure sits and the
# shape of the mask structures are read off a coarser view of the cell, which coarse_factor on the cell's row says. No
# skeletons. The liver arrays are all masks, so for them that is nearly everything: a windowed liver run is a full-scale
# volume per structure and a coarse view of the rest, which at COARSE_VOXELS=200000000 is not finer than s3.
#
# Scale and report must stay together: a report written at one scale is not comparable with one at another.
#SBATCH --job-name=openorganelle
#SBATCH --partition=normal
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --time=3-00:00:00
#SBATCH --output=openorganelle-%j.log
set -eu

dataset=${1:?usage: sbatch examples/openorganelle_slurm.sh DATASET OUT}
out=${2:?usage: sbatch examples/openorganelle_slurm.sh DATASET OUT}
workers=${WORKERS:-2}
mesh_workers=${MESH_WORKERS:-15}
bucket=s3://janelia-cosem-datasets

cd "${SLURM_SUBMIT_DIR:-.}"
mkdir -p "$out/$dataset"
uvrun="uv run --extra remote"

case "$dataset" in
jrc_hela-2|jrc_hela-3)
    scale=${SCALE:-s2}
    mask_scale=${MASK_SCALE:-s3}
    # The cells are derived: the membrane and the space around bound a cell, the nucleus seeds it, a watershed cuts
    # touching ones apart. The label arrays are instance maps, except the nucleus' own, which holds one id: that
    # one is a mask, a whole structure, since a window cannot split a binary array into its pieces.
    $uvrun organella cells "$bucket/$dataset/$dataset.n5" "$out/$dataset/cells" --prefix "${dataset#jrc_}" \
        --source em/fibsem-uint16 --membrane labels/pm_seg --outside labels/ecs_seg --seed labels/nucleus_seg \
        --scale "$scale" --mask-scale "$mask_scale" \
        --entity mito=labels/mito_seg --entity mito-mem=labels/mito-mem_seg --entity er=labels/er_seg \
        --entity golgi=labels/golgi_seg --entity lyso=labels/lyso_seg --entity ld=labels/ld_seg \
        --entity endo=labels/endo_seg --entity vesicle=labels/vesicle_seg --entity nucleus=labels/nucleus_seg \
        --instances mito,mito-mem,er,golgi,lyso,ld,endo,vesicle --masks nucleus
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
    $extra --windowed --coarse-voxels "${COARSE_VOXELS:-200000000}" --window-reach-um "${REACH_UM:-1}" \
    --max-workers "$workers" --mesh-workers "$mesh_workers" --resume
echo "report: $out/$dataset.parquet"
