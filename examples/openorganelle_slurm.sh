#!/bin/bash
# One OpenOrganelle volume, every cell in it, measured with contacts, skeletons (HeLa) and 3D geometry: the HeLa cells of
# Heinrich et al. (Nature 2021) and the mouse-liver volumes of Parlakgül, Arruda et al. (Nature 2022).
#
#   sbatch examples/openorganelle_slurm.sh DATASET OUT
#   sh examples/openorganelle_submit_all.sh OUT           all four, side by side
#
# DATASET is jrc_hela-2, jrc_hela-3, jrc_mus-liver-6 or jrc_mus-liver-7. Run from the repository root. OUT gets
# <OUT>/<dataset>/cells (a manifest and a small mask per cell), the report <OUT>/<dataset>.parquet and its geometry
# <OUT>/<dataset>_meshes. Nothing is downloaded: the cells are read from S3 by the job. A job that is killed and submitted
# again carries on where it stopped (--resume), so a time limit costs only the cell it was in.
#
# SCALE       the scale every cell is measured at (default s2 for all four: HeLa 16 nm laterally and 21 nm in z, liver
#             32 nm). The papers measured at 4 and 8 nm (s0 and s1); a finer one takes more of everything, see below.
# MASK_SCALE  a coarser scale the cells are cut apart at, read whole (default s3 for HeLa, s4 for liver).
# MIN_UM3     liver only: cells smaller than this are the slivers of cells the volume cuts off (default 300).
# REACH_UM    how far distances are exact (default 1). The reach must fit in CHUNK_EDGE voxels: 1 um is 63 voxels at HeLa
#             s2 and 31 at liver s2; at HeLa s1 it is 125 and needs CHUNK_EDGE=192, and at s0 use REACH_UM=0.5.
# THREADS, CACHE_GB, CHUNK_EDGE   --threads, --cache-gb and --chunk-edge (defaults 16, 4 and 128).
#
# This runs every cell of the volume one after another in one job. To run the cells side by side, one job each, see
# openorganelle_submit_cells.sh.
#
# Resources. The liver arrays are all masks, so a liver cell has no instances: its rows are the structures' volumes,
# surfaces and how deep each sits, and it is the voxels that cost. A HeLa cell has thousands of instances, each measured in
# its own box, and the instances are what take the time. The time and memory below are what the Jurkat cells needed at a
# comparable number of voxels (58 GB and 16 minutes for 1.4 G voxels at s1), not measured on these volumes: look at the
# first job's seff and adjust.
#SBATCH --job-name=openorganelle
#SBATCH --partition=normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=160G
#SBATCH --time=3-00:00:00
#SBATCH --output=openorganelle-%j.log
set -eu

dataset=${1:?usage: sbatch examples/openorganelle_slurm.sh DATASET OUT}
out=${2:?usage: sbatch examples/openorganelle_slurm.sh DATASET OUT}
threads=${THREADS:-${SLURM_CPUS_PER_TASK:-4}}
cache_gb=${CACHE_GB:-4}
chunk_edge=${CHUNK_EDGE:-128}
reach=${REACH_UM:-1}

cd "${SLURM_SUBMIT_DIR:-.}"
mkdir -p "$out/$dataset"
. examples/openorganelle_lib.sh
openorganelle_cut "$dataset" "$out"

uv run organella process "$out/$dataset/cells" -o "$out/$dataset.parquet" --object-noun cell --with-mesh ${extra[@]+"${extra[@]}"} \
    --reach-um "$reach" --resume --threads "$threads" --cache-gb "$cache_gb" --chunk-edge "$chunk_edge"
echo "report: $out/$dataset.parquet"
