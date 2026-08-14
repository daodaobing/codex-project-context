"""Phase 1.6 live-run transcript analyzer.

Reads the parent session rollout plus each sub-agent rollout and rebuilds,
per task: tool-call sequence (MCP candidate tool, legacy context tools,
tool_search, file reads), candidate pack contents, and final answers.

Experiment-only artifact (untracked). It never calls the MCP server itself;
all evidence comes from recorded transcripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE = json.loads((ROOT / "benchmarks" / "feasibility" / "tasks.json").read_text(encoding="utf-8"))
TASKS = {t["task_id"]: t for t in SUITE["tasks"]}

# Run manifest: natural-run task name -> frozen suite task id and environment.
RUNS: dict[str, dict] = {
    "nat_click_01": {"suite_id": "click-01", "class": "project_knowledge", "scratch": False, "guided": False},
    "nat_click_01b": {"suite_id": "click-01", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_click_03": {"suite_id": "click-03", "class": "known_hard", "scratch": True, "guided": False},
    "nat_click_04": {"suite_id": "click-04", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_uvicorn_01": {"suite_id": "uvicorn-01", "class": "project_knowledge", "scratch": False, "guided": False},
    "nat_uvicorn_01b": {"suite_id": "uvicorn-01", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_uvicorn_03": {"suite_id": "uvicorn-03", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_axios_h01": {"suite_id": "axios-h01", "class": "project_knowledge", "scratch": False, "guided": False},
    "nat_axios_h04": {"suite_id": "axios-h04", "class": "known_hard", "scratch": True, "guided": False},
    "nat_ruff_h01": {"suite_id": "ruff-h01", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_httpx_h01": {"suite_id": "httpx-h01", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_jest_h01": {"suite_id": "jest-h01", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_jest_h02": {"suite_id": "jest-h02", "class": "known_hard", "scratch": True, "guided": False},
    "nat_catch2_h01": {"suite_id": "catch2-h01", "class": "project_knowledge", "scratch": True, "guided": False},
    "nat_simple_01": {"suite_id": "simple-01", "class": "simple", "scratch": True, "guided": False},
    "nat_simple_03": {"suite_id": "simple-03", "class": "simple", "scratch": True, "guided": False},
    "nat_simple_05": {"suite_id": "simple-05", "class": "simple", "scratch": True, "guided": False},
    "gui_click_02": {"suite_id": "click-02", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui_uvicorn_02": {"suite_id": "uvicorn-02", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui_axios_h02": {"suite_id": "axios-h02", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui_ruff_h02": {"suite_id": "ruff-h02", "class": "known_hard", "scratch": True, "guided": True},
    "gui_jest_h03": {"suite_id": "jest-h03", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui_catch2_h03": {"suite_id": "catch2-h03", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui2_click_02": {"suite_id": "click-02", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui2_axios_h02": {"suite_id": "axios-h02", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui_ruff_h02": {"suite_id": "ruff-h02", "class": "known_hard", "scratch": True, "guided": True},
    "gui_jest_h03": {"suite_id": "jest-h03", "class": "project_knowledge", "scratch": True, "guided": True},
    "gui_catch2_h03": {"suite_id": "catch2-h03", "class": "project_knowledge", "scratch": True, "guided": True},
}

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


def _parent_mapping(parent: Path) -> tuple[dict[str, dict], list[dict]]:
    """Return {task_name: {thread_id, message}} and ordered spawn events."""

    mapping: dict[str, dict] = {}
    spawns = []
    for obj in _load_jsonl(parent):
        if obj.get("type") == "response_item":
            payload = obj.get("payload", {})
            if payload.get("type") == "function_call" and payload.get("name") == "spawn_agent":
                args = json.loads(payload.get("arguments") or "{}")
                spawns.append({"task_name": args.get("task_name"), "message": args.get("message", "")})
                mapping.setdefault(args.get("task_name", "?"), {"message": args.get("message", "")})
        elif obj.get("type") == "event_msg":
            p = obj.get("payload", {})
            if p.get("type") == "sub_agent_activity" and p.get("kind") == "started":
                name = p.get("agent_path", "").rsplit("/", 1)[-1]
                if name in mapping:
                    mapping[name]["thread_id"] = p.get("agent_thread_id")
                    mapping[name].setdefault("finished", False)
            if p.get("type") == "sub_agent_activity" and p.get("kind") == "finished":
                name = p.get("agent_path", "").rsplit("/", 1)[-1]
                if name in mapping:
                    mapping[name]["finished"] = True
    return mapping, spawns


def _real_files(cmd: str, workdir: str) -> list[str]:
    """Absolute paths referenced by a command that exist as files."""

    found = []
    for match in ABS_PATH_TOKEN.findall(cmd):
        candidate = match.strip("\"'`")
        candidate = os.path.normpath(candidate)
        if os.path.isfile(candidate):
            found.append(candidate)
    for match in REL_PATH_TOKEN.findall(cmd):
        candidate = match.strip("\"'`")
        candidate = os.path.normpath(os.path.join(workdir or "", candidate))
        candidate = os.path.normpath(candidate)
        if os.path.isfile(candidate):
            if candidate not in found:
                found.append(candidate)
    return found


def _candidate_paths(output_text: str) -> list[dict]:
    """Parse candidate pack paths/ranks from a get_context_candidates output."""

    # Tool outputs are wrapped in an exec-style envelope ("Wall time: ...").
    # Locate the embedded JSON array/dict before parsing.
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
        return [
            {"path": c.get("path"), "rank": c.get("rank")}
            for c in inner.get("candidates", [])
        ]
    if isinstance(outer, dict):
        return [
            {"path": c.get("path"), "rank": c.get("rank")}
            for c in outer.get("candidates", [])
        ]
    return []


def _analyze_agent(path: Path) -> dict:
    events = _load_jsonl(path)
    calls: list[dict] = []
    outputs: dict[str, str] = {}
    final_answer = ""
    workdirs: dict[str, str] = {}
    for obj in events:
        if obj.get("type") != "response_item":
            continue
        p = obj.get("payload", {})
        if p.get("type") == "function_call":
            name = p.get("name")
            try:
                args = json.loads(p.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            call = {"name": name, "args": args, "call_id": p.get("call_id")}
            calls.append(call)
            if "workdir" in args:
                workdirs[p.get("call_id", "")] = args["workdir"]
        elif p.get("type") == "custom_tool_call":
            args = p.get("input") or {}
            call = {"name": p.get("name") or "custom_tool_call", "args": args, "call_id": p.get("call_id")}
            calls.append(call)
        elif p.get("type") == "tool_search_call":
            call = {"name": "tool_search", "args": {"query": p.get("query")}, "call_id": p.get("id")}
            calls.append(call)
        elif p.get("type") == "function_call_output":
            outputs[p.get("call_id", "")] = p.get("output") or ""
        elif p.get("type") == "tool_search_output":
            outputs[p.get("call_id", "")] = json.dumps(p)
        elif p.get("type") == "message" and p.get("role") == "assistant":
            parts = p.get("content") or []
            text = " ".join(c.get("text", "") for c in parts if isinstance(c, dict))
            if text.strip():
                final_answer = text

    candidate_calls = [c for c in calls if c["name"] in ("get_context_candidates", "mcp__context_server__get_context_candidates")]
    legacy_calls = [c for c in calls if c["name"] in ("get_project_context", "get_context_pack", "scan_project", "suggest_projects", "list_projects", "get_project_index", "initialize_project_context", "analyze_changes")]
    search_calls = [c for c in calls if c["name"] == "tool_search"]
    candidate_pack = []
    for c in candidate_calls:
        out = outputs.get(c["call_id"], "")
        for row in _candidate_paths(out):
            if row["path"] not in [x["path"] for x in candidate_pack]:
                candidate_pack.append(row)
    if not candidate_pack:
        # MCP tool outputs may not share a call_id with their function_call
        # record; fall back to scanning every tool output for a candidate pack.
        for out in outputs.values():
            for row in _candidate_paths(out):
                if row["path"] not in [x["path"] for x in candidate_pack]:
                    candidate_pack.append(row)

    reads: list[str] = []
    read_events = []
    for c in calls:
        if c["name"] != "exec_command":
            continue
        wd = c["args"].get("workdir") or ""
        for real in _real_files(c["args"].get("cmd", ""), wd):
            reads.append(real)
            read_events.append({"file": real, "kind": "exec_command"})

    return {
        "calls": calls,
        "candidate_calls": candidate_calls,
        "legacy_calls": legacy_calls,
        "search_calls": search_calls,
        "candidate_pack": candidate_pack,
        "reads": reads,
        "read_events": read_events,
        "final_answer": final_answer,
        "has_final": bool(final_answer.strip()),
    }


def _rel(read: str, roots: list[str]) -> str | None:
    read = os.path.normcase(read)
    for root in roots:
        root = os.path.normcase(root)
        if read.startswith(root):
            return os.path.relpath(read, root)
    return None


def analyze(parent: Path, sessions_dir: Path) -> dict:
    mapping, _ = _parent_mapping(parent)
    rows = []
    for name, meta in RUNS.items():
        thread_id = mapping.get(name, {}).get("thread_id")
        suite = TASKS[meta["suite_id"]]
        repo = suite["repository"]
        if meta["scratch"]:
            root = Path(r"C:\Users\91991\.codex\phase16-live") / repo
        else:
            root = ROOT / "benchmarks" / "workspaces" / repo
        if not thread_id:
            rows.append({"name": name, "suite_id": meta["suite_id"], "error": "thread not found"})
            continue
        agent_file = None
        for base in sessions_dir:
            candidate = next(base.glob(f"*{thread_id}*.jsonl"), None)
            if candidate is not None:
                agent_file = candidate
                break
        if agent_file is None:
            rows.append({"name": name, "suite_id": meta["suite_id"], "error": "rollout missing"})
            continue
        a = _analyze_agent(agent_file)
        rel_reads = [_rel(r, [str(root)]) for r in a["reads"]]
        rel_reads = [r.replace("\\", "/") for r in rel_reads if r]
        candidate_paths = [c["path"] for c in a["candidate_pack"]]
        required = suite.get("required_docs", [])
        answer_file = suite.get("answer_file")
        rel_reads_l = [r.casefold() for r in rel_reads]
        candidate_paths_l = [p.casefold() for p in candidate_paths]
        required_in_candidate = [p for p in required if p.casefold() in candidate_paths_l]
        required_read = [p for p in required if p.casefold() in rel_reads_l]
        candidate_read = [p for p in candidate_paths if p.casefold() in rel_reads_l]
        non_candidate_doc_read = [
            p for p in rel_reads
            if p not in candidate_paths and p not in required_read
            and (p.lower().endswith((".md", ".markdown", ".rst", ".txt")) or "/docs/" in p.replace("\\", "/") or p.replace("\\", "/").startswith("docs/"))
        ]
        repeated = [p for p, n in Counter(rel_reads).items() if n > 1]
        adopted = len(a["candidate_calls"]) > 0
        legacy_adopted = len(a["legacy_calls"]) > 0
        search_adopted = len(a["search_calls"]) > 0
        rows.append(
            {
                "name": name,
                "suite_id": meta["suite_id"],
                "task_class": meta["class"],
                "guided": meta["guided"],
                "repository": repo,
                "project_context_expected": suite["project_context_expected"],
                "scratch": meta["scratch"],
                "thread_id": thread_id,
                "finished": mapping.get(name, {}).get("finished", False),
                "has_final": a["has_final"],
                "call_count": len(a["calls"]),
                "adopted_candidates": adopted,
                "adopted_legacy": legacy_adopted,
                "adopted_search": search_adopted,
                "candidate_call_count": len(a["candidate_calls"]),
                "candidate_limit": [c["args"].get("limit") for c in a["candidate_calls"]],
                "legacy_tools": [c["name"] for c in a["legacy_calls"]],
                "search_queries": [c["args"].get("query") for c in a["search_calls"]],
                "candidate_count": len(candidate_paths),
                "candidate_paths": candidate_paths,
                "candidate_reads": candidate_read,
                "required_in_candidate": required_in_candidate,
                "required_read": required_read,
                "required_docs": required,
                "answer_file": answer_file,
                "non_candidate_doc_reads": non_candidate_doc_read,
                "doc_reads_total": len(rel_reads),
                "all_reads": rel_reads,
                "repeated_reads": repeated,
                "final_answer": a["final_answer"][:6000],
            }
        )
    return {"tasks": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", default=str(Path(r"C:\Users\91991\.codex\sessions\2026\08\13\rollout-2026-08-13T22-00-12-019ffb6c-6205-7751-89b9-735c462049ad.jsonl")))
    parser.add_argument(
        "--sessions-dir",
        nargs="+",
        default=[
            str(Path(r"C:\Users\91991\.codex\sessions\2026\08\13")),
            str(Path(r"C:\Users\91991\.codex\sessions\2026\08\14")),
        ],
    )
    parser.add_argument("--out", default=str(ROOT / "benchmarks" / "results" / "phase16-live-report.json"))
    args = parser.parse_args()
    report = analyze(Path(args.parent), [Path(p) for p in args.sessions_dir])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in report["tasks"]:
        print(
            row.get("name"),
            "| class=", row.get("task_class"),
            "| adopted=", row.get("adopted_candidates"),
            "| legacy=", row.get("legacy_tools"),
            "| search=", bool(row.get("search_queries")),
            "| cand=", row.get("candidate_count"),
            "| req_read=", len(row.get("required_read") or []),
            "| final=", row.get("has_final"),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
