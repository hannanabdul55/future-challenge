"""NF4 QLoRA trainer over pre-tokenised SFT examples (venv-train).

Run as ``$FC_VENV_TRAIN/bin/python scripts/train/train_qlora.py --train X.jsonl --out RUN_DIR``.
Examples are tokenised once with ``fc.train.sft_data.tokenize_example`` (prompt labels -100,
House spans -100, eos labelled), so TRL's dataset preparation is skipped and nothing is ever
truncated: the run asserts ``len(input_ids) <= max_length`` for every example. Writes
RUN_DIR/status.json (status ok, oom, nan or deadline) and RUN_DIR/adapter; exits 3 on CUDA OOM
or a non-finite loss after writing the status.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fc.train import paths
from fc.train.sft_data import tokenize_example

PAD_ID = 100277
EOS_ID = 100257
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--train", nargs="+", required=True, help="training JSONL file(s)")
    ap.add_argument("--eval", help="held-out JSONL (loss each epoch)")
    ap.add_argument("--snapshot", help="base snapshot dir (default: the pinned local snapshot)")
    ap.add_argument("--out", required=True, help="RUN_DIR")
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--lora-dropout", type=float, default=0.05)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-length", type=int, help="default: longest example + 64")
    ap.add_argument("--activation-offloading", action="store_true")
    ap.add_argument("--save-each-epoch", action="store_true")
    ap.add_argument("--deadline-utc", help="ISO time (e.g. 2026-10-19T03:00:00Z); stop after it")
    ap.add_argument("--liger", choices=["trainer", "pre", "off"], default="off",
                    help="trainer: SFTConfig(use_liger_kernel=True); pre: apply_liger_kernel_to_"
                         "olmo3 before loading; off: no liger")
    ap.add_argument("--trainer", choices=["sft", "hf"], default="sft")
    return ap.parse_args(argv)


def resolve_snapshot(arg: str | None) -> str:
    if arg:
        return arg
    from huggingface_hub import snapshot_download
    return snapshot_download(paths.BASE_REPO, revision=paths.BASE_REVISION,
                             local_files_only=True,
                             allow_patterns=["*.safetensors", "*.json", "*.jinja", "*.txt"])


def read_jsonl(files: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in files:
        rows += [json.loads(line) for line in Path(f).read_text().splitlines() if line.strip()]
    return rows


class VramSampler(threading.Thread):
    """Samples whole-card memory with nvidia-smi every 2 s (the number that decides fit)."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self.last_error = None
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                out = subprocess.run(
                    ["/usr/lib/wsl/lib/nvidia-smi", "--query-gpu=memory.used",
                     "--format=csv,noheader,nounits"], capture_output=True, text=True,
                    timeout=10, check=False).stdout.strip().splitlines()
                if out:
                    self.peak = max(self.peak, int(out[0]))
            except Exception as exc:  # noqa: BLE001 - sampling is best effort
                self.last_error = repr(exc)
            self._stop_evt.wait(2.0)

    def stop(self) -> None:
        self._stop_evt.set()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {"status": "starting", "args": vars(args), "steps": 0,
                              "losses": [], "eval_losses": []}

    def write_status() -> None:
        (out / "status.json").write_text(json.dumps(status, indent=2) + "\n")

    import torch
    import transformers
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        TrainerCallback,
    )

    snapshot = resolve_snapshot(args.snapshot)
    status["snapshot"] = snapshot
    tok = AutoTokenizer.from_pretrained(snapshot, trust_remote_code=False)
    if tok.pad_token_id != PAD_ID:
        raise SystemExit(f"pad id {tok.pad_token_id} != {PAD_ID}")

    def tokenise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [tokenize_example(r, tok, eos_id=EOS_ID) for r in rows]

    train_rows = tokenise(read_jsonl(args.train))
    eval_rows = tokenise(read_jsonl([args.eval])) if args.eval else []
    longest = max(len(r["input_ids"]) for r in train_rows + eval_rows)
    max_length = args.max_length or longest + 64
    for r in train_rows + eval_rows:  # Pitfall 1: zero truncation, labels present
        assert len(r["input_ids"]) <= max_length, (len(r["input_ids"]), max_length)
        assert r["n_label"] > 0
    status.update(max_length=max_length, max_length_seen=longest, n_examples=len(train_rows),
                  n_eval=len(eval_rows),
                  n_label=[r["n_label"] for r in train_rows],
                  n_prompt=[r["n_prompt"] for r in train_rows])
    print(f"examples={len(train_rows)} longest={longest} max_length={max_length}", flush=True)

    keep = ("input_ids", "labels", "attention_mask")
    from datasets import Dataset
    train_ds = Dataset.from_list([{k: r[k] for k in keep} for r in train_rows])
    eval_ds = Dataset.from_list([{k: r[k] for k in keep} for r in eval_rows]) if eval_rows else None

    class Collator:
        """Right-pads; never truncates. Counts tokens seen for the throughput figure."""

        def __init__(self) -> None:
            self.tokens = 0

        def __call__(self, feats: list[dict[str, Any]]) -> dict[str, Any]:
            n = max(len(f["input_ids"]) for f in feats)
            self.tokens += sum(len(f["input_ids"]) for f in feats)
            ids, att, lab = [], [], []
            for f in feats:
                pad = n - len(f["input_ids"])
                ids.append(list(f["input_ids"]) + [PAD_ID] * pad)
                att.append(list(f["attention_mask"]) + [0] * pad)
                lab.append(list(f["labels"]) + [-100] * pad)
            return {"input_ids": torch.tensor(ids), "attention_mask": torch.tensor(att),
                    "labels": torch.tensor(lab)}

    collator = Collator()
    probe = collator([train_ds[0]])
    kept = int((probe["labels"] != -100).sum())
    assert kept == train_rows[0]["n_label"], (kept, train_rows[0]["n_label"])
    collator.tokens = 0
    status["label_check"] = {"collator_labels": kept, "n_label": train_rows[0]["n_label"]}

    # Triton compiles a small C launcher on first use and this box has no C compiler, so every
    # Triton kernel (Liger's, and TRL's chunked-log-prob kernel) fails at first call. TRL ships a
    # pure-torch tile path for devices without Triton; switch to it for the fused LM head.
    import shutil
    no_cc = not any(shutil.which(c) for c in ("gcc", "clang", "tcc")) and not os.environ.get("CC")
    status["triton"] = "disabled (no C compiler)" if no_cc else "enabled"
    if no_cc:
        from trl.kernels import chunked_logprob
        chunked_logprob._TRITON_AVAILABLE = False  # kernels fold tiles in torch
        # trl.trainer.utils binds its own `_use_triton`, which also decides whether only the
        # labelled positions are scored. Keep that selection (about 600 of 9.4k positions);
        # it needs no Triton, and scoring every position in torch tiles costs GBs of transients.
        import trl.trainer.utils as trl_utils
        trl_utils._use_triton = lambda device: device.type in ("cuda", "xpu")
        if args.liger != "off":
            raise SystemExit("liger kernels are Triton kernels; with no C compiler use --liger off")

    if args.liger == "pre":
        from liger_kernel.transformers import apply_liger_kernel_to_olmo3
        apply_liger_kernel_to_olmo3(fused_linear_cross_entropy=False)
    status["liger_path"] = {"trainer": "SFTConfig.use_liger_kernel (layer kernels) + TRL fused "
                                       "LM head",
                            "pre": "apply_liger_kernel_to_olmo3 before load + TRL fused LM head",
                            "off": "none (TRL fused LM head, pure-torch tiles)"}[args.liger]

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_use_double_quant=True,
                             bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        snapshot, quantization_config=bnb, dtype=torch.bfloat16, attn_implementation="sdpa",
        trust_remote_code=False, device_map={"": 0})
    model.config.use_cache = False
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False})
    # prepare_model_for_kbit_training upcasts every bf16 parameter to fp32, including the two
    # frozen 100k x 4096 matrices (1.6 GB each in bf16). Recast them: they are not trained.
    for emb in (model.get_input_embeddings(), model.get_output_embeddings()):
        emb.to(torch.bfloat16)
    model = get_peft_model(model, LoraConfig(
        r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=args.lora_dropout,
        target_modules=TARGETS, bias="none", task_type="CAUSAL_LM"))
    n_train = sum(p.numel() for p in model.parameters() if p.requires_grad)
    status["trainable_params"] = n_train
    print(f"trainable params: {n_train:,}", flush=True)

    deadline = None
    if args.deadline_utc:
        deadline = datetime.fromisoformat(args.deadline_utc).timestamp()

    sampler = VramSampler()
    sampler.start()
    t_start = time.time()

    class Log(TrainerCallback):
        def __init__(self) -> None:
            self.t_prev = time.time()
            self.tok_prev = 0

        def on_log(self, a, state, control, logs=None, **kw):
            logs = logs or {}
            if "loss" in logs:
                loss = float(logs["loss"])
                status["losses"].append(loss)
                status["steps"] = int(state.global_step)
                now = time.time()
                # TRL's cumulative num_tokens counts real (unpadded) tokens seen by the model
                tok = float(logs.get("num_tokens", collator.tokens))
                tps = (tok - self.tok_prev) / max(now - self.t_prev, 1e-6)
                self.t_prev, self.tok_prev = now, tok
                status.setdefault("step_tokens_per_s", []).append(round(tps, 1))
                status["peak_vram_mib"] = max(sampler.peak, status.get("peak_vram_mib", 0))
                print(f"step {state.global_step} loss {loss:.4f} tok/s {tps:.0f} "
                      f"alloc_peak {torch.cuda.max_memory_allocated() / 2**20:.0f} MiB "
                      f"smi_peak {sampler.peak} MiB", flush=True)
                if not math.isfinite(loss):
                    status["status"] = "nan"
                    control.should_training_stop = True
            if "eval_loss" in logs:
                status["eval_losses"].append(float(logs["eval_loss"]))
            write_status()

        def on_step_end(self, a, state, control, **kw):
            if deadline and time.time() > deadline:
                status["status"] = "deadline"
                control.should_training_stop = True

        def on_epoch_end(self, a, state, control, model=None, **kw):
            if args.save_each_epoch and model is not None:
                model.save_pretrained(out / f"epoch-{round(state.epoch)}")

    if args.trainer == "sft":
        from trl import SFTConfig, SFTTrainer
        cfg = SFTConfig(
            output_dir=str(out / "trainer"), per_device_train_batch_size=1,
            per_device_eval_batch_size=1, gradient_accumulation_steps=args.grad_accum,
            learning_rate=args.lr, lr_scheduler_type="cosine", warmup_steps=0.05,
            optim="paged_adamw_8bit", bf16=True, gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            use_liger_kernel=(args.liger == "trainer"),
            activation_offloading=args.activation_offloading, max_length=max_length,
            packing=False, dataset_kwargs={"skip_prepare_dataset": True},
            remove_unused_columns=False, logging_steps=1, save_strategy="no",
            report_to="none", seed=args.seed, num_train_epochs=args.epochs,
            max_steps=args.max_steps, eval_strategy="epoch" if eval_ds is not None else "no")
        trainer = SFTTrainer(model=model, args=cfg, train_dataset=train_ds,
                             eval_dataset=eval_ds, data_collator=collator,
                             processing_class=tok, callbacks=[Log()])
    else:
        from transformers import Trainer, TrainingArguments
        cfg = TrainingArguments(
            output_dir=str(out / "trainer"), per_device_train_batch_size=1,
            per_device_eval_batch_size=1, gradient_accumulation_steps=args.grad_accum,
            learning_rate=args.lr, lr_scheduler_type="cosine", warmup_steps=0.05,
            optim="paged_adamw_8bit", bf16=True, gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
            remove_unused_columns=False, logging_steps=1, save_strategy="no",
            report_to="none", seed=args.seed, num_train_epochs=args.epochs,
            max_steps=args.max_steps, eval_strategy="epoch" if eval_ds is not None else "no")
        trainer = Trainer(model=model, args=cfg, train_dataset=train_ds, eval_dataset=eval_ds,
                          data_collator=collator, callbacks=[Log()])
    status["trainer_class"] = type(trainer).__name__
    status["transformers"] = transformers.__version__
    status["status"] = "training"
    write_status()

    rc = 0
    try:
        trainer.train()
    except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
        # WSL reports some device-memory exhaustion as a driver error ("Failed to create GPU
        # mapping") instead of torch's OutOfMemoryError; both are recorded as oom.
        if not isinstance(exc, torch.cuda.OutOfMemoryError) and "CUDA" not in str(exc):
            raise
        status["status"] = "oom"
        status["error"] = str(exc)[:500]
        rc = 3
    finally:
        sampler.stop()
    status["seconds"] = round(time.time() - t_start, 1)
    status["peak_vram_mib"] = max(sampler.peak, status.get("peak_vram_mib", 0))
    status["torch_max_allocated_mib"] = round(torch.cuda.max_memory_allocated() / 2**20)
    status["torch_max_reserved_mib"] = round(torch.cuda.max_memory_reserved() / 2**20)
    tps = status.get("step_tokens_per_s", [])
    # skip step 1 (kernel autotune / allocator warm-up) when more steps exist
    steady = tps[1:] if len(tps) > 1 else tps
    status["tokens_per_s"] = round(sum(steady) / len(steady), 1) if steady else None
    if status["status"] == "nan":
        rc = 3
    elif status["status"] == "training":
        status["status"] = "ok"
    if status["losses"] and not all(math.isfinite(x) for x in status["losses"]):
        status["status"] = "nan"
        rc = 3
    if status["status"] in ("ok", "deadline"):
        trainer.model.save_pretrained(out / "adapter")
        status["adapter"] = str(out / "adapter")
    write_status()
    print(f"status {status['status']} steps {status['steps']} peak_vram_mib "
          f"{status['peak_vram_mib']} seconds {status['seconds']}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
