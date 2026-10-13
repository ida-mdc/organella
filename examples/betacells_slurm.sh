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
# the login node first, which has internet. It needs the same variables as the job; for the usual
# V100/A100/H100/H200 nodes:
#   UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu126 UV_INDEX_STRATEGY=unsafe-best-match \
#       uv run examples/betacells_mito.py --check
#
# uv reuses that environment afterwards and does not swap torch for another CUDA build: after moving
# between Volta-to-Hopper and Blackwell nodes, delete ~/.cache/uv/environments-v2/betacells-mito-* once.
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
nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader

# The CUDA build of torch has to match the card: Volta to Hopper (V100, A100, H100, H200) want CUDA 12.6,
# Blackwell (B100, B200; compute capability 10 and up) wants CUDA 13. TORCH_CUDA=cu126|cu130 overrides this.
if [ -z "${TORCH_CUDA:-}" ]; then
    major=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | cut -d. -f1)
    case "$major" in ''|*[!0-9]*) major=0 ;; esac
    if [ "$major" -ge 10 ]; then TORCH_CUDA=cu130; else TORCH_CUDA=cu126; fi
fi
echo "torch build: $TORCH_CUDA"
export UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/$TORCH_CUDA
export UV_INDEX_STRATEGY=unsafe-best-match

# A cell is at most 1740 x 1400 voxels at 8 nm, so TILE=2048 puts a whole cell through the network as one tile; the
# default suits a 16-32 GB card.
uv run examples/betacells_mito.py "$data" "$out" ${cells:+--cells "$cells"} --tile "${TILE:-1024}"
