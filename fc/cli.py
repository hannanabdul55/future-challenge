"""`fc` command line.

fc keygen  --out DIR
fc freeze  --repo ORG/MODEL [--revision REF] --model-id ID --team NAME --tier S|A --out model-metadata/ID.yml
           [--prompt-template FILE --weights-file NAME --public-key FILE --budget-gib N]
fc commit  --round R --model M --submission FILE --rationale FILE --key team.pem --out sidecar.json
fc commit  --round SEASON --entry model-metadata/ID.yml --key team.pem --out sidecar.json
fc reveal  --sidecar sidecar.json --submission FILE --rationale FILE --nonce HEX
fc merkle  --commits a,b,c [--prove i]
fc entropy --round R --drand-round N [--secret HEX --hs HEX] --families f1=100,f2=50 --out entropy.json
fc validate FILE --tasks hub-config/tasks.json [--round R]
fc score   --pred FILE... --truth FILE --baseline fc-baseline --out DIR
fc leaderboard --scores DIR/scores.csv --out leaderboard/index.html
fc verify   --sidecar FILE [--roots DIR --manifest FILE --hub-key FILE --bitcoin-headers FILE --allow-pending --json]
fc verify   --entry model-metadata/ID.yml --season-dir rounds/SEASON [--before ISO | --round-manifest FILE] [--snapshot DIR]
fc service submit --sidecar FILE --round-dir DIR --hub-key FILE [--metadata-dir DIR]
fc entry check FILE... [--resolve] [--metadata-dir model-metadata]
fc questions generate --races FILE --out questions/SEASON/questions.json
fc questions validate FILE [--event-date YYYY-MM-DD]
fc season init --season ID --questions FILE --first-day D --last-day D --last-admission ISO [--root .]
fc season score --season ID [--root . --truth FILE --out DIR --n-boot N --min-days 7]
fc season board --season ID [--root . --out leaderboard/ID.html]
fc packet build --date D --out DIR [--season ID --markets FILE --budget N]
fc round open --season ID --date D --packet DIR [--root .]
fc run --entry model-metadata/ID.yml --season ID --date D [--snapshot DIR | --fetch] [--image IMG]
       [--runner-key FILE --timeout S --dry-run --local --no-gpu --no-drand]
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


def _freeze(a):
    from huggingface_hub import HfApi

    from . import freeze as F
    from . import model_identity as M
    template = Path(a.prompt_template).read_bytes() if a.prompt_template else b""
    inf = M.Inference(weights_file=a.weights_file)
    ident = M.resolve(a.repo, a.revision, api=HfApi(), inference=inf, prompt_template=template)
    gate = M.check_entry(ident, a.tier, budget_bytes=int(a.budget_gib * 1024**3))
    for w in gate.warnings:
        print(f"WARNING {w}")
    for e in gate.errors:
        print(f"ERROR   {e}")
    pem = Path(a.public_key).read_text() if a.public_key else None
    F.write_entry_yaml(Path(a.out), model_id=a.model_id, team=a.team, tier=a.tier,
                       identity=ident, public_key_pem=pem)
    print(f"identity {ident.hash()}\nrevision {ident.revision}\nwrote    {a.out}")
    sys.exit(0 if gate.ok else 1)


def _commit(a):
    if a.entry:
        from . import freeze as F
        sk = C.load_private(C.file_bytes(a.key))
        req, nonce = F.commit_identity(Path(a.entry), season_round_id=a.round, team_sk=sk,
                                       sidecar_path=Path(a.out))
        print(f"commit {req.commit}\nnonce  {nonce.hex()}   (published in {a.entry})\n"
              f"wrote  {a.out}")
        return
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


def _service_submit(a):
    from . import service
    hub_sk = C.load_private(C.file_bytes(a.hub_key))
    receipt, outcome = service.submit_from_sidecar_file(
        a.sidecar, hub_sk, a.round_dir, metadata_dir=a.metadata_dir
    )
    # Print the outcome and the commitment only — never key material.
    print(f"{outcome} {receipt.commit if receipt else ''}".strip())
    sys.exit(0 if outcome == service.ACCEPTED else 1)


def _entry_check(a):
    from . import freeze as F
    ok = True
    for f in a.files:
        g = F.check_entry_file(Path(f), resolve=a.resolve)
        for w in g.warnings:
            print(f"WARNING {f}: {w}")
        for e in g.errors:
            print(f"ERROR   {f}: {e}")
        ok &= g.ok
        print(f"{'OK     ' if g.ok else 'FAIL   '} {f}")
    for e in F.check_team_limits(Path(a.metadata_dir)):
        print(f"ERROR   {e}"); ok = False
    sys.exit(0 if ok else 1)


def _questions(a):
    from . import questions as Q
    if a.questions_cmd == "generate":
        qs = Q.generate_midterms(Path(a.races))
        rep = Q.validate_questions(qs, event_date=Q.EVENT_DATE)
        if not rep.ok:
            print(rep); sys.exit(1)
        h = Q.write_question_set(Path(a.out), qs, season=a.season)
        print(f"{len(qs)} questions  hash {h}\nwrote {a.out}")
    else:
        rep = Q.validate_questions(Q.load_questions(a.file), event_date=a.event_date)
        print(rep); sys.exit(0 if rep.ok else 1)


def _season_init(a):
    from datetime import date as _date

    from . import season as S
    m = S.init_season(Path(a.root), season=a.season, questions_path=Path(a.questions),
                      first_day=_date.fromisoformat(a.first_day),
                      last_day=_date.fromisoformat(a.last_day), last_admission=a.last_admission,
                      runner_image_digest=a.runner_digest, evaluator_image_digest=a.evaluator_digest)
    print(f"season {m['season']}  questions {m['question_count']}  hash {m['questions_hash'][:16]}…")


def _season_score(a):
    from . import season_eval
    b = season_eval.run(Path(a.root), a.season, truth_path=Path(a.truth) if a.truth else None,
                        out_dir=Path(a.out) if a.out else None, n_boot=a.n_boot,
                        min_days=a.min_days)
    rs = pd.DataFrame(b["relative_skill"])
    print(rs.to_string(index=False) if len(rs) else "no rankable entries yet")


def _season_board(a):
    from . import season_board
    out = Path(a.out) if a.out else Path("leaderboard") / f"{a.season}.html"
    season_board.build(Path(a.root) / "eval" / "out" / a.season / "board.json", out)
    print(f"wrote {out}")


def _packet_build(a):
    from datetime import date as _date

    from . import packet as P
    from . import questions as Q
    mapping = json.loads(Path(a.markets).read_text()) if a.markets and Path(a.markets).exists() else {}
    qpath = Path(a.questions) if a.questions else Path("questions") / a.season / "questions.json"
    qs = Q.load_questions(qpath) if qpath.exists() else None
    idx = P.build(_date.fromisoformat(a.date), Path(a.out),
                  sources=P.default_sources(mapping, qs), season=a.season, budget_chars=a.budget)
    print(json.dumps({k: idx[k] for k in ("day", "sections", "packet.md")}, indent=2))


def _round_open(a):
    from datetime import date as _date

    from . import season as S
    m = S.open_round(Path(a.root), season=a.season, d=_date.fromisoformat(a.date),
                     packet_dir=Path(a.packet))
    print(f"opened {m['round_id']}  T_commit {m['T_commit']}  packet {m['packet']['packet_md_sha256'][:16]}…")


def _run(a):
    from . import run as R
    b = R.run_entry(Path(a.entry), root=Path(a.root), season=a.season, day=a.date,
                    snapshot=Path(a.snapshot) if a.snapshot else None, image=a.image,
                    runner_key=Path(a.runner_key) if a.runner_key else None,
                    timeout_s=a.timeout, dry_run=a.dry_run, local=a.local, fetch=a.fetch,
                    gpus=not a.no_gpu, fetch_drand=not a.no_drand)
    print(f"{b['model_id']} {b['round_id']} {b['status']}"
          + (f" ({b['reason']})" if b.get("reason") else "")
          + (f" parse_rate={b.get('parse_rate')}" if b["status"] == "ok" else ""))
    sys.exit(0 if b["status"] in ("ok", "dry-run") else 1)


def _verify(a):
    from . import verify_cli
    if a.entry:
        from . import freeze as F
        before = None
        if a.before:
            before = datetime.fromisoformat(a.before)
        elif a.round_manifest:
            m = json.loads(Path(a.round_manifest).read_text())
            before = datetime.fromisoformat(m["T_open"])
        rep = F.verify_entry(Path(a.entry), season_round_dir=Path(a.season_dir), before=before,
                             snapshot=Path(a.snapshot) if a.snapshot else None,
                             hub_key_path=a.hub_key, headers_path=a.bitcoin_headers,
                             allow_pending=a.allow_pending)
        print(json.dumps(rep.to_dict(), indent=2) if a.json else str(rep))
        sys.exit(0 if rep.ok else 1)
    rep = verify_cli.verify(a.sidecar, roots_dir=a.roots, manifest_path=a.manifest,
                            hub_key_path=a.hub_key, headers_path=a.bitcoin_headers,
                            allow_pending=a.allow_pending, fetch=a.fetch)
    print(json.dumps(rep.to_dict(), indent=2) if a.json else str(rep))
    sys.exit(0 if rep.ok else 1)


def main(argv=None):
    p = argparse.ArgumentParser(prog="fc")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("keygen"); s.add_argument("--out", required=True); s.set_defaults(fn=_keygen)

    s = sp.add_parser("freeze")
    for n in ("--repo", "--model-id", "--team", "--tier", "--out"):
        s.add_argument(n, required=True)
    s.add_argument("--revision", default="main"); s.add_argument("--prompt-template")
    s.add_argument("--weights-file"); s.add_argument("--public-key")
    s.add_argument("--budget-gib", type=float, default=11.0); s.set_defaults(fn=_freeze)

    s = sp.add_parser("commit")
    for n in ("--round", "--key", "--out"):
        s.add_argument(n, required=True)
    s.add_argument("--model"); s.add_argument("--submission"); s.add_argument("--entry")
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

    s = sp.add_parser("service")
    ss = s.add_subparsers(dest="service_cmd", required=True)
    sub = ss.add_parser("submit")
    for n in ("--sidecar", "--round-dir", "--hub-key"):
        sub.add_argument(n, required=True)
    sub.add_argument("--metadata-dir", default="model-metadata")
    sub.set_defaults(fn=_service_submit)

    s = sp.add_parser("entry")
    es = s.add_subparsers(dest="entry_cmd", required=True)
    ec = es.add_parser("check"); ec.add_argument("files", nargs="+")
    ec.add_argument("--resolve", action="store_true"); ec.add_argument("--metadata-dir", default="model-metadata")
    ec.set_defaults(fn=_entry_check)

    s = sp.add_parser("questions")
    qs = s.add_subparsers(dest="questions_cmd", required=True)
    g = qs.add_parser("generate"); g.add_argument("--races", required=True)
    g.add_argument("--out", required=True); g.add_argument("--season", default="2026-us-midterms")
    g.set_defaults(fn=_questions)
    v = qs.add_parser("validate"); v.add_argument("file"); v.add_argument("--event-date")
    v.set_defaults(fn=_questions)

    s = sp.add_parser("season")
    ss = s.add_subparsers(dest="season_cmd", required=True)
    si = ss.add_parser("init")
    for n in ("--season", "--questions", "--first-day", "--last-day", "--last-admission"):
        si.add_argument(n, required=True)
    si.add_argument("--root", default="."); si.add_argument("--runner-digest")
    si.add_argument("--evaluator-digest"); si.set_defaults(fn=_season_init)
    sc = ss.add_parser("score"); sc.add_argument("--season", required=True)
    sc.add_argument("--root", default="."); sc.add_argument("--truth"); sc.add_argument("--out")
    sc.add_argument("--n-boot", type=int, default=200); sc.add_argument("--min-days", type=int, default=7)
    sc.set_defaults(fn=_season_score)
    sb = ss.add_parser("board"); sb.add_argument("--season", required=True)
    sb.add_argument("--root", default="."); sb.add_argument("--out"); sb.set_defaults(fn=_season_board)

    s = sp.add_parser("packet")
    ps = s.add_subparsers(dest="packet_cmd", required=True)
    pb = ps.add_parser("build"); pb.add_argument("--date", required=True)
    pb.add_argument("--out", required=True); pb.add_argument("--season", default="2026-us-midterms")
    pb.add_argument("--markets"); pb.add_argument("--questions")
    pb.add_argument("--budget", type=int, default=60_000)
    pb.set_defaults(fn=_packet_build)

    s = sp.add_parser("round")
    rs = s.add_subparsers(dest="round_cmd", required=True)
    ro = rs.add_parser("open")
    for n in ("--season", "--date", "--packet"):
        ro.add_argument(n, required=True)
    ro.add_argument("--root", default="."); ro.set_defaults(fn=_round_open)

    s = sp.add_parser("run")
    for n in ("--entry", "--season", "--date"):
        s.add_argument(n, required=True)
    s.add_argument("--root", default="."); s.add_argument("--snapshot"); s.add_argument("--fetch", action="store_true")
    s.add_argument("--image", default="fc-forecaster:2026.10"); s.add_argument("--runner-key")
    s.add_argument("--timeout", type=int, default=3600); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--local", action="store_true"); s.add_argument("--no-gpu", action="store_true")
    s.add_argument("--no-drand", action="store_true"); s.set_defaults(fn=_run)

    s = sp.add_parser("verify"); s.add_argument("--sidecar")
    s.add_argument("--entry"); s.add_argument("--season-dir"); s.add_argument("--before")
    s.add_argument("--round-manifest"); s.add_argument("--snapshot")
    s.add_argument("--roots"); s.add_argument("--manifest"); s.add_argument("--hub-key")
    s.add_argument("--bitcoin-headers")
    s.add_argument("--allow-pending", action="store_true")
    s.add_argument("--fetch", action="store_true")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=_verify)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
