"""Task family: linear-recurrence (paradigm G example).

gen(seed) -> task dict. Deterministic. Instance space ~2^120 (coefficients, initial
values, N, modulus). Ground truth requires fast matrix exponentiation; brute force over
N ~ 1e12 is infeasible, so the verifier computes it exactly with O(log N) arithmetic.
"""

from __future__ import annotations

import random

FAMILY_ID = "linear-recurrence@0.1.0"

# a few large primes for the modulus
_PRIMES = [1_000_000_007, 998_244_353, 1_000_000_009, 2_147_483_647, 4_294_967_291]


def gen(seed: int, difficulty: int = 2) -> dict:
    rng = random.Random(seed)
    order = min(2 + difficulty // 2, 4)
    coeffs = [rng.randint(-50, 50) or 1 for _ in range(order)]
    init = [rng.randint(-100, 100) for _ in range(order)]
    n = rng.randint(10**9, 10**12) if difficulty >= 2 else rng.randint(10**4, 10**6)
    p = rng.choice(_PRIMES)
    rec = " + ".join(f"({c})*a[n-{i+1}]" for i, c in enumerate(coeffs))
    inits = ", ".join(f"a[{i}] = {v}" for i, v in enumerate(init))
    prompt = (
        f"Define the integer sequence a[n] for n >= {order} by\n"
        f"  a[n] = {rec}\n"
        f"with {inits}.\n"
        f"Compute a[{n}] mod {p}. Give the answer as a single non-negative integer in [0, {p})."
    )
    return {
        "family_id": FAMILY_ID,
        "seed": seed,
        "difficulty": difficulty,
        "params": {"coeffs": coeffs, "init": init, "n": n, "p": p},
        "prompt": prompt,
        "answer_format": "integer",
    }
