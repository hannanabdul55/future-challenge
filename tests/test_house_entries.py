"""OPS-04 / QSPEC-05: operator (house-*) entries are scored and published but never ranked,
and the ranked relative-skill table depends on ranked-track questions and cluster blocks only."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import demo_season

from fc import questions as Q
from fc import season_board, season_eval

SEASON = Q.SEASON
HOUSE = "house-demo"
N_BOOT = 20


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("house-demo-base")
    demo_season.main(root, 3)
    return root


@pytest.fixture
def work(demo_root: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "season"
    shutil.copytree(demo_root, dst)
    return dst


def add_house(root: Path, model_id: str = HOUSE, source: str = "acme-small") -> None:
    """A copy of `source` registered under a house- id with the same forecasts."""
    doc = yaml.safe_load((root / "model-metadata" / f"{source}.yml").read_text())
    doc["model_id"] = model_id
    (root / "model-metadata" / f"{model_id}.yml").write_text(yaml.safe_dump(doc, sort_keys=False))
    for p in sorted((root / "model-output" / source).glob("*.csv")):
        out = root / "model-output" / model_id / p.name
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(p, out)


def score(root: Path) -> dict:
    return season_eval.run(root, SEASON, n_boot=N_BOOT, min_days=2, allow_unanchored=True)


def rewrite_questions(root: Path, edit) -> None:
    """season_eval loads questions.json without re-checking its hash."""
    qpath = root / "questions" / SEASON / "questions.json"
    doc = json.loads(qpath.read_text())
    qs = doc["questions"] if isinstance(doc, dict) else doc
    for q in qs:
        edit(q)
    qpath.write_text(json.dumps(doc, indent=1) + "\n")


def ranked_rows(board: dict) -> pd.DataFrame:
    return pd.DataFrame(board["relative_skill"]).sort_values(["tier", "model_id"]).reset_index(drop=True)


# ----------------------------------------------------------------------------------- Task 1


def test_house_entry_is_published_but_unranked(work: Path):
    add_house(work)
    board = score(work)
    rs = pd.DataFrame(board["relative_skill"])
    assert not rs.model_id.str.startswith("house-").any()
    house = {h["model_id"]: h for h in board["house_entries"]}
    assert set(house) == {HOUSE}
    h = house[HOUSE]
    assert h["ranked"] is False and h["tier"] == "S"
    for k in ("rel_skill", "lo95", "hi95", "n_days", "n_questions"):
        assert h[k] is not None, k
    assert board["entries"][HOUSE]["house"] is True
    assert board["entries"]["acme-small"]["house"] is False
    out = work / "eval" / "out" / SEASON
    scores = pd.read_csv(out / "scores.csv")
    assert (scores.model_id == HOUSE).any()
    assert (out / "house_skill.csv").is_file()
    assert HOUSE in set(pd.read_csv(out / "calibration.csv").model_id)
    assert HOUSE in set(pd.read_csv(out / "trajectories.csv").model_id)


def test_no_house_entries_means_empty_group(work: Path):
    board = score(work)
    assert board["house_entries"] == []
    assert not any(e["house"] for e in board["entries"].values())


def test_board_renders_house_group_separately(work: Path):
    add_house(work)
    score(work)
    out_html = work / "board.html"
    season_board.build(work / "eval" / "out" / SEASON / "board.json", out_html)
    html = out_html.read_text()
    assert "Operator entries (house) — not ranked" in html
    assert "block bootstrap by day" not in html
    assert "outcome cluster" in html
    head, _, tail = html.partition("Operator entries (house)")
    assert HOUSE in tail
    tier_s = head.split("Tier S")[1]
    assert HOUSE not in tier_s
    assert "S (house, unranked)" in html
    house_section = tail.split("<h2>", 1)[0]
    assert "<th>#</th>" not in house_section
