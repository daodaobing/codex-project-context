"""Build isolated per-run workspaces for Pilot V1.

For each (task, arm) pair, create a fresh clone of the pinned repository under
``benchmarks/results/pilot-runs/<task_id>/<arm>/`` so all three arms of a task
start from an identical commit with no cross-run mutation.

Usage:
    python benchmarks/value_pilot/build_suite.py [--tasks t1-1-click-confirm] [--arm A] [--skip-existing]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "benchmarks" / "workspaces"
OUT = ROOT / "benchmarks" / "results" / "pilot-runs"
TASKS_PATH = ROOT / "benchmarks" / "value_pilot" / "tasks.json"


def _rmtree_force(path: Path) -> None:
    """Remove a tree even when Windows git clone propagated read-only attrs."""

    def _onerror(func, target, exc_info):  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    shutil.rmtree(path, onerror=_onerror)


def load_tasks() -> list[dict]:
    with TASKS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["tasks"]


def clone_repo(repo: str, commit: str, target: Path) -> None:
    src = WS / repo
    if not (src / ".git").is_dir():
        raise SystemExit(f"workspace repo missing: {src}")
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(src), str(target)],
        check=True,
    )
    got = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if got[:7] != commit[:7]:
        raise SystemExit(f"commit mismatch for {repo}: expected {commit}, got {got}")
    # Remove any workspace residue that could leak prior-run state.
    for junk in (".pytest_cache", "__pycache__"):
        p = target / junk
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="*", default=None)
    parser.add_argument("--arms", nargs="*", default=["A", "B", "C"])
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    tasks = load_tasks()
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [t for t in tasks if t["task_id"] in wanted]

    built = 0
    for task in tasks:
        repo, commit, task_id = task["repository"], task["commit"], task["task_id"]
        for arm in args.arms:
            target = OUT / task_id / arm
            if args.skip_existing and target.exists():
                continue
            if target.exists():
                _rmtree_force(target)
            clone_repo(repo, commit, target)
            # Git clone on Windows can propagate read-only attrs from the source
            # workspace; clear them so the run tree stays removable later.
            for dirpath, dirnames, filenames in os.walk(target):
                for name in dirnames + filenames:
                    p = Path(dirpath) / name
                    try:
                        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                    except OSError:
                        pass
            built += 1
            print(f"built {task_id}/{arm} at {target}")
    print(f"built {built} workspaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
