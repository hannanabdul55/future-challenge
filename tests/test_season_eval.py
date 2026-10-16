"""SCORE-03/05, BOARD-01/02: the whole season loop runs offline end to end — freeze, daily
rounds, forecasts, truth, scores, relative skill per tier, board HTML."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import demo_season

from fc import questions as Q


def test_demo_season_end_to_end(tmp_path: Path):
    demo_season.main(tmp_path, 3)
    out = tmp_path / "eval" / "out" / Q.SEASON
    board = json.loads((out / "board.json").read_text())
    assert board["days"] == ["2026-10-26", "2026-10-27", "2026-10-28"]
    assert board["n_resolved"] == 75
    rs = pd.DataFrame(board["relative_skill"])
    assert set(rs[rs.tier == "S"].model_id) == {"acme-small", "acme-sharp", "zed-noisy", "fc-baseline"}
    assert (rs[rs.tier == "H"].model_id.isin(["fc-baseline", "fc-uniform", "hub-ensemble"])).all()
    assert rs[rs.model_id == "fc-uniform"].rel_skill.iloc[0] > 1  # ignorance loses
    assert (rs.n_days == 3).all() and not rs.provisional.any()  # min_days=2 in the demo
    scores = pd.read_csv(out / "scores.csv")
    assert set(scores.output_type) == {"binary", "categorical", "quantile"}
    assert not scores.imputed.any()  # every entry answered everything
    traj = pd.read_csv(out / "trajectories.csv")
    assert traj.p_house_d.between(0, 1).all()
    html = (tmp_path / "leaderboard" / f"{Q.SEASON}.html").read_text()
    assert "Tier S" in html and "<svg" in html and "AI model outputs" in html


def test_unadmitted_days_are_dropped(tmp_path: Path):
    """An entry frozen after a round's T_open is not scored on that round (FREEZE-05)."""
    from fc import season_eval
    demo_season.main(tmp_path, 2)
    meta = tmp_path / "model-metadata" / "zed-noisy.yml"
    import yaml
    doc = yaml.safe_load(meta.read_text())
    sc = tmp_path / doc["freeze"]["sidecar"]
    rec = json.loads(sc.read_text())
    rec["hub_receipt"]["server_time"] = "2026-10-26T06:00:00.000Z"  # after day-1 T_open
    sc.write_text(json.dumps(rec))
    board = season_eval.run(tmp_path, Q.SEASON, n_boot=10, min_days=2)
    assert board["entries"]["zed-noisy"]["days"] == ["2026-10-27"]


def test_missing_core_questions_are_imputed(tmp_path: Path):
    from fc import season_eval
    demo_season.main(tmp_path, 2)
    f = tmp_path / "model-output" / "acme-small" / f"{Q.SEASON}-2026-10-26.csv"
    df = pd.read_csv(f)
    df = df[df.question_id != "senate-ga"]
    df.to_csv(f, index=False)
    season_eval.run(tmp_path, Q.SEASON, n_boot=10, min_days=2)
    scores = pd.read_csv(tmp_path / "eval" / "out" / Q.SEASON / "scores.csv")
    row = scores[(scores.model_id == "acme-small") & (scores.day == "2026-10-26")
                 & (scores.question_id == "senate-ga")]
    assert len(row) == 1 and bool(row.imputed.iloc[0])
    day = scores[(scores.day == "2026-10-26") & (scores.question_id == "senate-ga")
                 & ~scores.imputed]
    assert row.score.iloc[0] >= day.score.quantile(0.75) - 1e-12
