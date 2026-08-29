"""verify(task, answer) -> score in {0.0, 1.0}. Deterministic; no model as judge."""

from __future__ import annotations

import re


def _matmul(A, B, p):
    n = len(A)
    return [[sum(A[i][k] * B[k][j] for k in range(n)) % p for j in range(n)] for i in range(n)]


def _matpow(M, e, p):
    n = len(M)
    R = [[int(i == j) for j in range(n)] for i in range(n)]
    while e:
        if e & 1:
            R = _matmul(R, M, p)
        M = _matmul(M, M, p)
        e >>= 1
    return R


def solve(task: dict) -> int:
    c, a0, n, p = (task["params"][k] for k in ("coeffs", "init", "n", "p"))
    k = len(c)
    if n < k:
        return a0[n] % p
    # companion matrix: state [a[n-1], ..., a[n-k]]
    M = [[0] * k for _ in range(k)]
    M[0] = [x % p for x in c]
    for i in range(1, k):
        M[i][i - 1] = 1
    Mp = _matpow(M, n - k + 1, p)
    state = [a0[k - 1 - i] % p for i in range(k)]  # [a[k-1], ..., a[0]]
    return sum(Mp[0][j] * state[j] for j in range(k)) % p


def parse(answer: str) -> int | None:
    m = re.findall(r"-?\d+", answer or "")
    return int(m[-1]) if m else None


def verify(task: dict, answer: str) -> float:
    got = parse(answer)
    return 1.0 if got is not None and got == solve(task) else 0.0
