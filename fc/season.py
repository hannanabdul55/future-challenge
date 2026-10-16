"""Season and daily-round manifests (ROUND-01, ROUND-02; DESIGN.md §3.4).

Layout under ``rounds/<season>/``:

    season.json              the season manifest (pins everything a round depends on)
    season/manifest.json     the *freeze round*: entries commit identities here; its
                             T_commit is the season's last admission instant
    <YYYY-MM-DD>/manifest.json   one daily round; its T_open/T_commit/T_reveal come from
                             the season schedule and the service enforces them unchanged

All instants are RFC 3339 UTC. Deadlines are written once, here, and read everywhere else.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from . import commit as C
from . import entropy as E
from . import questions as Q

SCHEMA = "fc-season/2026.10"


@dataclass(frozen=True)
class Schedule:
    open_utc: str = "00:00"
    commit_utc: str = "12:00"
    reveal_utc: str = "18:00"

    def instants(self, d: date) -> dict[str, str]:
        def at(hhmm: str) -> str:
            h, m = (int(x) for x in hhmm.split(":"))
            return datetime.combine(d, time(h, m, tzinfo=UTC)).isoformat().replace("+00:00", "Z")
        return {"T_open": at(self.open_utc), "T_commit": at(self.commit_utc),
                "T_reveal": at(self.reveal_utc)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def round_id(season: str, d: date) -> str:
    return f"{season}-{d.isoformat()}"


def init_season(root: Path, *, season: str, questions_path: Path, first_day: date,
                last_day: date, last_admission: str, rules_version: str = Q.RULES,
                schedule: Schedule | None = None, runner_image_digest: str | None = None,
                evaluator_image_digest: str | None = None, min_scored_days: int = 7,
                notes: str = "") -> dict[str, Any]:
    """Write ``rounds/<season>/season.json`` and the freeze round manifest."""
    schedule = schedule or Schedule()
    qs = Q.load_questions(questions_path)
    rep = Q.validate_questions(qs)
    if not rep.ok:
        raise ValueError(f"question set invalid:\n{rep}")
    sdir = root / "rounds" / season
    sdir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": SCHEMA,
        "season": season,
        "rules_version": rules_version,
        "questions_path": str(questions_path),
        "questions_sha256": _sha(questions_path),
        "questions_hash": Q.questions_hash(qs),
        "question_count": len(qs),
        "schedule": {"open_utc": schedule.open_utc, "commit_utc": schedule.commit_utc,
                     "reveal_utc": schedule.reveal_utc},
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "last_admission": last_admission,
        "T_final": {q["family"]: q["resolution"]["T_final"] for q in qs},
        "runner_image_digest": runner_image_digest,
        "evaluator_image_digest": evaluator_image_digest,
        "min_scored_days_for_headline": min_scored_days,
        "tier_s_sandbox": "docker --network none, read-only mounts, wall-clock quota; "
                          "no gVisor (DESIGN.md §3.4)",
        "notes": notes,
    }
    (sdir / "season.json").write_text(json.dumps(manifest, indent=2) + "\n")
    freeze_dir = sdir / "season"
    freeze_dir.mkdir(exist_ok=True)
    freeze_manifest = {
        "round_id": f"{season}-season",
        "kind": "freeze",
        "rules_version": rules_version,
        "T_open": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "T_commit": last_admission,
        "T_reveal": last_admission,
        "season_manifest_sha256": _sha(sdir / "season.json"),
        "notes": "Entries commit model identities here (rules 2026.10 §2). An entry is "
                 "scored on a daily round only if its anchor precedes that round's T_open.",
    }
    (freeze_dir / "manifest.json").write_text(json.dumps(freeze_manifest, indent=2) + "\n")
    return manifest


def load_season(root: Path, season: str) -> dict[str, Any]:
    return json.loads((root / "rounds" / season / "season.json").read_text())


def open_round(root: Path, *, season: str, d: date, packet_dir: Path,
               hub_secret_commitment: str | None = None,
               now: datetime | None = None) -> dict[str, Any]:
    """Write the daily round manifest for day ``d`` (ROUND-02).

    The packet must already exist: its hash index is what the manifest pins. The drand
    round is the first quicknet round at or after T_open; the day's seed is derived from
    it at T_run so the runner's sampling seed did not exist when weights were frozen.
    """
    sm = load_season(root, season)
    sched = Schedule(**sm["schedule"])
    inst = sched.instants(d)
    if not (sm["first_day"] <= d.isoformat() <= sm["last_day"]):
        raise ValueError(f"{d} outside season {sm['first_day']}..{sm['last_day']}")
    index = packet_dir / "index.json"
    if not index.is_file():
        raise FileNotFoundError(f"packet index missing: {index}")
    idx = json.loads(index.read_text())
    t_open = datetime.fromisoformat(inst["T_open"])
    manifest = {
        "round_id": round_id(season, d),
        "kind": "daily",
        "season": season,
        "day": d.isoformat(),
        "rules_version": sm["rules_version"],
        **inst,
        "questions_hash": sm["questions_hash"],
        "season_manifest_sha256": _sha(root / "rounds" / season / "season.json"),
        "packet": {"path": str(packet_dir), "index_sha256": _sha(index),
                   "packet_json_sha256": idx.get("packet.json"),
                   "packet_md_sha256": idx.get("packet.md"),
                   "built_at": idx.get("built_at")},
        "drand": {"chain": "quicknet", "round": E.round_after(t_open, margin_s=0),
                  "expected_time": E.round_time(E.round_after(t_open, margin_s=0))
                  .isoformat().replace("+00:00", "Z")},
        "hub_secret_commitment": hub_secret_commitment,
        "runner_image_digest": sm.get("runner_image_digest"),
        "evaluator_image_digest": sm.get("evaluator_image_digest"),
        "published_at": (now or datetime.now(UTC)).isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    rdir = root / "rounds" / season / d.isoformat()
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def days(sm: dict[str, Any]) -> list[date]:
    a, b = date.fromisoformat(sm["first_day"]), date.fromisoformat(sm["last_day"])
    return [a + timedelta(days=i) for i in range((b - a).days + 1)]


def manifest_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(C.canonical_json(manifest)).hexdigest()
