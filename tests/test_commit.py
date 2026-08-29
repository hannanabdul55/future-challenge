from fc import commit as C


def test_commit_roundtrip():
    n = C.new_nonce()
    c = C.commitment(b"sub", b"rat", n)
    assert C.verify_reveal(c, b"sub", b"rat", n)
    assert not C.verify_reveal(c, b"sub!", b"rat", n)
    assert not C.verify_reveal(c, b"sub", b"rat", C.new_nonce())


def test_merkle_proofs():
    commits = [C.commitment(bytes([i]), b"", bytes(32)) for i in range(7)]
    root = C.merkle_root(commits)
    for i in range(len(commits)):
        proof = C.merkle_proof(commits, i)
        assert C.merkle_verify(commits[i], proof, root)
    bad = C.commitment(b"x", b"", bytes(32))
    assert not C.merkle_verify(bad, C.merkle_proof(commits, 0), root)
    assert C.merkle_root(commits[:1]) != C.merkle_root(commits)


def test_receipt_signatures():
    tpriv, tpub = C.generate_keypair()
    hpriv, hpub = C.generate_keypair()
    tsk, tpk = C.load_private(tpriv), C.load_public(tpub)
    hsk, hpk = C.load_private(hpriv), C.load_public(hpub)
    c = C.commitment(b"s", b"", bytes(32))
    req = C.team_sign_request(tsk, "2026-w37", "acme-v3", c)
    r = C.issue_receipt(hsk, req, tpk, position=3)
    assert C.verify_receipt(hpk, r)
    r.commit = "00" * 32
    assert not C.verify_receipt(hpk, r)
    # wrong team key rejected
    other = C.load_public(C.generate_keypair()[1])
    try:
        C.issue_receipt(hsk, req, other, 0)
        assert False
    except ValueError:
        pass
