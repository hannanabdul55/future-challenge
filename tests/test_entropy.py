from datetime import UTC, datetime

from fc import entropy as E


def test_secret_commit_reveal():
    S = E.new_hub_secret()
    h = E.secret_commitment(S)
    assert E.verify_secret(S, h)
    assert not E.verify_secret(E.new_hub_secret(), h)


def test_seed_derivation_deterministic():
    beta = "ab" * 32
    S = bytes(range(32))
    e = E.combined_entropy(beta, S)
    assert E.derive_seed(e, "fam@1", 0) == E.derive_seed(e, "fam@1", 0)
    assert E.derive_seed(e, "fam@1", 0) != E.derive_seed(e, "fam@1", 1)
    assert E.derive_seed(e, "fam@1", 0) != E.derive_seed(e, "fam@2", 0)


def test_round_after_fires_after_time():
    t = datetime(2026, 9, 9, 23, 59, 59, tzinfo=UTC)
    r = E.round_after(t, margin_s=30)
    assert E.round_time(r) >= t
    assert E.round_time(r - 1) < E.round_time(r)


def test_entropy_record_rejects_bad_secret():
    S = E.new_hub_secret()
    d = E.DrandRound(E.QUICKNET_CHAIN, 1, "00" * 32, "")
    try:
        E.entropy_record("r", E.secret_commitment(E.new_hub_secret()), S, d, {})
        assert False
    except ValueError:
        pass
    rec = E.entropy_record("r", E.secret_commitment(S), S, d, {"f": 3})
    assert rec["E"] is not None and rec["families"]["f"]["n"] == 3
