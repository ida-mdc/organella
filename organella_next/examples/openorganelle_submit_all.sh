#!/bin/sh
# The four OpenOrganelle volumes, one job each, side by side. Each writes <OUT>/<dataset>.parquet and its geometry.
#
#   sh examples/openorganelle_submit_all.sh OUT
#
# Run from organella_next/, on the login node. Settings (SCALE, REACH_UM, THREADS, ...) are those of
# openorganelle_slurm.sh and reach it as they are; MEM and TIME are asked for each job. DATASETS limits which are submitted.
set -eu

out=${1:?usage: sh examples/openorganelle_submit_all.sh OUT}
for dataset in ${DATASETS:-jrc_hela-2 jrc_hela-3 jrc_mus-liver-6 jrc_mus-liver-7}; do
    job=$(sbatch --parsable --job-name="${dataset#jrc_}" --mem="${MEM:-160G}" --time="${TIME:-3-00:00:00}" --export=ALL \
        examples/openorganelle_slurm.sh "$dataset" "$out")
    echo "$dataset: job $job"
done
