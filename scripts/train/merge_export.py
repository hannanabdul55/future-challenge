"""Adapter -> merged bf16 -> GGUF bf16 -> Q4_K_M -> checked artifact.

Each stage runs in the venv that owns its dependencies; ``all`` calls them as subprocesses:

  merge     (venv-train)    base bf16 on CPU + LoRA adapter -> work/merged, base config restored
  convert   (venv-convert)  convert_hf_to_gguf.py at llama.cpp 4df29be -> work/NAME-bf16.gguf
  quantize  (venv-eval, -I) llama_model_quantize (libllama of llama-cpp-python), Q4_K_M only
  check     (venv-convert)  fc.train.gguf_check against the base chat_template.jinja
  all       --adapter DIR --name NAME   (or --base-only)  -> GGUF_DIR/NAME/NAME-Q4_K_M.gguf

The merge never touches the NF4 weights: the base is reloaded in bf16 on CPU (RESEARCH
anti-pattern). The converter runs only from the commit-pinned tree (T-04.1-16).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fc.train import paths

BASE_FILES = ("config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
              "vocab.json", "merges.txt", "special_tokens_map.json", "chat_template.jinja")
SELF = Path(__file__).resolve()


def find_snapshot() -> Path:
    """Locate the pinned snapshot without importing huggingface_hub (venv-eval lacks it)."""
    snaps = paths.HF_HOME / "hub" / "models--allenai--Olmo-3-7B-Instruct" / "snapshots"
    snap = snaps / paths.BASE_REVISION
    if not (snap / "config.json").is_file():
        raise SystemExit(f"base snapshot not found at {snap}")
    return snap


def restore_base_files(merged: Path, snap: Path) -> list[str]:
    copied = []
    for f in BASE_FILES:
        if (snap / f).exists():
            shutil.copy(snap / f, merged / f)
            copied.append(f)
    return copied


# ---------------------------------------------------------------- stage bodies (run in-venv)

def stage_merge(adapter: Path, out: Path) -> None:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM

    snap = find_snapshot()
    model = AutoModelForCausalLM.from_pretrained(
        snap, dtype=torch.bfloat16, device_map={"": "cpu"}, low_cpu_mem_usage=True,
        trust_remote_code=False)
    merged = PeftModel.from_pretrained(model, str(adapter)).merge_and_unload()
    if out.exists():
        shutil.rmtree(out)
    merged.save_pretrained(out, safe_serialization=True)
    copied = restore_base_files(out, snap)
    print("merged ->", out, "restored:", ",".join(copied))


def stage_convert(merged: Path, outfile: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "NO_LOCAL_GGUF"}
    conv = paths.LLAMA_CPP_SRC / "convert_hf_to_gguf.py"
    subprocess.run([sys.executable, str(conv), str(merged), "--outtype", "bf16",
                    "--outfile", str(outfile)], check=True, env=env, cwd=str(paths.LLAMA_CPP_SRC))


def stage_quantize(src: Path, dst: Path) -> None:
    import ctypes

    import llama_cpp

    params = llama_cpp.llama_model_quantize_default_params()
    params.ftype = llama_cpp.LLAMA_FTYPE_MOSTLY_Q4_K_M
    rc = llama_cpp.llama_model_quantize(str(src).encode(), str(dst).encode(),
                                        ctypes.byref(params))
    if rc != 0:
        raise SystemExit(f"llama_model_quantize returned {rc}")
    print("quantized ->", dst)


# ---------------------------------------------------------------- driver

def run_stage(venv: Path, stage: str, *extra: str, isolated: bool = False) -> float:
    cmd = [str(venv / "bin" / "python")] + (["-I"] if isolated else []) + [str(SELF), stage,
                                                                          *extra]
    env = {**os.environ, **paths.env()}
    t0 = time.time()
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, env=env)
    return round(time.time() - t0, 1)


def cmd_all(args: argparse.Namespace) -> int:
    name = args.name
    work = paths.GGUF_DIR / name / "work"
    final_dir = paths.GGUF_DIR / name
    final_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    snap = find_snapshot()
    record: dict[str, object] = {"name": name, "base_only": bool(args.base_only)}
    if args.base_only:
        merged = work / "base-snapshot"
        if merged.exists():
            shutil.rmtree(merged)
        merged.mkdir()
        for f in snap.iterdir():  # safetensors + configs, symlinked: nothing is copied or edited
            (merged / f.name).symlink_to(f.resolve())
        record["merge_s"] = 0.0
    else:
        merged = work / "merged"
        record["merge_s"] = run_stage(paths.VENV_TRAIN, "merge", "--adapter", args.adapter,
                                      "--out", str(merged))
    bf16 = work / f"{name}-bf16.gguf"
    record["convert_s"] = run_stage(paths.VENV_CONVERT, "convert", "--merged", str(merged),
                                    "--outfile", str(bf16))
    q4 = final_dir / f"{name}-Q4_K_M.gguf"
    record["quantize_s"] = run_stage(paths.VENV_EVAL, "quantize", "--src", str(bf16),
                                     "--dst", str(q4), isolated=True)
    chk = final_dir / "check.json"
    run_stage(paths.VENV_CONVERT, "check", "--gguf", str(q4), "--template",
              str(snap / "chat_template.jinja"), "--json", str(chk))
    result = json.loads(chk.read_text())
    from fc.model_identity import sha256_file
    record.update(path=str(q4), size_bytes=q4.stat().st_size, sha256=sha256_file(q4),
                  ok=result["ok"], problems=result["problems"])
    chk.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: record[k] for k in ("name", "size_bytes", "sha256", "ok")}))
    if not result["ok"]:
        print("gguf_check problems:", result["problems"])
        return 1
    if args.cleanup:
        shutil.rmtree(work)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="stage", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--adapter", required=True)
    m.add_argument("--out", required=True)
    c = sub.add_parser("convert")
    c.add_argument("--merged", required=True)
    c.add_argument("--outfile", required=True)
    q = sub.add_parser("quantize")
    q.add_argument("--src", required=True)
    q.add_argument("--dst", required=True)
    k = sub.add_parser("check")
    k.add_argument("--gguf", required=True)
    k.add_argument("--template", required=True)
    k.add_argument("--json")
    a = sub.add_parser("all")
    a.add_argument("--adapter")
    a.add_argument("--name", required=True)
    a.add_argument("--base-only", action="store_true")
    a.add_argument("--cleanup", action="store_true")
    args = ap.parse_args(argv)
    if args.stage == "merge":
        stage_merge(Path(args.adapter), Path(args.out))
    elif args.stage == "convert":
        stage_convert(Path(args.merged), Path(args.outfile))
    elif args.stage == "quantize":
        stage_quantize(Path(args.src), Path(args.dst))
    elif args.stage == "check":
        from fc.train import gguf_check
        extra = ["--json", args.json] if args.json else []
        return gguf_check.main([args.gguf, "--template", args.template, *extra])
    else:
        if not args.base_only and not args.adapter:
            ap.error("all needs --adapter DIR or --base-only")
        return cmd_all(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
