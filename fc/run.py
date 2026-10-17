"""Host-side Tier S runner for one entry on one daily round (RUN-02, RUN-03).

    fc run --entry model-metadata/<id>.yml --season 2026-us-midterms --date 2026-10-26 \\
           [--snapshot DIR] [--runner-key keys/runner/private.pem] [--dry-run] [--local]

Steps:
  1. load the entry's identity record; refuse unless tier S and `weights_file` is set
  2. locate (or download, `--fetch`) the HF snapshot; re-hash the weights file against the
     committed sha256 — a mismatch is a hard stop (DESIGN.md §7.1a)
  3. derive the day's seed from the round manifest's drand round (`fc.entropy`)
  4. run the reference image with --network none, --gpus all, read-only mounts, a wall-clock
     quota; or `--local` to run forecast.py in-process for development
  5. parse `/out/forecast.csv` through the validator, write the signed result bundle to
     model-output/<id>/<round_id>.csv and runs/<season>/<date>/<id>/ (transcript, status,
     bundle.json); a failure yields a signed "missing" bundle rather than nothing, and a
     run that parses zero rows yields a signed "empty" bundle (nothing is published)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from . import commit as C
from . import entropy as E
from . import forecast_io as F
from . import model_identity as M
from . import questions as Q
from .validate import validate_frame

RUN_DOMAIN_SEP = b"future-challenge/run/v1"
DEFAULT_IMAGE = "fc-forecaster:2026.10"


@dataclass
class RunPaths:
    root: Path
    season: str
    day: str
    model_id: str

    @property
    def round_dir(self) -> Path:
        return self.root / "rounds" / self.season / self.day

    @property
    def packet_dir(self) -> Path:
        return self.root / "target-data" / "packets" / self.season / self.day

    @property
    def questions(self) -> Path:
        return self.root / "questions" / self.season / "questions.json"

    @property
    def run_dir(self) -> Path:
        return self.root / "runs" / self.season / self.day / self.model_id

    @property
    def output_csv(self) -> Path:
        return self.root / "model-output" / self.model_id / f"{self.season}-{self.day}.csv"


def day_seed(manifest: dict[str, Any], *, fetch: bool = True) -> tuple[int, dict[str, Any]]:
    """Seed = first 8 bytes of SHA-256(β_R) for the manifest's drand round. Falls back to the
    round number itself when the beacon is unreachable (recorded in the bundle)."""
    r = int(manifest["drand"]["round"])
    if fetch:
        try:
            d = E.fetch_drand(r)
            beta = bytes.fromhex(d.randomness)
            return int.from_bytes(hashlib.sha256(beta).digest()[:8], "big"), {
                "drand_round": r, "randomness": d.randomness, "source": "beacon"}
        except Exception as exc:  # noqa: BLE001 — recorded; a run must not block on drand
            return r, {"drand_round": r, "source": f"fallback:{type(exc).__name__}"}
    return r, {"drand_round": r, "source": "fallback:no-fetch"}


def docker_cmd(image: str, *, snapshot: Path, paths: RunPaths, prompt: Path, out: Path,
               env: dict[str, str], gpus: bool = True, cpus: float = 8, memory: str = "24g") -> list[str]:
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only",
           "--tmpfs", "/tmp:size=2g", "--security-opt", "no-new-privileges",
           "--cap-drop", "ALL", "--pids-limit", "256", f"--cpus={cpus}", f"--memory={memory}",
           "-v", f"{snapshot}:/model:ro", "-v", f"{paths.packet_dir}:/packet:ro",
           "-v", f"{paths.questions.parent}:/questions:ro", "-v", f"{prompt.parent}:/prompt:ro",
           "-v", f"{out}:/out"]
    if gpus:
        cmd += ["--gpus", "all"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd.append(image)
    return cmd


def run_entry(entry_yaml: Path, *, root: Path, season: str, day: str, snapshot: Path | None,
              image: str = DEFAULT_IMAGE, runner_key: Path | None = None,
              timeout_s: int = 3600, dry_run: bool = False, local: bool = False,
              fetch: bool = False, gpus: bool = True, fetch_drand: bool = True) -> dict[str, Any]:
    doc = yaml.safe_load(entry_yaml.read_text())
    model_id = doc["model_id"]
    if doc.get("tier") != "S":
        raise ValueError(f"{model_id} is tier {doc.get('tier')}; only tier S is hub-run")
    rec = M.load_identity_record(entry_yaml)
    wf = rec["inference"].get("weights_file")
    if not wf:
        raise ValueError(f"{model_id}: identity has no inference.weights_file")
    paths = RunPaths(root, season, day, model_id)
    manifest = json.loads((paths.round_dir / "manifest.json").read_text())
    round_id = manifest["round_id"]
    qs = Q.load_questions(paths.questions)
    if Q.questions_hash(qs) != manifest["questions_hash"]:
        raise ValueError("question set hash does not match the round manifest")

    bundle: dict[str, Any] = {
        "schema": "fc-run/2026.10", "round_id": round_id, "model_id": model_id,
        "identity_hash": rec["identity_hash"], "weights_file": wf,
        "packet_md_sha256": manifest["packet"]["packet_md_sha256"], "image": image,
        "started": C.iso_z(datetime.now(UTC)), "status": "missing", "reason": None,
    }
    paths.run_dir.mkdir(parents=True, exist_ok=True)

    # ---- weights: locate, verify
    if snapshot is None:
        if not fetch:
            raise ValueError("no --snapshot given and --fetch not set")
        from huggingface_hub import snapshot_download
        snapshot = Path(snapshot_download(rec["repo_id"], revision=rec["revision"],
                                          allow_patterns=[wf, "*.json"]))
    problems = M.verify_snapshot(rec, snapshot, only=[wf])
    if problems:
        bundle.update(reason=f"weights verification failed: {problems}")
        return _finish(bundle, paths, runner_key)
    bundle["weights_sha256"] = next(f["sha256"] for f in rec["files"] if f["path"] == wf)

    # ---- seed
    seed, seed_info = day_seed(manifest, fetch=fetch_drand)
    bundle["seed"] = seed
    bundle["seed_source"] = seed_info

    prompt = F.TEMPLATE_PATH
    if M.prompt_template_hash(prompt.read_bytes()) != rec["prompt_template_sha256"]:
        bundle.update(reason="prompt template hash does not match the frozen identity")
        return _finish(bundle, paths, runner_key)

    out = paths.run_dir / "out"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    inf = rec["inference"]
    env = {"FC_ROUND_ID": round_id, "FC_DAY": day, "FC_SEED": str(seed), "FC_WEIGHTS_FILE": wf,
           "FC_CTX": str(inf.get("context_length", 16384)),
           "FC_MAX_TOKENS": str(inf.get("decoding", {}).get("max_tokens", 4096)),
           "FC_THINKING": "1" if inf.get("thinking", False) else "0"}
    bundle["thinking"] = bool(inf.get("thinking", False))
    cmd = docker_cmd(image, snapshot=snapshot, paths=paths, prompt=prompt, out=out, env=env,
                     gpus=gpus)
    bundle["command"] = cmd
    if dry_run:
        bundle.update(status="dry-run", reason="not executed")
        return _finish(bundle, paths, runner_key)

    t0 = time.time()
    try:
        if local:
            rc = _run_local(snapshot, paths, prompt, out, env, timeout_s)
        else:
            rc = subprocess.run(cmd, timeout=timeout_s, capture_output=True, text=True,
                                check=False)
            (paths.run_dir / "docker.log").write_text(rc.stdout + rc.stderr)
            rc = rc.returncode
    except subprocess.TimeoutExpired:
        bundle.update(reason=f"timeout after {timeout_s}s")
        return _finish(bundle, paths, runner_key)
    bundle["seconds"] = round(time.time() - t0, 1)
    if rc != 0:
        bundle.update(reason=f"container exit code {rc}")
        return _finish(bundle, paths, runner_key)

    # ---- parse + validate + publish
    fcsv = out / "forecast.csv"
    if not fcsv.is_file():
        bundle.update(reason="no forecast.csv produced")
        return _finish(bundle, paths, runner_key)
    import pandas as pd
    df = pd.read_csv(fcsv)
    status = json.loads((out / "status.json").read_text()) if (out / "status.json").exists() else {}
    if len(df) == 0:
        # Nothing parsed: record it (signed) but publish no forecast for this entry/day.
        bundle.update(status="empty", parse_rate=status.get("parse_rate"),
                      n_missing=status.get("n_missing"), n_rows=0,
                      reason=f"model produced no parseable answers "
                             f"(parse_rate {status.get('parse_rate')})")
        _copy_evidence(bundle, out, paths)
        return _finish(bundle, paths, runner_key)
    rc_cfg = Q.to_tasks_round(qs, round_id=round_id, T_open=manifest["T_open"],
                              T_commit=manifest["T_commit"], T_reveal=manifest["T_reveal"])
    rep = validate_frame(df, rc_cfg)
    bundle["validation"] = {"errors": rep.errors, "warnings": rep.warnings}
    if not rep.ok:
        bundle.update(reason=f"invalid output: {rep.errors[:3]}")
        return _finish(bundle, paths, runner_key)
    bundle.update(status="ok", parse_rate=status.get("parse_rate"),
                  n_missing=status.get("n_missing"), n_rows=len(df))
    paths.output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(paths.output_csv, index=False)
    bundle["forecast_sha256"] = hashlib.sha256(paths.output_csv.read_bytes()).hexdigest()
    _copy_evidence(bundle, out, paths)
    return _finish(bundle, paths, runner_key)


def _copy_evidence(bundle: dict[str, Any], out: Path, paths: RunPaths) -> None:
    for name in ("transcript.jsonl", "status.json"):
        if (out / name).exists():
            shutil.copy(out / name, paths.run_dir / name)
            bundle[f"{name}_sha256"] = hashlib.sha256((out / name).read_bytes()).hexdigest()


def _run_local(snapshot: Path, paths: RunPaths, prompt: Path, out: Path, env: dict[str, str],
               timeout_s: int) -> int:
    """Development path: run runner/forecast/forecast.py in a subprocess with the container's
    fixed paths redirected through a temp tree of symlinks."""
    import tempfile
    tree = Path(tempfile.mkdtemp(prefix="fc-local-"))
    for name, target in (("model", snapshot), ("packet", paths.packet_dir),
                         ("questions", paths.questions.parent), ("prompt", prompt.parent)):
        (tree / name).symlink_to(target.resolve())
    (tree / "out").symlink_to(out.resolve())
    script = Path(__file__).resolve().parents[1] / "runner" / "forecast" / "forecast.py"
    code = (script.read_text()
            .replace('Path("/model")', f'Path("{tree}/model")')
            .replace('Path("/packet/packet.md")', f'Path("{tree}/packet/packet.md")')
            .replace('Path("/questions/questions.json")', f'Path("{tree}/questions/questions.json")')
            .replace('Path("/prompt/template.md")', f'Path("{tree}/prompt/{prompt.name}")')
            .replace('Path("/out")', f'Path("{tree}/out")'))
    local_script = tree / "forecast_local.py"
    local_script.write_text(code)
    full_env = {**os.environ, **env}
    rc = subprocess.run([sys.executable, str(local_script)], env=full_env, timeout=timeout_s,
                        capture_output=True, text=True, check=False)
    (paths.run_dir / "local.log").write_text(rc.stdout + rc.stderr)
    return rc.returncode


def _finish(bundle: dict[str, Any], paths: RunPaths, runner_key: Path | None) -> dict[str, Any]:
    bundle["finished"] = C.iso_z(datetime.now(UTC))
    payload = C.canonical_json({k: v for k, v in bundle.items() if k != "runner_signature"})
    if runner_key is not None:
        sk = C.load_private(runner_key.read_bytes())
        bundle["runner_signature"] = C.sign(sk, RUN_DOMAIN_SEP + payload)
    else:
        bundle["runner_signature"] = None
    (paths.run_dir / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n")
    return bundle
