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


# ----------------------------------------------------------------------------------- Task 2

WORLD_QIDS = ["senate-al", "senate-ak", "senate-ga"]


def mark_world(root: Path, qids=WORLD_QIDS) -> None:
    rewrite_questions(root, lambda q: q.update(track="world") if q["question_id"] in qids else None)


def scramble_forecasts(root: Path, qids=WORLD_QIDS) -> None:
    """Change every entry's forecast on `qids` (rotate the category probabilities)."""
    for p in sorted((root / "model-output").glob("*/*.csv")):
        df = pd.read_csv(p)
        m = df.question_id.isin(qids)
        assert m.any()
        df.loc[m, "value"] = df[m].groupby("question_id")["value"].transform(lambda v: v.to_numpy()[[1, 2, 0]])
        df.to_csv(p, index=False)


def test_world_track_scores_cannot_move_ranked_rows(work: Path, tmp_path: Path):
    mark_world(work)
    before = ranked_rows(score(work))
    other = tmp_path / "other"
    shutil.copytree(work, other)
    scramble_forecasts(other)
    after = ranked_rows(score(other))
    # The world questions were scored (and so differ) ...
    s0 = pd.read_csv(work / "eval" / "out" / SEASON / "scores.csv")
    s1 = pd.read_csv(other / "eval" / "out" / SEASON / "scores.csv")
    w = s0.question_id.isin(WORLD_QIDS)
    assert w.any() and not s0[w].score.reset_index(drop=True).equals(s1[w].score.reset_index(drop=True))
    # ... but the ranked table is bit-for-bit identical.
    pd.testing.assert_frame_equal(before, after, check_exact=True)


def test_adding_a_house_entry_leaves_every_ranked_row_identical(work: Path, tmp_path: Path):
    without = ranked_rows(score(work))
    withh = tmp_path / "withh"
    shutil.copytree(work, withh)
    add_house(withh)
    with_house = ranked_rows(score(withh))
    pd.testing.assert_frame_equal(without, with_house, check_exact=True)
    assert "hub-ensemble" in set(with_house.model_id)  # the ensemble row is still compared


def test_house_skill_does_not_depend_on_other_house_entries(work: Path, tmp_path: Path):
    add_house(work)
    alone = {h["model_id"]: h for h in score(work)["house_entries"]}[HOUSE]
    two = tmp_path / "two"
    shutil.copytree(work, two)
    add_house(two, "house-other", source="zed-noisy")
    both = {h["model_id"]: h for h in score(two)["house_entries"]}
    assert set(both) == {HOUSE, "house-other"}
    assert both[HOUSE]["rel_skill"] == alone["rel_skill"]


def test_bootstrap_blocks_are_declared_clusters(work: Path, monkeypatch: pytest.MonkeyPatch):
    clusters = {}

    def tag(q):
        if q["question_id"].startswith("senate-"):
            q["cluster"] = f"senate-{q['question_id'][-1]}"
            clusters[q["question_id"]] = q["cluster"]

    rewrite_questions(work, tag)
    assert clusters
    seen: list[pd.Series] = []
    real = season_eval.bootstrap_relative_skill

    def spy(scores, baseline, **kw):
        seen.append(kw["block"])
        return real(scores, baseline, **kw)

    monkeypatch.setattr(season_eval, "bootstrap_relative_skill", spy)
    add_house(work)
    score(work)
    assert seen
    for block in seen:
        for key, blk in block.items():
            day, qid = key.split("/", 1)
            # a declared cluster where the question has one, the day otherwise
            assert blk == clusters.get(qid, day)


def test_ensemble_excludes_house(work: Path, tmp_path: Path):
    # Two external entries + a house entry: the ensemble is the mean of the externals only.
    add_house(work, source="zed-noisy")
    score(work)
    fcs = pd.read_csv(work / "eval" / "out" / SEASON / "forecasts.csv")
    key = ["day", "question_id", "output_type", "output_type_id"]
    # Binary rows are left out: ensemble_mean_probs renormalises a lone binary "p" to 1.0
    # (pre-existing, logged in deferred-items.md; outside this plan's files).
    fcs = fcs[fcs.output_type != "binary"]
    ext = fcs[fcs.model_id.isin(["acme-small", "acme-sharp", "zed-noisy"])]
    want = ext.groupby(key)["value"].mean()
    got = fcs[fcs.model_id == "hub-ensemble"].set_index(key)["value"]
    assert len(got) == len(want)
    pd.testing.assert_series_equal(got.sort_index(), want.sort_index(), check_names=False,
                                   atol=1e-9, rtol=0)
    # One external entry + a house entry: not enough external entries, so no ensemble at all.
    one = tmp_path / "one"
    shutil.copytree(work, one)
    for m in ("acme-sharp", "zed-noisy"):
        (one / "model-metadata" / f"{m}.yml").unlink()
        shutil.rmtree(one / "model-output" / m)
    score(one)
    fcs1 = pd.read_csv(one / "eval" / "out" / SEASON / "forecasts.csv")
    assert {"acme-small", HOUSE} <= set(fcs1.model_id)
    assert "hub-ensemble" not in set(fcs1.model_id)


def test_imputation_covers_ranked_core_but_never_world(work: Path):
    mark_world(work, ["senate-ak"])
    f = work / "model-output" / "acme-small" / f"{SEASON}-2026-10-26.csv"
    df = pd.read_csv(f)
    df[~df.question_id.isin(["senate-ak", "senate-ga"])].to_csv(f, index=False)
    score(work)
    scores = pd.read_csv(work / "eval" / "out" / SEASON / "scores.csv")
    mine = scores[(scores.model_id == "acme-small") & (scores.day == "2026-10-26")]
    assert "senate-ak" not in set(mine.question_id)  # world-track gap is not imputed
    row = mine[mine.question_id == "senate-ga"]
    assert len(row) == 1 and bool(row.imputed.iloc[0])  # ranked core gap still is
