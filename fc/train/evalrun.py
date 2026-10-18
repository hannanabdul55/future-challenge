"""Answer one historical day through the real runner script (no round manifest, no repo writes).

``run_day`` builds the same symlink tree as ``fc.run._run_local`` (model, packet, questions,
prompt, out), applies the same five ``Path(...)`` string replacements to
``runner/forecast/forecast.py`` and runs the result under the eval venv with ``python -I``.
The runner script is used verbatim apart from those five fixed paths (T-04.1-19).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import paths

DEFAULT_PYTHON = paths.VENV_EVAL / "bin" / "python"
DEFAULT_SCRIPT = paths.REPO_ROOT / "runner" / "forecast" / "forecast.py"
PROMPT_DIR = paths.REPO_ROOT / "fc" / "prompts"
PROMPT_NAME = "forecaster-v1.md"

# D-02 / registration.json "inference": the registered runner settings.
RUNNER_ENV = {"FC_SEED": "0", "FC_CTX": "16384", "FC_MAX_TOKENS": "4096",
              "FC_BATCH": "20", "FC_THINKING": "0"}


def rewrite_script(code: str, tree: Path, prompt_name: str) -> str:
    """The five path replacements of fc.run._run_local, character for character."""
    return (code
            .replace('Path("/model")', f'Path("{tree}/model")')
            .replace('Path("/packet/packet.md")', f'Path("{tree}/packet/packet.md")')
            .replace('Path("/questions/questions.json")',
                     f'Path("{tree}/questions/questions.json")')
            .replace('Path("/prompt/template.md")', f'Path("{tree}/prompt/{prompt_name}")')
            .replace('Path("/out")', f'Path("{tree}/out")'))


def run_day(gguf: str | Path, packet_dir: str | Path, questions_json: str | Path,
            out_dir: str | Path, *, day: str, round_id: str,
            python: str | Path = DEFAULT_PYTHON, script: str | Path = DEFAULT_SCRIPT,
            tmp_root: str | Path = paths.TMP, timeout_s: int = 3600,
            prompt_dir: str | Path = PROMPT_DIR, prompt_name: str = PROMPT_NAME,
            ) -> dict[str, Any]:
    """Run the runner on one day; returns its status.json plus ``wall_s`` and ``returncode``."""
    gguf, packet_dir = Path(gguf), Path(packet_dir)
    questions_json, out_dir = Path(questions_json), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tmp_root)
    tmp_root.mkdir(parents=True, exist_ok=True)
    tree = Path(tempfile.mkdtemp(prefix="fc-eval-", dir=tmp_root))
    for name, target in (("model", gguf.parent), ("packet", packet_dir),
                         ("questions", questions_json.parent), ("prompt", Path(prompt_dir))):
        (tree / name).symlink_to(target.resolve())
    (tree / "out").symlink_to(out_dir.resolve())
    code = rewrite_script(Path(script).read_text(), tree, prompt_name)
    local_script = tree / "forecast_local.py"
    local_script.write_text(code)
    env = {**os.environ, **RUNNER_ENV, "FC_ROUND_ID": round_id, "FC_DAY": day,
           "FC_WEIGHTS_FILE": gguf.name}
    t0 = time.time()
    proc = subprocess.run([str(python), "-I", str(local_script)], env=env, timeout=timeout_s,
                          capture_output=True, text=True, check=False)
    wall = round(time.time() - t0, 1)
    (out_dir / "local.log").write_text(proc.stdout + proc.stderr)
    status_path = out_dir / "status.json"
    status: dict[str, Any] = json.loads(status_path.read_text()) if status_path.exists() else {}
    status["wall_s"] = wall
    status["returncode"] = proc.returncode
    # the runner's own status.json is evidence and stays as written; timing goes beside it
    (out_dir / "run_day.json").write_text(
        json.dumps({"wall_s": wall, "returncode": proc.returncode}) + "\n")
    return status


def assert_chat_mode(status: dict[str, Any]) -> None:
    """Every batch must have run on the plain chat path with no think prefill."""
    batches = status.get("batches") or []
    if not batches:
        raise AssertionError("status has no batches")
    bad = [b for b in batches if b.get("mode") != "chat" or b.get("think_prefill") is not False]
    if bad:
        raise AssertionError(f"non-chat batches: {bad}")
