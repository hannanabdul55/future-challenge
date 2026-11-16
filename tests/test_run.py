"""RUN-02: the host runner verifies weights before anything else, derives the day's seed from
the manifest, builds a network-disabled docker invocation, and always leaves a signed bundle."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import yaml

from fc import commit as C
from fc import freeze as F
from fc import model_identity as M
from fc import packet as P
from fc import questions as Q
from fc import run as R
from fc import season as S

RACES = Path(__file__).resolve().parents[1] / "questions" / "2026-us-midterms" / "races.csv"
WEIGHTS = b"GGUF" + b"\x00" * 4096
WSHA = hashlib.sha256(WEIGHTS).hexdigest()


class FakeApi:
    def model_info(self, repo_id, *, revision=None, files_metadata=False):
        sib = SimpleNamespace(rfilename="m.gguf", size=len(WEIGHTS), blob_id="b" * 40,
                              lfs={"sha256": WSHA, "size": len(WEIGHTS), "pointer_size": 134})
        return SimpleNamespace(sha="3" * 40, siblings=[sib], card_data={"license": "mit"},
                               gated=False)

    def list_repo_commits(self, repo_id, *, revision=None):
        return []


def _season(tmp_path: Path, mutate=None) -> tuple[Path, Path]:
    qs = Q.generate_midterms(RACES)
    if mutate is not None:
        mutate(qs)
    qpath = tmp_path / "questions" / Q.SEASON / "questions.json"
    Q.write_question_set(qpath, qs, season=Q.SEASON)
    S.init_season(tmp_path, season=Q.SEASON, questions_path=qpath, first_day=date(2026, 10, 26),
                  last_day=date(2026, 11, 3), last_admission="2026-11-03T12:00:00Z")
    pdir = tmp_path / "target-data" / "packets" / Q.SEASON / "2026-10-26"
    srcs = P.default_sources({})
    srcs[-1].pause_s = 0  # type: ignore[attr-defined]
    srcs[0].pause_s = 0  # type: ignore[attr-defined]

    def http(url, params=None):
        raise TimeoutError("offline")

    P.build(date(2026, 10, 26), pdir, sources=srcs, http=http, season=Q.SEASON,
            now=datetime(2026, 10, 26, tzinfo=UTC))
    S.open_round(tmp_path, season=Q.SEASON, d=date(2026, 10, 26), packet_dir=pdir,
                 now=datetime(2026, 10, 26, tzinfo=UTC))
    template = Path(__file__).resolve().parents[1] / "fc" / "prompts" / "forecaster-v1.md"
    ident = M.resolve("org/m", "main", api=FakeApi(), prompt_template=template.read_bytes())
    meta = tmp_path / "model-metadata" / "acme-m.yml"
    F.write_entry_yaml(meta, model_id="acme-m", team="Acme", tier="S", identity=ident,
                       public_key_pem=None)
    return meta, tmp_path


def test_dry_run_builds_sealed_command_and_signed_bundle(tmp_path):
    meta, root = _season(tmp_path)
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "m.gguf").write_bytes(WEIGHTS)
    priv, pub = C.generate_keypair()
    key = tmp_path / "runner.pem"
    key.write_bytes(priv)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day="2026-10-26", snapshot=snap,
                    runner_key=key, dry_run=True, fetch_drand=False)
    assert b["status"] == "dry-run"
    cmd = b["command"]
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--read-only" in cmd and "--gpus" in cmd
    assert any(v.endswith(":/model:ro") for v in cmd)
    assert b["weights_sha256"] == WSHA
    assert b["seed_source"]["source"].startswith("fallback")
    # Signed over everything but the signature itself.
    payload = C.canonical_json({k: v for k, v in b.items() if k != "runner_signature"})
    assert C.verify_sig(C.load_public(pub), R.RUN_DOMAIN_SEP + payload, b["runner_signature"])
    bundle = json.loads((root / "runs" / Q.SEASON / "2026-10-26" / "acme-m" / "bundle.json").read_text())
    assert bundle["identity_hash"] == b["identity_hash"]


def test_tampered_weights_are_refused_before_running(tmp_path):
    meta, root = _season(tmp_path)
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "m.gguf").write_bytes(WEIGHTS[:-1] + b"!")
    b = R.run_entry(meta, root=root, season=Q.SEASON, day="2026-10-26", snapshot=snap,
                    dry_run=True, fetch_drand=False)
    assert b["status"] == "missing" and "weights verification failed" in b["reason"]
    assert "command" not in b


def test_prompt_template_drift_is_refused(tmp_path):
    meta, root = _season(tmp_path)
    doc = yaml.safe_load(meta.read_text())
    rec = doc["model_identity"]
    rec["prompt_template_sha256"] = "0" * 64
    rec["identity_hash"] = M.identity_hash(rec)
    meta.write_text(yaml.safe_dump(doc, sort_keys=False))
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "m.gguf").write_bytes(WEIGHTS)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day="2026-10-26", snapshot=snap,
                    dry_run=True, fetch_drand=False)
    assert b["status"] == "missing" and "prompt template" in b["reason"]


def test_tier_a_entries_are_not_hub_run(tmp_path):
    meta, root = _season(tmp_path)
    doc = yaml.safe_load(meta.read_text())
    doc["tier"] = "A"
    meta.write_text(yaml.safe_dump(doc, sort_keys=False))
    try:
        R.run_entry(meta, root=root, season=Q.SEASON, day="2026-10-26", snapshot=tmp_path,
                    dry_run=True, fetch_drand=False)
    except ValueError as exc:
        assert "tier A" in str(exc)
    else:
        raise AssertionError("tier A entry was accepted for hub execution")


def _snap(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "m.gguf").write_bytes(WEIGHTS)
    return snap


def _env_value(cmd, name):
    return next(cmd[i + 1].split("=", 1)[1] for i, v in enumerate(cmd)
                if v == "-e" and cmd[i + 1].startswith(name + "="))


def test_fc_thinking_follows_the_frozen_identity(tmp_path):
    meta, root = _season(tmp_path)
    snap = _snap(tmp_path)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day="2026-10-26", snapshot=snap,
                    dry_run=True, fetch_drand=False)
    assert _env_value(b["command"], "FC_THINKING") == "0" and b["thinking"] is False

    template = Path(__file__).resolve().parents[1] / "fc" / "prompts" / "forecaster-v1.md"
    ident = M.resolve("org/m", "main", api=FakeApi(), prompt_template=template.read_bytes(),
                      inference=M.Inference(weights_file="m.gguf", thinking=True))
    F.write_entry_yaml(meta, model_id="acme-m", team="Acme", tier="S", identity=ident,
                       public_key_pem=None)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day="2026-10-26", snapshot=snap,
                    dry_run=True, fetch_drand=False)
    assert _env_value(b["command"], "FC_THINKING") == "1" and b["thinking"] is True


def test_zero_parsed_rows_is_an_empty_signed_bundle(tmp_path, monkeypatch):
    meta, root = _season(tmp_path)
    snap = _snap(tmp_path)

    def fake_local(snapshot, paths, prompt, out, env, timeout_s):
        import pandas as pd

        from fc import forecast_io as FIO
        pd.DataFrame(columns=FIO.OUTPUT_COLS).to_csv(out / "forecast.csv", index=False)
        (out / "status.json").write_text(json.dumps({"parse_rate": 0.0, "n_missing": 75}))
        (out / "transcript.jsonl").write_text("{}\n")
        return 0

    monkeypatch.setattr(R, "_run_local", fake_local)
    priv, _ = C.generate_keypair()
    key = tmp_path / "runner.pem"
    key.write_bytes(priv)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day="2026-10-26", snapshot=snap,
                    runner_key=key, local=True, fetch_drand=False)
    assert b["status"] == "empty" and "no parseable answers" in b["reason"]
    assert b["runner_signature"]
    run_dir = root / "runs" / Q.SEASON / "2026-10-26" / "acme-m"
    assert (run_dir / "bundle.json").is_file() and (run_dir / "status.json").is_file()
    assert (run_dir / "transcript.jsonl").is_file()
    assert not (root / "model-output" / "acme-m").exists()


# --------------------------------------------------------------------------- plan 06-02

DAY = "2026-10-26"


def _resolve_on(qs, day, only=None):
    for q in qs:
        if only is None or q["question_id"] == only:
            q["resolution"] = {**(q.get("resolution") or {}), "expected_resolution_date": day}


def _mounts(cmd):
    return [cmd[i + 1] for i, v in enumerate(cmd) if v == "-v"]


def test_only_active_questions_are_mounted_and_asked(tmp_path):
    meta, root = _season(tmp_path, mutate=lambda qs: _resolve_on(qs, DAY, only="control-house"))
    snap = _snap(tmp_path)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, dry_run=True,
                    fetch_drand=False)
    qdir = root / "runs" / Q.SEASON / DAY / "acme-m" / "questions"
    asked = Q.load_questions(qdir / "questions.json")
    ids = {q["question_id"] for q in asked}
    full = Q.load_questions(root / "questions" / Q.SEASON / "questions.json")
    assert "control-house" not in ids and len(ids) == len(full) - 1
    assert b["asked_questions"] == len(ids) and b["asked_question_ids"] == sorted(ids)
    assert b["asked_questions_hash"] == Q.questions_hash(asked)
    mounts = _mounts(b["command"])
    assert f"runs/{Q.SEASON}/{DAY}/acme-m/questions:/questions:ro" in mounts
    assert not any(m.startswith(f"questions/{Q.SEASON}:") for m in mounts)


def test_runtime_overrides_are_allow_listed_validated_and_recorded(tmp_path, monkeypatch):
    meta, root = _season(tmp_path)
    snap = _snap(tmp_path)
    monkeypatch.setenv("FC_BATCH", "20")
    monkeypatch.setenv("FC_GPU_LAYERS", "40")
    monkeypatch.setenv("FC_CTX", "512")  # identity-derived: must never come from the host
    monkeypatch.setenv("FC_MAX_TOKENS", "7")
    b = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, dry_run=True,
                    fetch_drand=False)
    assert _env_value(b["command"], "FC_BATCH") == "20"
    assert _env_value(b["command"], "FC_GPU_LAYERS") == "40"
    assert b["runtime_overrides"] == {"FC_BATCH": "20", "FC_GPU_LAYERS": "40"}
    assert _env_value(b["command"], "FC_CTX") == "16384"
    assert _env_value(b["command"], "FC_MAX_TOKENS") == "4096"
    monkeypatch.setenv("FC_BATCH", "abc")
    bad = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, dry_run=True,
                      fetch_drand=False)
    assert bad["status"] == "missing" and "invalid runtime override FC_BATCH='abc'" in bad["reason"]
    monkeypatch.delenv("FC_BATCH")
    monkeypatch.delenv("FC_GPU_LAYERS")
    none = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, dry_run=True,
                       fetch_drand=False)
    assert none["runtime_overrides"] == {}


def _pin(root, digest):
    mp = root / "rounds" / Q.SEASON / DAY / "manifest.json"
    m = json.loads(mp.read_text())
    m["runner_image_digest"] = digest
    mp.write_text(json.dumps(m))


def test_image_that_is_not_the_pinned_digest_yields_a_missing_bundle(tmp_path, monkeypatch):
    meta, root = _season(tmp_path)
    snap = _snap(tmp_path)
    _pin(root, "sha256:aaa")

    def boom(*a, **k):
        raise AssertionError("docker must not run on a digest mismatch")

    monkeypatch.setattr(R.subprocess, "run", boom)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, fetch_drand=False,
                    image_inspect=lambda image: "sha256:bbb")
    assert b["status"] == "missing"
    assert "does not match the season's pinned digest" in b["reason"]
    assert b["image_id"] == "sha256:bbb"
    gone = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, fetch_drand=False,
                       image_inspect=lambda image: None)
    assert gone["status"] == "missing" and "not found" in gone["reason"]


def test_no_active_questions_is_a_signed_missing_bundle(tmp_path):
    meta, root = _season(tmp_path, mutate=lambda qs: _resolve_on(qs, DAY))
    snap = _snap(tmp_path)
    priv, _ = C.generate_keypair()
    key = tmp_path / "runner.pem"
    key.write_bytes(priv)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, runner_key=key,
                    dry_run=True, fetch_drand=False)
    assert b["status"] == "missing" and b["reason"] == f"no active questions on {DAY}"
    assert b["runner_signature"] and "command" not in b


def _uniform_forecast(qs, round_id):
    import pandas as pd
    rows = []
    for q in qs:
        qid, t = q["question_id"], q["output_type"]
        if t == "binary":
            rows.append((qid, t, "p", 0.5))
        elif t == "categorical":
            rows += [(qid, t, c, 1 / len(q["categories"])) for c in q["categories"]]
        else:
            lv = [float(x) for x in q["quantile_levels"]]
            lo, hi = q.get("bounds") or (0, len(lv))
            rows += [(qid, t, str(x), lo + (hi - lo) * (i + 1) / (len(lv) + 1))
                     for i, x in enumerate(lv)]
    return pd.DataFrame([(round_id, *r) for r in rows],
                        columns=["round_id", "question_id", "output_type", "output_type_id",
                                 "value"])


def test_signed_bundle_carries_no_host_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    snap = home / ".cache" / "hf" / "snap"
    snap.mkdir(parents=True)
    (snap / "m.gguf").write_bytes(WEIGHTS)
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    meta, root = _season(repo)
    priv, pub = C.generate_keypair()
    key = tmp_path / "runner.pem"
    key.write_bytes(priv)
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = list(argv)
        rid = next(a.split("=", 1)[1] for a in argv if a.startswith("FC_ROUND_ID="))
        out = Path(next(m for m in _mounts(argv) if m.endswith(":/out")).split(":/out")[0])
        qdir = Path(next(m for m in _mounts(argv) if m.endswith(":/questions:ro"))
                    .split(":/questions")[0])
        qs = Q.load_questions(qdir / "questions.json")
        _uniform_forecast(qs, rid).to_csv(out / "forecast.csv", index=False)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(R.subprocess, "run", fake_run)
    b = R.run_entry(meta, root=root, season=Q.SEASON, day=DAY, snapshot=snap, runner_key=key,
                    fetch_drand=False, image_inspect=lambda image: "sha256:abc")
    assert b["status"] == "ok", b.get("reason")
    stored = (root / "runs" / Q.SEASON / DAY / "acme-m" / "bundle.json").read_text()
    for leak in (str(tmp_path), str(tmp_path.resolve()), str(home), os.path.realpath(home)):
        assert leak not in stored
    mounts = _mounts(b["command"])
    assert "~/.cache/hf/snap:/model:ro" in mounts
    assert f"runs/{Q.SEASON}/{DAY}/acme-m/questions:/questions:ro" in mounts
    assert f"runs/{Q.SEASON}/{DAY}/acme-m/out:/out" in mounts
    assert f"target-data/packets/{Q.SEASON}/{DAY}:/packet:ro" in mounts
    # the docker invocation itself still uses absolute host paths
    real = _mounts(seen["argv"])
    assert all(m.startswith("/") for m in real)
    assert str(snap.resolve()) + ":/model:ro" in real or str(snap) + ":/model:ro" in real
    bundle = json.loads(stored)
    payload = C.canonical_json({k: v for k, v in bundle.items() if k != "runner_signature"})
    assert C.verify_sig(C.load_public(pub), R.RUN_DOMAIN_SEP + payload, bundle["runner_signature"])
