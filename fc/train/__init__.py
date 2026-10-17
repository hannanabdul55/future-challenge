"""Phase 04.1 data and training helpers for the operator-trained OLMo 3 entry.

The package is import-light: only the standard library, numpy, pandas and pyyaml are imported
at module level. torch, transformers, peft and llama_cpp are imported only inside functions
that need them, so ``import fc.train`` works in the evaluator image. Training dependencies
never enter pyproject.toml (D-11); they live in venvs under /mnt/d/fc-train.
"""
