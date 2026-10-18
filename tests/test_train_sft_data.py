"""The tracer's example contract, offline: a tiny cycle goes from races CSV through questions,
the live renderer on fake point-in-time revisions, and a runner-identical SFT example."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from fc import forecast_io as F
from fc import packet as P
from fc import questions as Q
from fc.train import history, packets, pollmodel, sft_data, wiki_asof

CYCLE = history.CYCLES[2022]
DAY = date(2022, 10, 31)
RACES = """office,state,state_name,seat,incumbent_party,open_seat,pres_2020
senate,GA,Georgia,class3,D,false,D
senate,CA,California,class3,D,false,D
senate,CA,California,special,D,false,D
governor,AZ,Arizona,,R,true,D
"""


def poll_table(heading_extra="", d="49%", r="45%"):
    return f"""
== General election ==
=== Polling ===
{{| class="wikitable"
! Poll source !! Date !! Sample !! Ossoff (D) !! Walker (R){heading_extra}
|-
| Emerson || Oct 1 || 800 || {d} || {r}
|-
| Quinnipiac || Sep 20 || 1,100 || 47% || 48%
|}}
"""


class Wiki:
    def __init__(self, texts):
        self.texts = texts
        self.ids = {t: 1000 + i for i, t in enumerate(texts)}

    def __call__(self, params):
        if params["action"] == "query":
            t = params["titles"]
            if t not in self.texts:
                return json.dumps({"query": {"pages": [{"title": t, "missing": True}]}})
            return json.dumps({"query": {"pages": [{"title": t, "revisions": [
                {"revid": self.ids[t], "timestamp": "2022-10-20T00:00:00Z"}]}]}})
        title = next(t for t, i in self.ids.items() if i == params["oldid"])
        return json.dumps({"parse": {"title": title, "revid": params["oldid"],
                                     "wikitext": self.texts[title]}})


@pytest.fixture
def world(tmp_path):
    races = tmp_path / "races.csv"
    races.write_text(RACES)
    qs = history.build_questions(races, CYCLE)
    texts = {
        "2022 United States Senate election in Georgia": poll_table(),
        "2022 United States Senate election in California": poll_table(d="58%", r="36%"),
        "2022 Arizona gubernatorial election": poll_table(),
        "2022 United States Senate elections": poll_table(),
    }
    cache = wiki_asof.WikiCache(tmp_path / "wiki", fetch=Wiki(texts), pause_s=0,
                                sleep=lambda s: None)
    return qs, cache, tmp_path


def test_question_set_uses_cycle_dates_ids_and_titles(world):
    qs, _, _ = world
    by = {q["question_id"]: q for q in qs}
    assert {"senate-ga", "senate-ca", "senate-ca-special", "governor-az"} <= set(by)
    assert "November 8, 2022" in by["senate-ga"]["statement"]
    assert "118th Congress" in by["control-house"]["statement"]
    assert by["control-house"]["resolution"]["final_rule"] == (
        "membership_at_seating_of_the_118th_congress_2023-01-03")
    assert by["senate-ga"]["pres_2020"] == "D" and by["senate-ga"]["event_date"] == "2022-11-08"
    assert history.race_title(by["senate-ca-special"], 2022) == (
        "2022 United States Senate special election in California")
    assert history.race_title(by["governor-az"], 2022) == "2022 Arizona gubernatorial election"
    assert CYCLE.days()[0].isoformat() == "2022-10-31"
    assert len(CYCLE.days()) == 9


def test_live_2026_set_is_reproduced(tmp_path):
    root = Path(__file__).resolve().parents[1]
    live = root / "questions" / "2026-us-midterms" / "races.csv"
    mine = history.build_questions(live, history.CYCLES[2026])
    assert Q.questions_hash(mine) == Q.questions_hash(Q.generate_midterms(live))
    for q in Q.generate_midterms(live):
        assert history.race_title(q, 2026) == P.race_page_title(q)


def build(world):
    qs, cache, tmp = world
    out = tmp / "out"
    qpath = out / "questions.json"
    history.write_questions(qpath, qs, CYCLE)
    loaded = Q.load_questions(qpath)
    index = packets.build_day(CYCLE, DAY, loaded, out / "packet", cache)
    md = (out / "packet" / "packet.md").read_text()
    return loaded, index, md


def senate_example(world):
    loaded, index, md = build(world)
    batch = next(b for b in F.batches(loaded, max_per_batch=20)
                 if b[0]["family"] == "senate")
    answers = {}
    for q in batch:
        feat = pollmodel.extract_features(md, q["question_id"])
        answers[q["question_id"]] = sft_data.round_probs(
            pollmodel.race_probs(feat, pollmodel.DEFAULT_PARAMS, office="senate"))
    ex = sft_data.build_example(cycle=CYCLE, day=DAY, packet_md=md, batch=batch, batch_index=0,
                                answers=answers, masked_ids=[], template=F.load_template())
    return ex, batch, md, index


def test_packet_marks_gdelt_unavailable_and_keeps_the_live_section_layout(world):
    _, index, md = build(world)
    assert index["sections"] == {"wikipedia_polls": "ok", "prediction_markets": "unavailable",
                                 "gdelt_headlines": "unavailable"}
    assert ("## Headlines\n\n_unavailable: historical packet: the GDELT DOC API reaches back "
            "only about 3 months_") in md
    assert "_unavailable: no market mapping configured_" in md
    assert "Ossoff (D)" in md and "### senate-ga" in md


def test_prompt_equals_the_runner_prompt_and_completion_parses(world):
    ex, batch, md, _ = senate_example(world)
    assert ex["prompt"] == F.build_prompt(F.load_template(), packet_md=md, questions=batch,
                                          day="2022-10-31")
    sft_data.check_example(ex, batch, "r")
    parsed = F.parse_answer(ex["completion"], batch, round_id="r")
    assert parsed.parse_rate == 1.0 and not parsed.missing
    assert ex["id"] == "2022-2022-10-31-senate-0"
    # the mildly D-leaning GA table gives a probability strictly between 0 and 1
    ga = {r["output_type_id"]: r["value"] for r in parsed.rows if r["question_id"] == "senate-ga"}
    assert 0 < ga["R"] < ga["D"] < 1 and ga["Other"] == 0.01


def test_features_read_the_rendered_rows(world):
    _, _, md = build(world)
    f = pollmodel.extract_features(md, "senate-ca")
    assert f.n_rows == 2 and f.layout == "polls" and not f.unpolled
    assert f.m_bar == pytest.approx(((58 - 36) + (47 - 48)) / 2)
    assert pollmodel.extract_features(md, "senate-nowhere").unpolled


def test_unpolled_race_gets_the_packet_only_prior(world):
    feat = pollmodel.Features("x", 0, 0.0, "none", True, False)
    p = pollmodel.race_probs(feat, pollmodel.DEFAULT_PARAMS, office="governor")
    assert p == pytest.approx({"D": 0.495, "R": 0.495, "Other": 0.01}, abs=0.005)
    with pytest.raises(ValueError):
        pollmodel.race_probs(feat, pollmodel.DEFAULT_PARAMS, office="senate", prior_mode="x")


def test_control_batch_masks_house_answers(world):
    loaded, _, md = build(world)
    batch = next(b for b in F.batches(loaded, max_per_batch=20)
                 if b[0]["family"] == "control")
    answers = {"control-house": sft_data.NEUTRAL_HOUSE["control-house"],
               "control-senate": {"p": 0.55}}
    ex = sft_data.build_example(cycle=CYCLE, day=DAY, packet_md=md, batch=batch, batch_index=0,
                                answers=answers, masked_ids=[], template=F.load_template())
    assert ex["masked_ids"] == ["control-house"]
    a, b = ex["mask_spans"][0]
    assert json.loads(ex["completion"][a:b]) == {"p": 0.5}
    sft_data.check_example(ex, batch, "r")
    ex["masked_ids"], ex["mask_spans"] = [], []
    with pytest.raises(ValueError, match="D-13"):
        sft_data.check_example(ex, batch, "r")


def test_seats_batch_masks_house_quantiles_and_neutral_values_are_monotone():
    seats = sft_data.NEUTRAL_HOUSE["seats-house-d"]
    vals = list(seats.values())
    assert list(seats) == [str(x) for x in Q.SEAT_LEVELS] and vals == sorted(vals)
    assert seats["0.5"] == 218 and all(isinstance(v, int) for v in vals)


def test_check_example_rejects_raw_zero_one_and_parse_failures(world):
    ex, batch, _, _ = senate_example(world)
    bad = dict(ex, completion=ex["completion"].replace('"D": 0.', '"D": 1.0, "x": 0.', 1))
    with pytest.raises(ValueError):
        sft_data.check_example(bad, batch, "r")
    with pytest.raises(ValueError, match="missed"):
        sft_data.check_example(dict(ex, completion="no json"), batch, "r")


@pytest.mark.parametrize("raw", [
    {"D": 0.999, "R": 0.0004, "Other": 0.0006},
    {"D": 0.0, "R": 0.0, "Other": 1.0},
    {"D": 0.333, "R": 0.333, "Other": 0.334},
    {"D": 0.495, "R": 0.495, "Other": 0.01},
    {"D": 0.004, "R": 0.99, "Other": 0.006},
])
def test_round_probs_sums_to_exactly_one_with_every_class_at_least_a_cent(raw):
    out = sft_data.round_probs(raw)
    cents = [round(v * 100) for v in out.values()]
    assert sum(cents) == 100 and min(cents) >= 1
    assert all(v == round(v, 2) for v in out.values())


def test_completion_text_spans_cover_each_value_object():
    batch = [{"question_id": "a"}, {"question_id": "b"}]
    text, spans = sft_data.completion_text(batch, {"a": {"p": 0.25}, "b": {"D": 0.5, "R": 0.5}})
    assert text == '{"a": {"p": 0.25}, "b": {"D": 0.5, "R": 0.5}}'
    assert text[slice(*spans["b"])] == '{"D": 0.5, "R": 0.5}'


class CharTokenizer:
    """Duck-typed stub: one token per character, ids are code points, offsets are (i, i+1)."""

    def apply_chat_template(self, messages, *, add_generation_prompt, tokenize):
        assert add_generation_prompt is True and tokenize is False
        assert [m["role"] for m in messages] == ["user"]
        return "<u>" + messages[0]["content"] + "<a>"

    def __call__(self, text, *, add_special_tokens, return_offsets_mapping=False):
        assert add_special_tokens is False
        out = {"input_ids": [ord(c) for c in text]}
        if return_offsets_mapping:
            out["offset_mapping"] = [(i, i + 1) for i in range(len(text))]
        return out


def _ex(completion, spans):
    return {"id": "x", "prompt": "hello", "completion": completion, "mask_spans": spans}


def test_tokenize_example_labels_only_the_unmasked_completion_and_eos():
    comp = '{"a": {"p": 0.4}, "b": {"p": 0.5}}'
    start = comp.index('{"p": 0.5}')
    span = [start, start + len('{"p": 0.5}')]
    tok = sft_data.tokenize_example(_ex(comp, [span]), CharTokenizer())
    n_prompt = len("<u>hello<a>")
    assert tok["n_prompt"] == n_prompt
    assert all(x == -100 for x in tok["labels"][:n_prompt])  # prompt labels
    body = tok["labels"][n_prompt:-1]
    assert len(body) == len(comp)
    assert all(lab == -100 for lab in body[span[0]:span[1]])  # masked characters
    assert all(lab == ord(c) for lab, c in zip(body[:span[0]], comp[:span[0]]))
    assert tok["labels"][-1] == 100257 and tok["input_ids"][-1] == 100257
    unmasked = len(comp) - (span[1] - span[0])
    assert tok["n_label"] == unmasked + 1 == sum(1 for x in tok["labels"] if x != -100)
    assert tok["n_completion"] == len(comp) + 1
    assert len(tok["input_ids"]) == len(tok["labels"]) == len(tok["attention_mask"])
    assert set(tok["attention_mask"]) == {1}


def test_tokenize_example_without_spans_labels_everything_in_the_completion():
    tok = sft_data.tokenize_example(_ex('{"a": 1}', []), CharTokenizer())
    assert tok["n_label"] == len('{"a": 1}') + 1


def test_tokenize_example_prompt_ids_come_from_the_template_render_alone():
    tok = sft_data.tokenize_example(_ex("{}", []), CharTokenizer())
    assert tok["input_ids"][:tok["n_prompt"]] == [ord(c) for c in "<u>hello<a>"]


def test_tokenize_example_rejects_a_fully_masked_example_that_has_no_labels():
    class NoEos(CharTokenizer):
        pass

    # eos is always labelled, so n_label can never be 0 through spans alone: an empty
    # completion still carries the eos label.
    tok = sft_data.tokenize_example(_ex("", []), NoEos())
    assert tok["n_label"] == 1
