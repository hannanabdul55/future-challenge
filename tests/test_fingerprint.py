from fc import fingerprint as F


def test_capture_and_drift():
    probes = F.load_panel()
    assert len(probes) >= 10
    before = F.capture(lambda p: p.upper(), probes)
    same = F.capture(lambda p: p.upper(), probes)
    assert F.drift(before, same)["changed_fraction"] == 0.0
    assert not F.changed(before, same)
    after = F.capture(lambda p: p.upper() + ("!" if len(p) % 2 else ""), probes)
    d = F.drift(before, after)
    assert 0 < d["changed_fraction"] <= 1 and d["mean_edit_distance"] > 0


def test_roundtrip_json():
    probes = F.load_panel()
    fp = F.capture(lambda p: "x", probes)
    assert F.Fingerprint.from_json(fp.to_json()) == fp
