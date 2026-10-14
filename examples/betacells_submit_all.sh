#!/bin/bash
# One Slurm job per beta cell, so the cells run side by side on different GPUs and one that fails or runs out
# of time does not hold up the others. Each job is betacells_slurm.sh for a single cell.
#
#   sh examples/betacells_submit_all.sh DATA OUT [CELLS]
#
# CELLS is a space-separated list, "high_c1 high_c3" for example; the default is all seven.
# Anything in SBATCH_ARGS goes to every sbatch, for example the GPU:
#
#   SBATCH_ARGS="--gres=gpu:b300:1 --mem=200G" TILE=2048 sh examples/betacells_submit_all.sh DATA OUT
set -eu

data=${1:?usage: sh examples/betacells_submit_all.sh DATA OUT [CELLS]}
out=${2:?usage: sh examples/betacells_submit_all.sh DATA OUT [CELLS]}
cells=${3:-"high_c1 high_c2 high_c3 high_c4 low_c1 low_c2 low_c3"}

for cell in $cells; do
    # shellcheck disable=SC2086
    sbatch --job-name="mito-$cell" ${SBATCH_ARGS:-} --export=ALL,TILE="${TILE:-1024}" \
        examples/betacells_slurm.sh "$data" "$out" "$cell"
done
