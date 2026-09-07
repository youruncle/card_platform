#!/bin/zsh
set -euo pipefail

project_root=${0:A:h:h}
runtime_dir="$project_root/.runtime/flatbed-ml"
checkpoint_dir="$project_root/.runtime/models"
checkpoint="$checkpoint_dir/sam2.1_hiera_tiny.pt"

"$project_root/.venv/bin/python" -m venv "$runtime_dir"
"$runtime_dir/bin/pip" install --upgrade pip
"$runtime_dir/bin/pip" install 'torch==2.13.0' 'torchvision==0.28.0'
"$runtime_dir/bin/pip" install 'opencv-python-headless==5.0.0.93'
SAM2_BUILD_CUDA=0 "$runtime_dir/bin/pip" install \
  'git+https://github.com/facebookresearch/sam2.git@2b90b9f5ceec907a1c18123530e92e794ad901a4'
mkdir -p "$checkpoint_dir"
if [[ ! -f "$checkpoint" ]]; then
  curl --fail --location --retry 3 \
    --output "$checkpoint" \
    'https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt'
fi
"$runtime_dir/bin/python" -c \
  'import torch; from sam2.build_sam import build_sam2; print({"mps": torch.backends.mps.is_available(), "sam2": "ready"})'
