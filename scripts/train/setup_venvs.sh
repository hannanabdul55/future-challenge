#!/usr/bin/env bash
# Install the phase 04.1 venvs from the approved hashed locks, fetch the base snapshot,
# and run the smoke checks. Idempotent: safe to re-run.
#
#   bash scripts/train/setup_venvs.sh            # train, convert, eval + base snapshot
#   bash scripts/train/setup_venvs.sh --unsloth  # also the fallback Unsloth venv
#
# Installs ONLY from scripts/train/locks/*.lock with --require-hashes (T-04.1-SC). Pins are
# never loosened here: a lock that fails to install stops the script.
# Every cache and temp dir comes from scripts/train/env.sh and lives under /mnt/d (D-10).
set -euo pipefail

REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
cd "$REPO"

WITH_UNSLOTH=0
for arg in "$@"; do
  case "$arg" in
    --unsloth) WITH_UNSLOTH=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# shellcheck source=scripts/train/env.sh
. scripts/train/env.sh

LOCKS="scripts/train/locks"
SOURCES="$LOCKS/sources.json"
RMG_SITE="/home/hannanabdul/rent-my-gpu/.venv/lib/python3.12/site-packages"
CPU_INDEX="https://download.pytorch.org/whl/cpu"

mkdir -p "$FC_TRAIN_ROOT" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$HF_HOME" "$TMPDIR" \
  "$PIP_CACHE_DIR" "$XDG_CACHE_HOME" "$TRITON_CACHE_DIR" "$TORCH_HOME" \
  "$TORCHINDUCTOR_CACHE_DIR" "$FC_TRAIN_ROOT/logs" "$FC_TRAIN_ROOT/parity"
echo "== root disk before =="
df -h / | tee "$TMPDIR/.df_before"

echo "== check locks =="
uv run python scripts/train/check_locks.py "$LOCKS"

echo "== python 3.12 =="
uv python install 3.12

make_venv() {  # make_venv <path>
  if [ ! -x "$1/bin/python" ]; then
    uv venv --python 3.12 "$1"
  fi
}

sync_lock() {  # sync_lock <venv> <lock> [extra uv flags...]
  local venv="$1" lock="$2"
  shift 2
  echo "== sync $lock -> $venv =="
  uv pip sync --require-hashes --no-build --python "$venv/bin/python" "$@" "$LOCKS/$lock"
}

write_pth() {  # write_pth <venv>: make `import fc` work without a build backend
  local site
  site="$("$1/bin/python" -I -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
  printf '%s\n' "$REPO" > "$site/fc_repo.pth"
}

make_venv "$FC_VENV_TRAIN"
sync_lock "$FC_VENV_TRAIN" train.lock
write_pth "$FC_VENV_TRAIN"

make_venv "$FC_VENV_CONVERT"
# convert.lock records torch 2.11.0+cpu, which only the PyTorch CPU index serves
# (sources.json convert_lock_notes). unsafe-best-match matches how the lock was compiled.
sync_lock "$FC_VENV_CONVERT" convert.lock --extra-index-url "$CPU_INDEX" \
  --index-strategy unsafe-best-match
write_pth "$FC_VENV_CONVERT"

make_venv "$FC_VENV_EVAL"
sync_lock "$FC_VENV_EVAL" eval.lock
write_pth "$FC_VENV_EVAL"

if [ "$WITH_UNSLOTH" = 1 ]; then
  if ! grep -q 'unsloth' .planning/phases/04.1-trained-olmo-3-entry/04.1-01-SUMMARY.md; then
    echo "unsloth lock not approved in 04.1-01-SUMMARY.md" >&2
    exit 1
  fi
  make_venv "$FC_VENV_UNSLOTH"
  sync_lock "$FC_VENV_UNSLOTH" unsloth.lock
  write_pth "$FC_VENV_UNSLOTH"
fi

echo "== llama_cpp copy into venv-eval =="
EVAL_SITE="$("$FC_VENV_EVAL/bin/python" -I -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
# `uv pip sync` treats the copied llama_cpp_python dist-info as an unlocked package and removes
# the files its RECORD lists on every run, so the copy is always redone from scratch here.
case "$EVAL_SITE" in
  "$FC_VENV_EVAL"/lib/python3.12/site-packages) ;;
  *) echo "unexpected venv-eval site-packages: $EVAL_SITE" >&2; exit 1 ;;
esac
# The copied libggml-cuda.so needs libcudart.so.12 and libcublas*.so.12. The source venv gets
# them from the NVIDIA cu12 wheels plus a tiny preload shim (zzz_cuda_preload.pth ->
# _cuda_preload.py); eval.lock has no CUDA 12 package, so copy the same files (cp -a, source
# untouched, sha256 recorded below) instead of installing anything outside the approved locks.
for item in llama_cpp llama_cpp_python.libs llama_cpp_python-0.3.35.dist-info \
    nvidia/cuda_runtime nvidia/cublas _cuda_preload.py zzz_cuda_preload.pth; do
  rm -rf -- "${EVAL_SITE:?}/$item"
  mkdir -p "$EVAL_SITE/$(dirname "$item")"
  cp -a "$RMG_SITE/$item" "$EVAL_SITE/$item"
done
python3 -I - "$RMG_SITE" "$EVAL_SITE" "$SOURCES" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

src, dst, sources = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


files = []
for sub in ("llama_cpp", "llama_cpp_python.libs", "nvidia/cuda_runtime", "nvidia/cublas"):
    root = src / sub
    if not root.is_dir():
        continue
    for so in sorted(p for p in root.rglob("*") if p.is_file() and ".so" in p.name):
        rel = so.relative_to(src)
        copy = dst / rel
        a, b = sha(so), sha(copy)
        if a != b:
            raise SystemExit(f"copy mismatch for {rel}: source {a} copy {b}")
        files.append({"path": str(rel), "sha256": a})
for extra in ("_cuda_preload.py", "zzz_cuda_preload.pth"):
    if sha(src / extra) != sha(dst / extra):
        raise SystemExit(f"copy mismatch for {extra}")
    files.append({"path": extra, "sha256": sha(src / extra)})
if not files:
    raise SystemExit("no .so files found to record")
data = json.loads(sources.read_text())
data["llama_cpp_copy"] = {"source": str(src), "version": "0.3.35", "so_files": files}
sources.write_text(json.dumps(data, indent=2) + "\n")
print(f"recorded {len(files)} sha256 entries (.so plus CUDA-12 preload shim) (copy verified equal to source)")
PY

echo "== base snapshot (D-01) =="
"$FC_VENV_TRAIN/bin/python" -I - "$SOURCES" <<'PY'
import json
import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

from fc.model_identity import UNSAFE_WEIGHT_SUFFIXES

repo, rev = os.environ["FC_BASE_REPO"], os.environ["FC_BASE_REVISION"]
snap = Path(snapshot_download(
    repo_id=repo, revision=rev,
    allow_patterns=["*.safetensors", "*.json", "*.jinja", "*.txt"]))
assert snap.name == rev, f"resolved commit {snap.name} != {rev}"
files = sorted(p for p in snap.rglob("*") if p.is_file() or p.is_symlink())
bad = [p.name for p in files if p.name.lower().endswith(UNSAFE_WEIGHT_SUFFIXES)]
assert not bad, f"pickled weight files in snapshot: {bad}"
entries = [{"name": str(p.relative_to(snap)), "bytes": p.resolve().stat().st_size} for p in files]
assert any(e["name"].endswith(".safetensors") for e in entries), "no safetensors in snapshot"
sources = Path(sys.argv[1])
data = json.loads(sources.read_text())
data["base_snapshot"] = {"repo": repo, "revision": rev, "path": str(snap), "files": entries}
sources.write_text(json.dumps(data, indent=2) + "\n")
total = sum(e["bytes"] for e in entries)
print(f"base snapshot ok: {len(entries)} files, {total / 1e9:.2f} GB at {snap}")
PY

echo "== smoke checks =="
"$FC_VENV_TRAIN/bin/python" -I -c "
import torch
ok = torch.cuda.is_available()
name = torch.cuda.get_device_name(0) if ok else ''
assert ok and '4070' in name, (ok, name)
print('smoke venv-train cuda:', name, 'torch', torch.__version__)"
"$FC_VENV_TRAIN/bin/python" -I -c "
from transformers import Olmo3ForCausalLM
from liger_kernel.transformers import apply_liger_kernel_to_olmo3
print('smoke venv-train Olmo3ForCausalLM + liger olmo3: ok')"
"$FC_VENV_TRAIN/bin/python" -I -c "
import peft, bitsandbytes, trl, accelerate
print('smoke venv-train peft', peft.__version__, 'bnb', bitsandbytes.__version__,
      'trl', trl.__version__, 'accelerate', accelerate.__version__)"
"$FC_VENV_TRAIN/bin/python" -I -c "
import fc.questions, fc.forecast_io, fc.train.sft_data
print('smoke venv-train repo imports: ok')"
"$FC_VENV_TRAIN/bin/python" -I -c "
import time; t = time.time(); import torch
print('smoke venv-train cold import torch: %.1f s' % (time.time() - t))"
"$FC_VENV_CONVERT/bin/python" -I -c "
import torch, transformers, numpy
print('smoke venv-convert torch', torch.__version__, 'transformers', transformers.__version__,
      'numpy', numpy.__version__)"
"$FC_VENV_CONVERT/bin/python" "$FC_LLAMA_CPP_SRC/convert_hf_to_gguf.py" --help > /dev/null
echo "smoke venv-convert convert_hf_to_gguf.py --help: exit 0"
"$FC_VENV_EVAL/bin/python" -I -c "
import llama_cpp, fc.forecast_io, pandas
assert llama_cpp.__version__ == '0.3.35', llama_cpp.__version__
assert llama_cpp.llama_supports_gpu_offload() is True
print('smoke venv-eval llama_cpp', llama_cpp.__version__, 'gpu offload: True, pandas', pandas.__version__)"

echo "== root disk after =="
df -h /
echo "(before:)"
cat "$TMPDIR/.df_before"
echo "setup_venvs: done"
