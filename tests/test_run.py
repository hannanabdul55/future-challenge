"""RUN-02: the host runner verifies weights before anything else, derives the day's seed from
the manifest, builds a network-disabled docker invocation, and always leaves a signed bundle."""

from __future__ import annotations

import hashlib
import json
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


def _season(tmp_path: Path) -> tuple[Path, Path]:
    qs = Q.generate_midterms(RACES)
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
