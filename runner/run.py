"""Sealed runner (DESIGN.md §7.4–7.5, §11). Tier S execution of a committed container.

Contract with the container:
  - pulled by digest only (`image@sha256:...`), never by tag
  - /tasks/tasks.jsonl mounted read-only; container writes /out/answers.jsonl
  - no secrets in the environment; egress per policy (closed | allowlist | open-logged)
  - CPU/memory/time/token quotas enforced; a single run with a fixed seed (no retries)
  - stdout/stderr and proxy logs captured and published with the results

This scaffold builds the docker invocation and signs the result bundle. Sandboxing
(gVisor / Firecracker) and the logging proxy are deployment concerns; see README.md.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from fc import commit as C

RUNTIME = "runsc"  # gVisor; use firecracker-containerd for GPU jobs


def load_policy(name: str) -> dict:
    return yaml.safe_load((Path(__file__).parent / "policies" / f"{name}.yaml").read_text())


def docker_cmd(image_digest: str, tasks_dir: Path, out_dir: Path, policy: dict, quotas: dict) -> list[str]:
    if "@sha256:" not in image_digest:
        raise ValueError("image must be pinned by digest")
    cmd = ["docker", "run", "--rm", f"--runtime={RUNTIME}",
           "--read-only", "--tmpfs", "/tmp:size=1g", "--security-opt", "no-new-privileges",
           "--cap-drop", "ALL", "--pids-limit", "512",
           f"--cpus={quotas['cpus']}", f"--memory={quotas['memory']}",
           "-v", f"{tasks_dir}:/tasks:ro", "-v", f"{out_dir}:/out",
           "-e", f"FC_SEED={quotas['seed']}", "-e", f"FC_TOKEN_BUDGET={quotas['token_budget']}"]
    if policy["egress"] == "closed":
        cmd += ["--network", "none"]
    else:
        # proxy container on fc-egress network enforces allowlist and logs every request
        cmd += ["--network", "fc-egress",
                "-e", "HTTP_PROXY=http://fc-proxy:8080", "-e", "HTTPS_PROXY=http://fc-proxy:8080",
                "-e", f"FC_EGRESS_POLICY={policy['egress']}"]
    cmd.append(image_digest)
    return cmd


def run(image_digest: str, tasks_dir: Path, out_dir: Path, policy_name: str, quotas: dict,
        hub_key_pem: bytes, timeout_s: int, dry_run: bool = False) -> dict:
    policy = load_policy(policy_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = docker_cmd(image_digest, tasks_dir, out_dir, policy, quotas)
    started = datetime.now(UTC)
    if dry_run:
        print(shlex.join(cmd))
        rc, log = 0, "dry-run"
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
        rc, log = proc.returncode, proc.stdout + proc.stderr
    (out_dir / "run.log").write_text(log)
    answers = out_dir / "answers.jsonl"
    bundle = {
        "image_digest": image_digest,
        "policy": policy_name,
        "quotas": quotas,
        "started": started.isoformat(),
        "finished": datetime.now(UTC).isoformat(),
        "exit_code": rc,
        "answers_sha256": hashlib.sha256(answers.read_bytes()).hexdigest() if answers.exists() else None,
        "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
    }
    sk = C.load_private(hub_key_pem)
    bundle["runner_signature"] = C.sign(sk, C.canonical_json(bundle))
    (out_dir / "bundle.json").write_text(json.dumps(bundle, indent=2))
    return bundle


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("image"); p.add_argument("--tasks", required=True); p.add_argument("--out", required=True)
    p.add_argument("--policy", default="closed"); p.add_argument("--key", required=True)
    p.add_argument("--cpus", default="4"); p.add_argument("--memory", default="16g")
    p.add_argument("--seed", type=int, default=0); p.add_argument("--token-budget", type=int, default=2_000_000)
    p.add_argument("--timeout", type=int, default=6 * 3600); p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    b = run(a.image, Path(a.tasks), Path(a.out), a.policy,
            {"cpus": a.cpus, "memory": a.memory, "seed": a.seed, "token_budget": a.token_budget},
            Path(a.key).read_bytes(), a.timeout, a.dry_run)
    print(json.dumps(b, indent=2))
    sys.exit(b["exit_code"])
