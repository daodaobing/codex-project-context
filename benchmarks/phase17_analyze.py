"""Phase 1.7 live-run transcript analyzer (skill-trigger smoke test).

Same evidence model as Phase 1.6: parent rollout maps task -> agent thread,
each agent rollout provides the ordered tool calls and outputs. Read-only;
never calls the MCP server.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE = json.loads((ROOT / "benchmarks" / "feasibility" / "tasks.json").read_text(encoding="utf-8"))
TASKS = {t["task_id"]: t for t in SUITE["tasks"]}

RUNS: dict[str, dict] = {
    "p17_click_01": {"suite_id": "click-01", "class": "project_knowledge"},
    "p17_click_04": {"suite_id": "click-04", "class": "project_knowledge"},
    "p17_uvicorn_03": {"suite_id": "uvicorn-03", "class": "project_knowledge"},
    "p17_axios_h01": {"suite_id": "axios-h01", "class": "project_knowledge"},
    "p17_httpx_h01": {"suite_id": "httpx-h01", "class": "project_knowledge"},
    "p17_jest_h01": {"suite_id": "jest-h01", "class": "project_knowledge"},
    "p17_simple_01": {"suite_id": "simple-01", "class": "simple"},
    "p17_simple_03": {"suite_id": "simple-03", "class": "simple"},
    "p17_simple_05": {"suite_id": "simple-05", "class": "simple"},
    "p17c_click_01": {"suite_id": "click-01", "class": "project_knowledge"},
    "p17c_click_04": {"suite_id": "click-04", "class": "project_knowledge"},
    "p17c_uvicorn_03": {"suite_id": "uvicorn-03", "class": "project_knowledge"},
    "p17c_axios_h01": {"suite_id": "axios-h01", "class": "project_knowledge"},
    "p17c_httpx_h01": {"suite_id": "httpx-h01", "class": "project_knowledge"},
    "p17c_jest_h01": {"suite_id": "jest-h01", "class": "project_knowledge"},
    "p17c_simple_01": {"suite_id": "simple-01", "class": "simple"},
    "p17c_simple_03": {"suite_id": "simple-03", "class": "simple"},
    "p17c_simple_05": {"suite_id": "simple-05", "class": "simple"},
}

SCRATCH = Path(r"C:\Users\91991\.codex\phase16-live")
ABS_PATH_TOKEN = re.compile(r"[A-Za-z]:[\\/][^\s\"'`|;,&]+")
REL_PATH_TOKEN = re.compile(r"(?<![\w.-])[\w.-]+(?:[\\/][\w.-]+)*\.[A-Za-z0-9]{1,12}")


def _load_jsonl(path: Path) -> list[dict]:
    items = []
    if not path.is_file():
        return items
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _parent_mapping(parent: Path) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for obj in _load_jsonl(parent):
        if obj.get("type") == "response_item":
            payload = obj.get("payload", {})
            if payload.get("type") == "function_call" and payload.get("name") == "spawn_agent":
                args = json.loads(payload.get("arguments") or "{}")
                mapping.setdefault(args.get("task_name", "?"), {"message": args.get("message", "")})
        elif obj.get("type") == "event_msg":
            p = obj.get("payload", {})
            if p.get("type") == "sub_agent_activity" and p.get("kind") == "started":
                name = p.get("agent_path", "").rsplit("/", 1)[-1]
                if name in mapping:
                    mapping[name]["thread_id"] = p.get("agent_thread_id")
            if p.get("type") == "sub_agent_activity" and p.get("kind") == "finished":
                name = p.get("agent_path", "").rsplit("/", 1)[-1]
                if name in mapping:
                    mapping[name]["finished"] = True
    return mapping


def _real_files(cmd: str, workdir: str) -> list[str]:
    found = []
    for match in ABS_PATH_TOKEN.findall(cmd):
        candidate = os.path.normpath(match.strip("\"'`"))
        if os.path.isfile(candidate):
            found.append(candidate)
    for match in REL_PATH_TOKEN.findall(cmd):
        candidate = os.path.normpath(os.path.join(workdir or "", match.strip("\"'`")))
        if os.path.isfile(candidate) and candidate not in found:
            found.append(candidate)
    return found


def _candidate_paths(output_text: str) -> list[dict]:
    text = output_text
    marker = text.find('[{"type":')
    if marker < 0:
        marker = text.find('{"project"')
    if marker >= 0:
        text = text[marker:]
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(outer, list):
        try:
            inner = json.loads(outer[0]["text"])
        except Exception:
            return []
        return [{"path": c.get("path"), "rank": c.get("rank")} for c in inner.get("candidates", [])]
    if isinstance(outer, dict):
        return [{"path": c.get("path"), "rank": c.get("rank")} for c in outer.get("candidates", [])]
    return []


def _analyze_agent(path: Path) -> dict:
    calls: list[dict] = []
    outputs: dict[str, str] = {}
    final_answer = ""
    for obj in _load_jsonl(path):
        if obj.get("type") != "response_item":
            continue
        p = obj.get("payload", {})
        if p.get("type") == "function_call":
            try:
                args = json.loads(p.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"name": p.get("name"), "args": args, "call_id": p.get("call_id")})
        elif p.get("type") == "custom_tool_call":
            calls.append({"name": p.get("name") or "custom_tool_call", "args": p.get("input") or {}, "call_id": p.get("call_id")})
        elif p.get("type") == "tool_search_call":
            calls.append({"name": "tool_search", "args": {"query": (p.get("arguments") or {}).get("query")}, "call_id": p.get("id")})
        elif p.get("type") == "function_call_output":
            outputs[p.get("call_id", "")] = p.get("output") or ""
        elif p.get("type") == "tool_search_output":
            outputs[p.get("call_id", "")] = json.dumps(p)
        elif p.get("type") == "message" and p.get("role") == "assistant":
            text = " ".join(c.get("text", "") for c in (p.get("content") or []) if isinstance(c, dict))
            if text.strip():
                final_answer = text

    candidate_calls = [c for c in calls if c["name"] in ("get_context_candidates", "mcp__context_server__get_context_candidates")]
    legacy_calls = [c for c in calls if c["name"] in ("get_project_context", "get_context_pack", "scan_project", "suggest_projects", "list_projects", "get_project_index", "initialize_project_context", "analyze_changes")]
    search_calls = [c for c in calls if c["name"] == "tool_search"]
    candidate_pack = []
    for c in candidate_calls:
        out = outputs.get(c["call_id"], "")
        if not isinstance(out, str):
            continue
        for row in _candidate_paths(out):
            if row["path"] not in [x["path"] for x in candidate_pack]:
                candidate_pack.append(row)
    if not candidate_pack:
        for out in outputs.values():
            if not isinstance(out, str):
                continue
            for row in _candidate_paths(out):
                if row["path"] not in [x["path"] for x in candidate_pack]:
                    candidate_pack.append(row)

    reads: list[str] = []
    for c in calls:
        if c["name"] != "exec_command":
            continue
        for real in _real_files(c["args"].get("cmd", ""), c["args"].get("workdir") or ""):
            reads.append(real)
    return {
        "calls": calls,
        "candidate_calls": candidate_calls,
        "legacy_calls": legacy_calls,
        "search_calls": search_calls,
        "candidate_pack": candidate_pack,
        "reads": reads,
        "final_answer": final_answer,
    }


def analyze(parent: Path, sessions_dirs: list[Path]) -> dict:
    mapping = _parent_mapping(parent)
    rows = []
    for name, meta in RUNS.items():
        suite = TASKS[meta["suite_id"]]
        repo = suite["repository"]
        thread_id = mapping.get(name, {}).get("thread_id")
        if not thread_id:
            rows.append({"name": name, "suite_id": meta["suite_id"], "error": "thread not found"})
            continue
        agent_file = None
        for base in sessions_dirs:
            candidate = next(base.glob(f"*{thread_id}*.jsonl"), None)
            if candidate is not None:
                agent_file = candidate
                break
        if agent_file is None:
            rows.append({"name": name, "suite_id": meta["suite_id"], "error": "rollout missing"})
            continue
        a = _analyze_agent(agent_file)
        root = SCRATCH / repo
        rel_reads = []
        for r in a["reads"]:
            rn = os.path.normcase(r)
            if rn.startswith(os.path.normcase(str(root))):
                rel_reads.append(os.path.relpath(r, root).replace("\\", "/"))
        rel_l = [r.casefold() for r in rel_reads]
        candidate_paths = [c["path"] for c in a["candidate_pack"]]
        cand_l = [p.casefold() for p in candidate_paths]
        required = suite.get("required_docs", [])
        required_in_candidate = [p for p in required if p.casefold() in cand_l]
        required_read = [p for p in required if p.casefold() in rel_l]
        candidate_read = [p for p in candidate_paths if p.casefold() in rel_l]
        rows.append(
            {
                "name": name,
                "suite_id": meta["suite_id"],
                "task_class": meta["class"],
                "repository": repo,
                "project_context_expected": suite["project_context_expected"],
                "thread_id": thread_id,
                "finished": mapping.get(name, {}).get("finished", False),
                "call_count": len(a["calls"]),
                "adopted_candidates": len(a["candidate_calls"]) > 0,
                "adopted_legacy": [c["name"] for c in a["legacy_calls"]],
                "adopted_search": [c["args"].get("query") for c in a["search_calls"]],
                "candidate_count": len(candidate_paths),
                "candidate_paths": candidate_paths,
                "candidate_reads": candidate_read,
                "required_in_candidate": required_in_candidate,
                "required_read": required_read,
                "required_docs": required,
                "answer_file": suite.get("answer_file"),
                "doc_reads": rel_reads,
                "repeated_reads": [p for p, n in Counter(rel_reads).items() if n > 1],
                "final_answer": a["final_answer"][:5000],
                "has_final": bool(a["final_answer"].strip()),
            }
        )
    return {"tasks": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", default=str(Path(r"C:\Users\91991\.codex\sessions\2026\08\13\rollout-2026-08-13T22-00-12-019ffb6c-6205-7751-89b9-735c462049ad.jsonl")))
    parser.add_argument("--sessions-dir", nargs="+", default=[
        str(Path(r"C:\Users\91991\.codex\sessions\2026\08\13")),
        str(Path(r"C:\Users\91991\.codex\sessions\2026\08\14")),
    ])
    parser.add_argument("--out", default=str(ROOT / "benchmarks" / "results" / "phase17-live-report.json"))
    args = parser.parse_args()
    report = analyze(Path(args.parent), [Path(p) for p in args.sessions_dir])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in report["tasks"]:
        print(
            row.get("name"), "| class=", row.get("task_class"),
            "| cand_adopt=", row.get("adopted_candidates"),
            "| legacy=", row.get("adopted_legacy"),
            "| search=", bool(row.get("adopted_search")),
            "| cand=", row.get("candidate_count"),
            "| req_read=", len(row.get("required_read") or []),
            "| final=", row.get("has_final"),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
