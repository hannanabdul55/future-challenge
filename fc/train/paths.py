"""Filesystem layout for phase 04.1 (D-10): every large artifact lives under /mnt/d/fc-train.

The root disk has about 5 GB free, so caches, venvs, datasets and checkpoints all sit on the
D: drive. Nothing here touches the disk at import time.
"""

from __future__ import annotations

from pathlib import Path

FC_TRAIN_ROOT = Path("/mnt/d/fc-train")
HF_HOME = Path("/mnt/d/hf-cache")

VENV_TRAIN = FC_TRAIN_ROOT / "venv-train"
VENV_CONVERT = FC_TRAIN_ROOT / "venv-convert"
VENV_EVAL = FC_TRAIN_ROOT / "venv-eval"
VENV_UNSLOTH = FC_TRAIN_ROOT / "venv-unsloth"

DATA = FC_TRAIN_ROOT / "data"
WIKI_CACHE = DATA / "wiki"
PACKETS = DATA / "packets"
TARGETS = DATA / "targets"
SFT = DATA / "sft"
MEDSL = DATA / "medsl"
RUNS = FC_TRAIN_ROOT / "runs"
GGUF_DIR = FC_TRAIN_ROOT / "gguf"
TMP = FC_TRAIN_ROOT / "tmp"
SRC = FC_TRAIN_ROOT / "src"
LLAMA_CPP_SRC = SRC / "llama.cpp-4df29be"
TRACER = FC_TRAIN_ROOT / "tracer"
LIVEPACKET = FC_TRAIN_ROOT / "livepacket"
EVAL_OUT = FC_TRAIN_ROOT / "eval"

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_DATA = REPO_ROOT / "data" / "phase-04.1"

BASE_REPO = "allenai/Olmo-3-7B-Instruct"
BASE_REVISION = "6e5971d9eba42665f5bd5a0fcf047f299ce1dccc"  # D-01
LLAMA_CPP_COMMIT = "4df29be4f4c3673f428170fda944a5b19f743bb8"


def env() -> dict[str, str]:
    """Environment variables that keep every cache and temp file off the root disk."""
    root = FC_TRAIN_ROOT
    return {
        "UV_CACHE_DIR": str(root / "uv-cache"),
        "UV_LINK_MODE": "copy",
        "UV_PYTHON_INSTALL_DIR": str(root / "uv-python"),
        "HF_HOME": str(HF_HOME),
        "TMPDIR": str(root / "tmp"),
        "PIP_CACHE_DIR": str(root / "pip-cache"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "TRITON_CACHE_DIR": str(root / "triton-cache"),
        "TORCH_HOME": str(root / "torch-home"),
        "TORCHINDUCTOR_CACHE_DIR": str(root / "inductor-cache"),
        "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
    }


def under_root(path: str | Path) -> bool:
    """True when ``path`` resolves inside FC_TRAIN_ROOT (or is the root itself)."""
    p = Path(path).resolve()
    root = FC_TRAIN_ROOT.resolve()
    return p == root or root in p.parents
