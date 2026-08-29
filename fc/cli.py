"""`fc` command line.

fc keygen  --out DIR
fc commit  --round R --model M --submission FILE --rationale FILE --key team.pem --out sidecar.json
fc reveal  --sidecar sidecar.json --submission FILE --rationale FILE --nonce HEX
fc merkle  --commits a,b,c [--prove i]
fc entropy --round R --drand-round N [--secret HEX --hs HEX] --families f1=100,f2=50 --out entropy.json
fc validate FILE --tasks hub-config/tasks.json [--round R]
fc score   --pred FILE... --truth FILE --baseline fc-baseline --out DIR
fc leaderboard --scores DIR/scores.csv --out leaderboard/index.html
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import commit as C
from . import entropy as E
from .eval import bootstrap_relative_skill, contribution, score_quantile_frame
from .validate import read_output, validate_file


def _keygen(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    priv, pub = C.generate_keypair()
    (out / "private.pem").write_bytes(priv); (out / "public.pem").write_bytes(pub)
    print(f"wrote {out/'private.pem'} and {out/'public.pem'}")


def _commit(a):
    sub = C.file_bytes(a.submission)
    rat = C.file_bytes(a.rationale) if a.rationale else b""
    nonce = C.new_nonce()
    c = C.commitment(sub, rat, nonce)
    sk = C.load_private(C.file_bytes(a.key))
    req = C.team_sign_request(sk, a.round, a.model, c)
    rec = {"round_id": a.round, "model_id": a.model, "commit": c,
           "team_signature": req.team_signature,
           "created": datetime.now(UTC).isoformat()}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rec, indent=2) + "\n")
    print(f"commit {c}\nnonce  {nonce.hex()}   <- keep private until T_reveal\nwrote  {a.out}")


def _reveal(a):
    rec = json.loads(Path(a.sidecar).read_text())
    ok = C.verify_reveal(rec["commit"], C.file_bytes(a.submission),
                         C.file_bytes(a.rationale) if a.rationale else b"", bytes.fromhex(a.nonce))
    print("reveal OK" if ok else "reveal MISMATCH")
    sys.exit(0 if ok else 1)


def _merkle(a):
    commits = a.commits.split(",")
    root = C.merkle_root(commits)
    print(f"root {root}")
    if a.prove is not None:
        proof = C.merkle_proof(commits, a.prove)
        print(json.dumps(proof))
        print("verify", C.merkle_verify(commits[a.prove], proof, root))


def _entropy(a):
    fams = dict(kv.split("=") for kv in a.families.split(",")) if a.families else {}
    fams = {k: int(v) for k, v in fams.items()}
    if a.secret is None:
        S = E.new_hub_secret()
        print(f"H(S) {E.secret_commitment(S)}  publish + anchor before T_commit")
        print(f"S    {S.hex()}                  keep secret until T_entropy")
        rec = E.entropy_record(a.round, E.secret_commitment(S), None, None, fams)
    else:
        S = bytes.fromhex(a.secret)
        d = E.fetch_drand(a.drand_round)
        rec = E.entropy_record(a.round, a.hs, S, d, fams)
        print(f"E {rec['E']}")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(rec, indent=2) + "\n")
        print(f"wrote {a.out}")


def _validate(a):
    rep = validate_file(a.file, a.tasks, a.round)
    print(rep)
    sys.exit(0 if rep.ok else 1)


def _score(a):
    preds = []
    for f in a.pred:
        df = read_output(f)
        if "model_id" not in df.columns:
            df.insert(0, "model_id", Path(f).parent.name)
        preds.append(df)
    pred = pd.concat(preds, ignore_index=True)
    truth = pd.read_csv(a.truth)
    scores = score_quantile_frame(pred, truth)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    scores.to_csv(out / "scores.csv", index=False)
    try:
        rs = bootstrap_relative_skill(scores, a.baseline, n_boot=a.n_boot, min_shared=a.min_shared)
        rs.to_csv(out / "relative_skill.csv")
        print(rs.round(3).to_string())
    except ValueError as e:
        print(f"relative skill skipped: {e}")
    contrib = contribution(pred, truth, score_quantile_frame)
    contrib.to_csv(out / "contribution.csv")
    print("\ncontribution to ensemble:\n" + contrib.round(4).to_string())


def _leaderboard(a):
    from .leaderboard import build
    build(Path(a.scores).parent, Path(a.out))
    print(f"wrote {a.out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="fc")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("keygen"); s.add_argument("--out", required=True); s.set_defaults(fn=_keygen)

    s = sp.add_parser("commit")
    for n in ("--round", "--model", "--submission", "--key", "--out"):
        s.add_argument(n, required=True)
    s.add_argument("--rationale"); s.set_defaults(fn=_commit)

    s = sp.add_parser("reveal")
    for n in ("--sidecar", "--submission", "--nonce"):
        s.add_argument(n, required=True)
    s.add_argument("--rationale"); s.set_defaults(fn=_reveal)

    s = sp.add_parser("merkle"); s.add_argument("--commits", required=True)
    s.add_argument("--prove", type=int); s.set_defaults(fn=_merkle)

    s = sp.add_parser("entropy"); s.add_argument("--round", required=True)
    s.add_argument("--drand-round", type=int); s.add_argument("--secret"); s.add_argument("--hs")
    s.add_argument("--families"); s.add_argument("--out"); s.set_defaults(fn=_entropy)

    s = sp.add_parser("validate"); s.add_argument("file"); s.add_argument("--tasks", required=True)
    s.add_argument("--round"); s.set_defaults(fn=_validate)

    s = sp.add_parser("score"); s.add_argument("--pred", nargs="+", required=True)
    s.add_argument("--truth", required=True); s.add_argument("--baseline", default="fc-baseline")
    s.add_argument("--out", default="eval/out"); s.add_argument("--n-boot", type=int, default=200)
    s.add_argument("--min-shared", type=int, default=20); s.set_defaults(fn=_score)

    s = sp.add_parser("leaderboard"); s.add_argument("--scores", required=True)
    s.add_argument("--out", default="leaderboard/index.html"); s.set_defaults(fn=_leaderboard)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
