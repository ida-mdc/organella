#!/bin/bash
# One step of an OpenOrganelle volume measured with every cell as a job of its own. Not submitted by hand: see
# openorganelle_submit_cells.sh, which submits the three steps with their dependencies.
#
#   cells   cut the volume into one folder per cell, <OUT>/<dataset>/cells/<cell>/source.json
#   cell    one task of a job array: measure the cell this task's index picks (the n-th folder, in name order) into
#           <OUT>/<dataset>/reports/<cell>.parquet, its geometry into <OUT>/<dataset>_meshes/<cell>/. A task past the last
#           cell does nothing. CELLS="a b" picks from those names instead, so one big cell can be run again alone.
#   merge   join the cells' reports into <OUT>/<dataset>.parquet, the report the single-job script writes, and say which
#           cells have none
#
# The measuring settings (SCALE, MASK_SCALE, MIN_UM3, REACH_UM, THREADS, CACHE_GB, CHUNK_EDGE) are those of
# openorganelle_slurm.sh. A cell that was killed and is submitted again carries on where it stopped (--resume).
#SBATCH --partition=normal
set -eu

step=${1:?usage: openorganelle_cells_slurm.sh cells|cell|merge DATASET OUT}
dataset=${2:?usage: openorganelle_cells_slurm.sh cells|cell|merge DATASET OUT}
out=${3:?usage: openorganelle_cells_slurm.sh cells|cell|merge DATASET OUT}
threads=${THREADS:-${SLURM_CPUS_PER_TASK:-4}}
cache_gb=${CACHE_GB:-4}
chunk_edge=${CHUNK_EDGE:-128}
reach=${REACH_UM:-1}

cd "${SLURM_SUBMIT_DIR:-.}"
. examples/openorganelle_lib.sh
cells="$out/$dataset/cells"
reports="$out/$dataset/reports"

names() {   # the cells of this volume, one per line
    if [ -n "${CELLS:-}" ]; then printf '%s\n' $CELLS; else ls "$cells"; fi
}

case "$step" in
cells)
    mkdir -p "$out/$dataset"
    openorganelle_cut "$dataset" "$out"
    echo "$(ls "$cells" | wc -l) cell(s) in $cells"
    ;;
cell)
    task=${SLURM_ARRAY_TASK_ID:?this step is a task of a job array}
    name=$(names | sed -n "${task}p")
    if [ -z "$name" ]; then echo "task $task: no cell with that number, nothing to do"; exit 0; fi
    openorganelle_settings "$dataset"
    mkdir -p "$reports"
    uv run organella process "$cells/$name/source.json" -o "$reports/$name.parquet" --mesh-dir "$out/${dataset}_meshes" \
        --object-noun cell --with-mesh ${extra[@]+"${extra[@]}"} --reach-um "$reach" --resume --threads "$threads" \
        --cache-gb "$cache_gb" --chunk-edge "$chunk_edge"
    echo "report: $reports/$name.parquet"
    ;;
merge)
    for name in $(ls "$cells"); do
        [ -f "$reports/$name.parquet" ] || echo "no report for $name: its job failed or has not run" >&2
    done
    uv run organella merge "$reports"/*.parquet -o "$out/$dataset.parquet"
    echo "report: $out/$dataset.parquet"
    ;;
*)
    echo "unknown step $step (cells, cell, merge)" >&2
    exit 2
    ;;
esac
