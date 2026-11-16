"""T-06-38: the house- prefix is reserved for operator entries in registration CI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows" / "register.yml"


def gate_step() -> dict:
    doc = yaml.safe_load(WF.read_text())  # PyYAML parses the bare key `on` as True
    steps = [s for job in doc["jobs"].values() for s in job["steps"]]
    found = [s for s in steps if "house-" in s.get("run", "")]
    assert len(found) == 1, "exactly one step reserves the house- prefix"
    return found[0]


def test_house_prefix_step_comes_before_the_entry_check():
    doc = yaml.safe_load(WF.read_text())
    names = [s.get("name") for job in doc["jobs"].values() for s in job["steps"]]
    assert names.index(gate_step()["name"]) < names.index("Check changed entries")


def test_house_prefix_step_takes_event_data_from_env_only():
    step = gate_step()
    assert "AUTHOR_ASSOCIATION" in step["env"]
    assert "author_association" in step["env"]["AUTHOR_ASSOCIATION"]
    assert "BASE_REF" in step["env"]
    assert "${{" not in step["run"]  # no expression interpolation in the script body
    assert "$AUTHOR_ASSOCIATION" in step["run"]


def test_house_prefix_step_script_gates_by_association(tmp_path: Path):
    """Run the real script against a throwaway repo: non-owners fail, owners pass."""
    script = gate_step()["run"]
    repo = tmp_path / "r"
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    (repo / "model-metadata").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "model-metadata" / "acme-a.yml").write_text("model_id: acme-a\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)

    def run(assoc: str) -> subprocess.CompletedProcess:
        env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
               "AUTHOR_ASSOCIATION": assoc, "BASE_REF": "main", "HOME": str(tmp_path)}
        return subprocess.run(["bash", "-c", script], cwd=repo, env=env, text=True,
                              capture_output=True, check=False)

    # an ordinary entry: everyone passes
    (repo / "model-metadata" / "acme-b.yml").write_text("model_id: acme-b\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "ordinary"], check=True)
    assert run("NONE").returncode == 0

    # a house entry: only the project may add it
    (repo / "model-metadata" / "house-x.yml").write_text("model_id: house-x\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "house"], check=True)
    for assoc in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", ""):
        r = run(assoc)
        assert r.returncode == 1, assoc
        assert "reserved for operator entries" in r.stdout
    for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert run(assoc).returncode == 0, assoc
