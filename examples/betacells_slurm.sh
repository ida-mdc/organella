#!/bin/bash
# Mitochondria instances of the beta cells on a GPU node (see betacells_mito.py).
#
# Submit from the root of the repository, so that the script is found:
#
#   sbatch examples/betacells_slurm.sh DATA OUT [CELLS]
#
# DATA is the betacells folder holding low/ and high/; OUT is where the results go and, while it runs,
# about 17 GB per cell of intermediates (OUT/_work), so put it on fast scratch. CELLS is a comma list
# such as high_c2; leave it out for all seven. A job that is killed and submitted again resumes from the
# last finished tile.
#
# The first `uv run` builds the environment into uv's default cache in $HOME, a few GB. Do it once on
# the login node first, which has internet:  uv run examples/betacells_mito.py --help
#SBATCH --job-name=betacells-mito
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=12:00:00
#SBATCH --output=mito-%j.log
set -eu

data=${1:?usage: sbatch examples/betacells_slurm.sh DATA OUT [CELLS]}
out=${2:?usage: sbatch examples/betacells_slurm.sh DATA OUT [CELLS]}
cells=${3:-}

cd "$SLURM_SUBMIT_DIR"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

uv run examples/betacells_mito.py "$data" "$out" ${cells:+--cells "$cells"} --tile 1024
