#!/bin/sh
# The OpenOrganelle volumes with every cell as a job of its own: one job cuts a volume into cells, a job array measures them
# side by side, and a last job joins the reports into <OUT>/<dataset>.parquet, with the geometry in <OUT>/<dataset>_meshes.
#
#   sh examples/openorganelle_submit_cells.sh OUT
#
# Run from the repository root, on the login node. The environment is synced once here and the jobs are told not to sync it
# again, so jobs starting together cannot rebuild it under each other.
#
# DATASETS    which volumes (default: jrc_hela-2 jrc_hela-3 jrc_mus-liver-6 jrc_mus-liver-7)
# SCALE, MASK_SCALE, MIN_UM3, REACH_UM, CHUNK_EDGE, THREADS, CACHE_GB   as in openorganelle_slurm.sh, passed on as they are
# MEM, TIME   what each cell's job asks for (default 160G and 2-00:00:00); CPUS, default 16
# CUT_MEM, CUT_TIME   what the cutting job asks for (default 64G and 12:00:00)
# MAX_CELLS   how many array tasks to submit (default 64); a task past the last cell ends at once
# AT_ONCE     the most cell jobs running at once (default: no limit)
# CELLS       run only these cells ("hela-2_01"); the volume must already have been cut, so use DATASETS with one volume,
#             and this submits the cell jobs and the merge, not the cutting
set -eu

out=${1:?usage: sh examples/openorganelle_submit_cells.sh OUT}
uv sync --quiet
export UV_NO_SYNC=1
script=examples/openorganelle_cells_slurm.sh
for dataset in ${DATASETS:-jrc_hela-2 jrc_hela-3 jrc_mus-liver-6 jrc_mus-liver-7}; do
    short=${dataset#jrc_}
    after=""
    if [ -z "${CELLS:-}" ]; then
        cut=$(sbatch --parsable --job-name="cut-$short" --cpus-per-task="${CPUS:-16}" --mem="${CUT_MEM:-64G}" \
            --time="${CUT_TIME:-12:00:00}" --output="cut-$short-%j.log" --export=ALL "$script" cells "$dataset" "$out")
        after="--dependency=afterok:$cut"
    fi
    if [ -n "${CELLS:-}" ]; then tasks=1-$(echo $CELLS | wc -w); else tasks=1-${MAX_CELLS:-64}; fi
    [ -n "${AT_ONCE:-}" ] && tasks="$tasks%$AT_ONCE"
    array=$(sbatch --parsable $after --job-name="cell-$short" --array="$tasks" --cpus-per-task="${CPUS:-16}" --mem="${MEM:-160G}" \
        --time="${TIME:-2-00:00:00}" --output="cell-$short-%A_%a.log" --export=ALL "$script" cell "$dataset" "$out")
    # afterany: the report is joined from the cells that finished even if one did not, and the log says which are missing
    merge=$(sbatch --parsable --dependency="afterany:$array" --job-name="merge-$short" --cpus-per-task=2 --mem=16G --time=01:00:00 \
        --output="merge-$short-%j.log" --export=ALL "$script" merge "$dataset" "$out")
    echo "$dataset: cut ${cut:-(skipped)}, cells $array ($tasks), merge $merge"
done
