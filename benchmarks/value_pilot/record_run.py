"""Record per-run metadata + token telemetry for Pilot V1 CLI runs.

Aggregates usage.jsonl (opencodex proxy) over the run's wall-clock window.
The window is exclusive because runs execute serially and no other model
request should overlap; residual overlap is noted, not corrected.

Usage:
    python benchmarks/value_pilot/record_run.py <task_id> <arm> \
        --session <session_id> --start <iso> --end <iso> \
        [--exit <code>] [--wall <sec>] [--oracle PASS|FAIL] [--success PASS|FAIL] \
        [--constraint PASS|FAIL|N/A] [--wrong-direction] [--rework]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmarks" / "results" / "pilot-runs"
USAGE = Path(os.environ.get("OPENCODEX_USAGE_FILE", "~/.opencodex/usage.jsonl")).expanduser()
PROTOCOL_PATH = ROOT / "benchmarks" / "value_pilot" / "protocol.json"
TASKS_PATH = ROOT / "benchmarks" / "value_pilot" / "tasks.json"


def iso_to_ms(iso: str) -> int:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def aggregate_usage(start_ms: int, end_ms: int) -> dict:
    agg = {
        "requests": 0,
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 0,
        "total": 0,
        "status200": 0,
        "status_non200": 0,
        "window": [start_ms, end_ms],
    }
    if not USAGE.exists():
        agg["unavailable"] = "usage.jsonl missing"
        return agg
    for line in USAGE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get("timestamp")
        if not isinstance(ts, int):
            continue
        if not (start_ms <= ts <= end_ms):
            continue
        agg["requests"] += 1
        if rec.get("status") == 200:
            agg["status200"] += 1
        else:
            agg["status_non200"] += 1
        u = rec.get("usage") or {}
        agg["input"] += u.get("inputTokens", 0)
        agg["output"] += u.get("outputTokens", 0)
        agg["cache_read"] += u.get("cacheReadInputTokens", 0)
        agg["cache_creation"] += u.get("cachedInputTokens", 0)
        agg["reasoning"] += u.get("reasoningOutputTokens", 0)
        agg["total"] += u.get("totalTokens", 0)
    # Provider usage fields have different semantics (some providers count
    # cache-read inside inputTokens). Report uncached conservatively and keep
    # cache_creation separate; never invent a negative number.
    agg["uncached_input"] = max(0, agg["input"] - agg["cache_read"])
    return agg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_id")
    parser.add_argument("arm")
    parser.add_argument("--session", default=None)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--exit", type=int, default=None)
    parser.add_argument("--wall", type=float, default=None)
    parser.add_argument("--oracle", default=None)
    parser.add_argument("--success", default=None)
    parser.add_argument("--constraint", default=None)
    parser.add_argument("--wrong-direction", action="store_true")
    parser.add_argument("--rework", action="store_true")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    run_dir = OUT / args.task_id / args.arm
    prompt_path = run_dir / "prompt.txt"
    prompt_hash = hashlib.sha256(
        prompt_path.read_bytes()
    ).hexdigest().upper() if prompt_path.exists() else None

    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    task = next(t for t in tasks["tasks"] if t["task_id"] == args.task_id)

    usage = aggregate_usage(iso_to_ms(args.start), iso_to_ms(args.end))
    meta = {
        "run_id": f"{args.task_id}/{args.arm}",
        "carrier": "codex-cli-exec",
        "codex_version": "0.144.6",
        "model": "gpt-5.6-luna",
        "provider": "openai",
        "reasoning_effort": "max",
        "approval": "never",
        "sandbox": "danger-full-access",
        "permission_profile": "disabled/unrestricted",
        "session_id": args.session,
        "start": args.start,
        "end": args.end,
        "wall_sec": args.wall,
        "exit_status": args.exit,
        "workspace": str(run_dir),
        "repo_commit": task["commit"],
        "task_hash": protocol["frozen_hashes"]["tasks_json"],
        "protocol_hash": protocol["frozen_hashes"]["protocol_json"],
        "prompt_hash": prompt_hash,
        "transcript": {
            "run_log": str(run_dir / "run.log"),
            "rollout": "~/.codex/sessions/<date>/<session>.jsonl",
        },
        "tokens": usage,
    }
    (run_dir / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = {
        "task_id": args.task_id,
        "arm": args.arm,
        "status": "ok" if args.exit == 0 else "infra_failed",
        "oracle": args.oracle,
        "task_success": args.success,
        "constraint_compliance": args.constraint,
        "wrong_direction": args.wrong_direction,
        "rework": args.rework,
        "meta": meta,
        "evidence_notes": args.notes,
    }
    (run_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta["tokens"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
