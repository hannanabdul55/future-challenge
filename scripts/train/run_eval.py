"""Answer a historical day through the runner path, and assemble run records.

  run_eval.py run-day --gguf G --packet-dir D --questions Q --out O --day YYYY-MM-DD --round-id R
  run_eval.py record  --name N [--run RUN_DIR] --gguf G --eval EVAL_DIR [--probe-json F] --out JSON

Smoke runs report timings, memory and parse rates only; nothing here is scored against truth.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fc.model_identity import sha256_file
from fc.train import evalrun


def _modes(status: dict[str, Any]) -> list[str]:
    return [b.get("mode") for b in status.get("batches", [])]


def cmd_run_day(args: argparse.Namespace) -> int:
    status = evalrun.run_day(args.gguf, args.packet_dir, args.questions, args.out,
                             day=args.day, round_id=args.round_id)
    modes = _modes(status)
    print(f"parse_rate={status.get('parse_rate')} n_missing={status.get('n_missing')} "
          f"wall_s={status.get('wall_s')} returncode={status.get('returncode')}")
    for b in status.get("batches", []):
        print(f"  batch {b['batch']} {b['family']}: mode={b['mode']} "
              f"think_prefill={b['think_prefill']} parse_rate={b['parse_rate']} "
              f"seconds={b['seconds']} error={b['error']}")
    if status.get("returncode") != 0 or not modes:
        print("runner failed; see", Path(args.out) / "local.log")
        return 1
    try:
        evalrun.assert_chat_mode(status)
    except AssertionError as exc:
        print("FAIL:", exc)
        return 1
    return 0


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text()) if path.exists() else {}


def build_record(args: argparse.Namespace) -> dict[str, Any]:
    gguf = Path(args.gguf)
    status = _load(Path(args.eval) / "status.json")
    status.update(_load(Path(args.eval) / "run_day.json"))
    rec: dict[str, Any] = {"path": str(gguf), "sha256": sha256_file(gguf),
                           "size_bytes": gguf.stat().st_size}
    check = _load(gguf.parent / "check.json")
    rec["gguf_check"] = "ok" if check.get("ok") else check.get("problems", ["no check.json"])
    for k in ("merge_s", "convert_s", "quantize_s"):
        if k in check:
            rec[k] = check[k]
    if args.run:
        run = _load(Path(args.run) / "status.json")
        rec["run"] = run
    if args.probe_json:
        probe = _load(Path(args.probe_json))
        rec["probe"] = {k: probe.get(k) for k in
                        ("offloaded", "needle_ok", "prompt_eval_tps", "gen_tps", "load_s",
                         "needle_tokens", "needle_seconds", "arch")}
    modes = _modes(status)
    rec["runner_tracer_day"] = {
        "parse_rate": status.get("parse_rate"), "modes": modes,
        "seconds": [b.get("seconds") for b in status.get("batches", [])],
        "wall_s": status.get("wall_s"), "n_missing": status.get("n_missing"),
        "think_prefill": [b.get("think_prefill") for b in status.get("batches", [])],
    }
    return rec


def flatten_tracer(rec: dict[str, Any], example_len: int | None) -> dict[str, Any]:
    run = rec.get("run", {})
    day = rec["runner_tracer_day"]
    return {
        "steps": run.get("steps"), "losses": run.get("losses"),
        "peak_vram_mib": run.get("peak_vram_mib"),
        "tokens_per_s": run.get("tokens_per_s"),
        "sequence_length": example_len if example_len is not None else run.get("max_length"),
        "liger_path": run.get("liger_path"), "trainer_class": run.get("trainer_class"),
        "train_seconds": run.get("seconds"),
        "merge_s": rec.get("merge_s"), "convert_s": rec.get("convert_s"),
        "quantize_s": rec.get("quantize_s"),
        "gguf_size_bytes": rec["size_bytes"], "gguf_sha256": rec["sha256"],
        "gguf_check": rec["gguf_check"],
        "parse_rate": day["parse_rate"], "batch_modes": day["modes"],
        "batch_seconds": day["seconds"], "think_prefill": day["think_prefill"],
        "eval_wall_s": day["wall_s"],
        "run_status": run.get("status"),
    }


def cmd_record(args: argparse.Namespace) -> int:
    rec = build_record(args)
    out = Path(args.out)
    if out.name == "tracer.json":
        n = None
        run = rec.get("run", {})
        n = run.get("max_length_seen")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(flatten_tracer(rec, n), indent=2) + "\n")
    else:
        data = _load(out)
        data[args.name] = rec
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"recorded {args.name} -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run-day")
    r.add_argument("--gguf", required=True)
    r.add_argument("--packet-dir", required=True)
    r.add_argument("--questions", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--day", required=True)
    r.add_argument("--round-id", required=True)
    r.set_defaults(fn=cmd_run_day)
    c = sub.add_parser("record")
    c.add_argument("--name", required=True)
    c.add_argument("--run")
    c.add_argument("--gguf", required=True)
    c.add_argument("--eval", required=True)
    c.add_argument("--probe-json")
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_record)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
