import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator import gen
from verifier import solve, verify


def brute(task):
    c, a, n, p = (task["params"][k] for k in ("coeffs", "init", "n", "p"))
    seq = list(a)
    for i in range(len(a), n + 1):
        seq.append(sum(c[j] * seq[i - 1 - j] for j in range(len(c))) % p)
    return seq[n] % p


def test_deterministic():
    assert gen(12345) == gen(12345)
    assert gen(1)["prompt"] != gen(2)["prompt"]


def test_solver_matches_bruteforce_small():
    for seed in range(20):
        t = gen(seed, difficulty=0)
        assert solve(t) == brute(t)


def test_verifier():
    t = gen(99)
    ans = solve(t)
    assert verify(t, f"The answer is {ans}.") == 1.0
    assert verify(t, str(ans + 1)) == 0.0
    assert verify(t, "") == 0.0
