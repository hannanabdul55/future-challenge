"""Record or compare chat-template renders and token ids (training vs runtime).

  python -I scripts/train/template_parity.py --side hf --snapshot DIR --prompt-file F --out J
      (venv-train python)
  python -I scripts/train/template_parity.py --side llama --gguf FILE --prompt-file F --out J
      (venv-eval python)
  python scripts/train/template_parity.py --compare HF.json LLAMA.json
      (any python with fc importable; exit 1 on any problem)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fc.train.template_parity import compare, hf_side, llama_side, sample_prompts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--side", choices=["hf", "llama"])
    ap.add_argument("--snapshot", help="HF snapshot directory (--side hf)")
    ap.add_argument("--gguf", help="GGUF file (--side llama)")
    ap.add_argument("--prompt-file", help="tracer prompt text file")
    ap.add_argument("--out", help="JSON file to write")
    ap.add_argument("--compare", nargs=2, metavar=("HF_JSON", "LLAMA_JSON"))
    args = ap.parse_args(argv)

    if args.compare:
        hf, ll = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.compare)
        problems = compare(hf, ll)
        if problems:
            print("\n".join(problems))
            return 1
        tokens = sum(len(r["ids"]) for r in hf)
        print(f"parity ok: {len(hf)} prompts, {tokens} tokens")
        print("token counts per prompt: " + ", ".join(str(len(r["ids"])) for r in hf))
        return 0

    if not (args.side and args.prompt_file and args.out):
        ap.error("--side, --prompt-file and --out are required")
    prompts = sample_prompts(args.prompt_file)
    if args.side == "hf":
        if not args.snapshot:
            ap.error("--snapshot is required for --side hf")
        records = hf_side(args.snapshot, prompts)
    else:
        if not args.gguf:
            ap.error("--gguf is required for --side llama")
        records = llama_side(args.gguf, prompts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(records)} records to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
