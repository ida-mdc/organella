#!/bin/bash
# One OpenOrganelle volume, every cell in it, measured with contacts, skeletons (HeLa) and 3D geometry: the HeLa cells of
# Heinrich et al. (Nature 2021) and the mouse-liver volumes of Parlakgül, Arruda et al. (Nature 2022).
#
#   sbatch examples/openorganelle_slurm.sh DATASET OUT
#   sh examples/openorganelle_submit_all.sh OUT           all four, side by side
#
# DATASET is jrc_hela-2, jrc_hela-3, jrc_mus-liver-6 or jrc_mus-liver-7. Run from organella_next/. OUT gets
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
bucket=s3://janelia-cosem-datasets

cd "${SLURM_SUBMIT_DIR:-.}"
mkdir -p "$out/$dataset"

case "$dataset" in
jrc_hela-2|jrc_hela-3)
    scale=${SCALE:-s2}
    mask_scale=${MASK_SCALE:-s3}
    # The cells are derived: the membrane and the space around bound a cell, the nucleus seeds it, a watershed cuts
    # touching ones apart. The label arrays are instance maps, except the nucleus' own, which holds one id: it is a mask,
    # a whole structure. The seed size is 2 um3: at the default of 40 the fragments of a neighbouring cell's nucleus are
    # merged into the cell beside it.
    uv run organella cells "$bucket/$dataset/$dataset.n5" "$out/$dataset/cells" --prefix "${dataset#jrc_}" \
        --source em/fibsem-uint16 --membrane labels/pm_seg --outside labels/ecs_seg --seed labels/nucleus_seg \
        --scale "$scale" --mask-scale "$mask_scale" --min-seed-um3 2 \
        --entity mito=labels/mito_seg --entity mito-mem=labels/mito-mem_seg --entity er=labels/er_seg \
        --entity golgi=labels/golgi_seg --entity lyso=labels/lyso_seg --entity ld=labels/ld_seg \
        --entity endo=labels/endo_seg --entity vesicle=labels/vesicle_seg --entity nucleus=labels/nucleus_seg \
        --instances mito,mito-mem,er,golgi,lyso,ld,endo,vesicle --masks nucleus
    extra=(--skeletons mito)
    ;;
jrc_mus-liver-6|jrc_mus-liver-7)
    scale=${SCALE:-s2}
    mask_scale=${MASK_SCALE:-s4}
    # The volume ships a segmentation of its cells, so they are cut from that, not derived. Its structure arrays are
    # binary, so liver_cells.py declares every structure a mask.
    uv run python examples/liver_cells.py "$dataset" "$out/$dataset/cells" \
        --scale "$scale" --mask-scale "$mask_scale" --min-um3 "${MIN_UM3:-300}"
    extra=()
    ;;
*)
    echo "unknown dataset $dataset (jrc_hela-2, jrc_hela-3, jrc_mus-liver-6, jrc_mus-liver-7)" >&2
    exit 2
    ;;
esac

uv run organella process "$out/$dataset/cells" -o "$out/$dataset.parquet" --object-noun cell --with-mesh ${extra[@]+"${extra[@]}"} \
    --reach-um "$reach" --resume --threads "$threads" --cache-gb "$cache_gb" --chunk-edge "$chunk_edge"
echo "report: $out/$dataset.parquet"
