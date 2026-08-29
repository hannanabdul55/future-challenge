# linear-recurrence@0.1.0

**What it tests.** Exact modular arithmetic on a linear recurrence at N ≈ 10^9–10^12. Requires recognizing that fast matrix exponentiation (or an equivalent doubling method) is needed; brute force is infeasible.

**Generator.** `gen(seed, difficulty)`; order 2–4 recurrences, coefficients in [−50, 50], initial values in [−100, 100], modulus one of five large primes. Instance space: ≈ 101^4 · 201^4 · 10^12 · 5 ≈ 2^120.

**Verifier.** `verify(task, answer)` → {0, 1}; parses the last integer in the answer; exact comparison to `solve(task)`.

**Novelty check.** Instances are numeric; nearest-neighbour check against public corpora is a no-op for this family (no natural-language content to collide). Difficulty 0 instances (N ≤ 10^6) are brute-forceable and used only in tests and human baselines.

**Human baseline.** Not yet collected.

**Known weaknesses.** A model with a code tool solves this trivially; the family is a scaffold example of the generator/verifier contract, not a frontier task. Replace or extend before Phase 1.
