# runner — sealed execution (Tier S)

`run.py` builds a locked-down `docker run` for a container pinned by digest, mounts the
generated tasks read-only, enforces quotas, applies an egress policy, and signs the result
bundle with the runner key.

Deployment requirements not included in this scaffold:

- gVisor (`runsc`) for CPU jobs; firecracker-containerd for GPU jobs; one job per VM.
- `fc-proxy`: an mitmproxy-based logging proxy on the `fc-egress` network implementing
  `policies/allowlist.yaml` / `open-logged.yaml`, writing hashed request/response logs
  that are published with the round.
- Image pulls by digest only; a pull-through cache pinned at `T_commit`.
- Runner key in an HSM or KMS; never inside the container's environment.

Dry run:

    python runner/run.py ghcr.io/team/agent@sha256:<digest> --tasks rounds/2026-10-G01/tasks \
        --out runs/2026-10-G01/team-agent --policy closed --key keys/runner-private.pem --dry-run
