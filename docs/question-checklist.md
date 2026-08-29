# Question checklist (editors)

Every question ships with a machine-readable spec (see `docs/question-spec.example.yaml`). Before it ships:

- [ ] Resolution source is independent of every plausible participant and of the hub.
- [ ] `expected_resolution_date > T_commit + 24 h`; no scheduled pre-announcement exists.
- [ ] Resolver ran mechanically on ≥ 12 months of history with no manual intervention.
- [ ] Vintage rule and fallback hierarchy written.
- [ ] Void conditions enumerated.
- [ ] Manipulation cost estimated and recorded; target labelled if below "high".
- [ ] Not about an identifiable individual; not retail-tradable.
- [ ] Baseline defined and computable at `T_open`.
- [ ] Stranger test: two editors resolved a synthetic past instance independently and agreed.
- [ ] Author is not participating in the round.
