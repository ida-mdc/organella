#!/bin/sh
# The Jurkat batch as one job per cell: one job cuts the cells apart, the 11 measuring jobs then run side by side, and a
# last one merges their reports into <OUT>/jurkat.parquet. The wall time is that of the longest cell, not the sum.
#
#   SCALE=s1 CHUNK_EDGE=192 THREADS=16 MEM=192G TIME=1-00:00:00 sh examples/jurkat_submit_all.sh OUT
#
# Run from organella_next/, on the login node. MEM and TIME are asked for each measuring job; the other settings are
# those of jurkat_slurm.sh and reach it as they are. CELLS (default all 11) limits which cells are submitted. A cell
# that fails stops the merge: submit that cell again with STEP=cell, then the merge with STEP=merge.
set -eu

out=${1:?usage: sh examples/jurkat_submit_all.sh OUT}
cells=${CELLS:-"jurkat_01 jurkat_02 jurkat_03 jurkat_04 jurkat_05 jurkat_06 jurkat_07 jurkat_08 jurkat_09 jurkat_10 jurkat_11"}
script=examples/jurkat_slurm.sh

cut=$(STEP=cells sbatch --parsable --job-name=jurkat-cut --mem=64G --time=01:00:00 --export=ALL "$script" "$out")
echo "cut apart: job $cut"

waiting=""
for cell in $cells; do
    job=$(STEP=cell CELL=$cell sbatch --parsable --job-name="$cell" --dependency=afterok:"$cut" --mem="${MEM:-128G}" \
        --time="${TIME:-1-00:00:00}" --export=ALL "$script" "$out")
    echo "$cell: job $job"
    waiting="$waiting:$job"
done

merge=$(STEP=merge sbatch --parsable --job-name=jurkat-merge --dependency=afterok"$waiting" --mem=16G --time=00:30:00 \
    --export=ALL "$script" "$out")
echo "merge: job $merge, then: organella view $out/jurkat.parquet"
