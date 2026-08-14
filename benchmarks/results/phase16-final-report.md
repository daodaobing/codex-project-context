# Phase 1.6 Final — Real Natural Adoption + Semantic Selection Validation

Date: 2026-08-14 (runs span 2026-08-13 evening and 2026-08-14 early morning, Asia/Shanghai)
Checkpoint: `b18d97c` (Candidate Retrieval V0.1), branch `main`, no commits, no push.
Deployed runtime: `C:\Users\91991\.codex\context-server` (files byte-identical to repo HEAD; see #1).

---

# 1. Fresh MCP Exposure

- Tool count: **13** (fresh-process `tools/list` probe against the deployed server; serverInfo `codex-project-context` v1.29.0)
- `get_context_candidates` visible: **YES**
- Real evidence:
  1. In-session `tool_search` surfaced `mcp__context_server__get_context_candidates`.
  2. A live in-session call returned a full 10-candidate pack (click-01 task: required docs ranked 1/2/4).
  3. `~/.codex/config.toml` registers `[mcp_servers.context_server]` -> deployed venv python + `server.py`.
  4. SHA256 parity vs `b18d97c`: `server.py`, `scanners/candidate_retriever.py`, `scanners/document_family.py`, `scanners/instrumentation.py`, `scanners/safe_paths.py` all identical.
  5. Scratch-copy parity: the same task against a copied workspace produced identical candidate paths/ranks.

# 2. Experiment Design

- Subject: real Codex runs as independent context-free sub-agents (`fork_turns=none`); task prompts contained **no tool names**, no doc hints, no benchmark hints. A neutral scratch environment (`C:\Users\91991\.codex\phase16-live\<repo>`) was used after batch 1 exposed a harness-contamination vector (see #13).
- Natural runs: 17 (15 clean + 2 contaminated-but-recorded: `nat_click_01`, `nat_uvicorn_01` read `benchmarks/tasks.json` from the workspace-env; `nat_click_01b` is the clean rerun; `nat_uvicorn_01b` was the clean rerun attempt and is an infra failure).
- Guided runs: 8 (one-sentence generic hint appended: *"When project-level documentation is likely relevant, consider using the available project-context retrieval tools before broad documentation reads."*). 7 have usable evidence; 1 died before any tool call.
- Classes (frozen suite): project_knowledge / known_hard / simple; 8 repos: click, uvicorn, axios, ruff, httpx, jest, catch2, gin.
- Natural: 11 PK, 3 KH, 3 simple. Guided: 6 PK, 2 KH.

# 3. Natural Adoption

`get_context_candidates` calls in natural runs: **0 / 17 (0.0%)**.
`tool_search` calls in natural runs: **0 / 17**. Legacy context tools (`get_project_context`/`get_context_pack`/`scan_project`/...): **0 / 17**.

| Class | Runs | Adopted | Rate |
|---|---|---|---|
| project_knowledge | 11 | 0 | 0% |
| known_hard | 3 | 0 | 0% |
| simple | 3 | 0 | 0% |

| Repo | Runs | Adopted |
|---|---|---|
| click | 5 | 0 |
| uvicorn | 3 | 0 |
| axios | 3 | 0 |
| jest | 2 | 0 |
| ruff / httpx / catch2 / gin | 1 each | 0 |

Un-adopted tasks: all 17. Natural agents went source-first (rg/Get-Content on `src/`, `lib/`, `crates/`), reproduced behavior with tests or live runs, and only occasionally discovered docs on their own (12 required-doc reads out of 26 across the 12 clean dependent natural runs = 30.8%).

# 4. Guided Adoption (separate cohort)

7 runs with evidence (8 spawned, 1 infra-dead):

| Run | Tool discovery | Context-MCP call |
|---|---|---|
| gui_click_02 | tool_search (returned only Vercel — discovery miss) | none |
| gui_uvicorn_02 | skill + tool_search | `get_project_context` (legacy) |
| gui_ruff_h02 | tool_search | `get_context_pack` (legacy) |
| gui_jest_h03 | tool_search | none |
| gui2_click_02 | tool_search -> context_server namespace | **`get_context_candidates`** |
| gui2_axios_h02 | none | none |
| gui_catch2_h03 | tool_search -> context_server namespace | **`get_context_candidates`** |

- Guided `get_context_candidates` adoption: **2 / 7 = 28.6%**
- Guided any-Context-MCP adoption: **4 / 7 = 57.1%**
- Guided tool_search usage: **6 / 7 = 85.7%**
- Discovery is inconsistent: one identical search returned only the Vercel namespace; two returned the full `context_server` namespace. Agents that followed the formal skill selected **legacy** tools, because `skill/project-context/SKILL.md` (repo == installed == deployed, unchanged this round) names only `get_project_context` / `get_context_pack` and never mentions `get_context_candidates`.

# 5. Semantic Selection (only tasks where the candidate tool was actually called; n=2)

| Task | Required in Top10 | Required read | Recall |
|---|---|---|---|
| gui2_click_02 (click-02) | 2/2 (commands-and-groups.md r1, commands.md r2) | 1/2 (commands.md not read) | 50% |
| gui_catch2_h03 (catch2-h03) | 2/2 (command-line.md r1, reporters.md r4) | 2/2 | 100% |

- **Candidate Selection Recall (real Codex): 3 / 4 = 75.0%**
- **All-Available-Required-Selected: 1 / 2 = 50%** (eligible=2, all-selected=1: gui_catch2_h03)
- Per-task evidence is transcript-backed: MCP tool call + returned candidate pack + subsequent `rg`/`Get-Content` reads of the doc paths (not self-report).

# 6. Candidate Usage

| Run | Candidates | Candidate docs read | Non-candidate doc reads | Candidate Read Ratio |
|---|---|---|---|---|
| gui2_click_02 | 10 | 2 (commands-and-groups.md, complex.md) | 1 (CHANGES.md) | 20% |
| gui_catch2_h03 | 10 | 3 (command-line.md, ci-and-misc.md, reporters.md) | 0 docs (source/config files only) | 30% |

- Aggregate Candidate Read Ratio: **5 / 20 = 25%**
- **Top10 -> read 10/10: never observed.** No over-reading in the two adopted runs.
- Repeated reads: both agents re-read their key docs/source files 2-4 times (verification passes), which is targeted, not scan-all.

# 7. Non-Candidate Escape

- Reasonable Escape (required doc outside Top10): **0** — in both adopted runs all required docs were inside Top10.
- Unnecessary Escape (candidates contained the evidence, agent still read outside): **1 minor case** — gui2_click_02 read `CHANGES.md` once during orientation. catch2's source-file reads are source verification, not doc escapes.

# 8. Simple Tasks / False Adoption

- simple-01/03/05: candidate tool calls = **0 / 3 -> False Adoption Rate 0.0%**
- Workflow overhead: none. Each simple task used 1-7 commands and answered correctly (v1.12.0; --count/--name; GET). No "candidate -> reasoning -> read" tax was observed.

# 9. Task Success

- Valid runs: 22 / 25; **Task Success 22 / 22 (100%)** (each answer verified against repo ground truth: line-level source spot checks, in-repo test runs, or both).
- Per class: PK 15/15, KH 4/4, simple 3/3.
- Failures: none downstream. 3 runs are infra failures (#10).

# 10. Failure Attribution

- A. MCP Exposure Failure: **0**
- B. Adoption Failure (tool visible; natural dependent task did not call): **cohort-level, 14/14 natural dependent tasks** (all clean natural PK/KH runs). Primary observation of the phase.
- C. Retrieval Failure: **0** (no live tool calls -> no live retrieval miss; the 45-task ratchet's known misses remain frozen at 11, 6 selection-side / 5 retrieval-side).
- D. Selection / Metadata Failure: **1 doc-level miss** (click-02 `docs/commands.md`, rank 2, not read; task still succeeded — secondary).
- E. Over-Reading Failure: **0**
- F. False Adoption: **0**
- G. Downstream Task Failure: **0**
- H. Infrastructure Failure: **3** — `nat_uvicorn_01b`, `gui_click_02`, `gui_axios_h02` died mid-run with upstream provider `402 Insufficient Balance` (deepseek route); not a product failure.

# 11. Proxy vs Real Codex

- Previous deterministic metadata-overlap proxy: Selection Recall **69.57%** (32/46), All-Available **45%** (9/20).
- Real Codex this round (n=2): Selection Recall **75.0%** (3/4), All-Available **50.0%** (1/2).
- Interpretation: directionally consistent — the proxy neither systematically over- nor under-estimated a real agent on this tiny sample. **n=2 is too small for a quantitative claim**; it only shows real selection is in the same ballpark when adoption happens.

# 12. Metadata Verdict

**Current Metadata Sufficient.** Only one selection miss (rank-2 doc not read) across two adopted runs; it is not demonstrably caused by insufficient title/summary/reasons, and the "repeated multi-run miss + metadata-linked failure" bar for Metadata Presentation V0.1 is not met. No code changed.

# 13. Retrieval Ratchet (formal 45-task, vs b18d97c)

| Metric | Value | Floor | Status |
|---|---|---|---|
| Required Recall@10 | 0.945652 | >=0.93 | PASS |
| Worst Dataset Recall@10 | 0.900000 | >=0.88 | PASS |
| All-required-found@10 | 0.888889 | — | unchanged |
| Full Context Reduction | 0.616439 | >=0.60 | PASS |
| Metadata Reduction | 0.949619 | — | unchanged |
| measured p95 metadata chars | 14112 | <=1.15x baseline | PASS |

- Re-run of `analyze_candidate_retrieval.py` over all 4 datasets (45 tasks): every retrieval field **byte-identical** to the committed artifact; only `metadata_assembly_*_ms` timing values differ (machine noise). No drift.

# 14. Instrumentation Limitation

`PROJECT_CONTEXT_BENCHMARK_RUN_ID/TASK_ID` are read per call but live in a per-process environment, so the long-lived MCP server cannot be re-labeled per task from a single Codex session. Not changed this round (recorded as benchmark infrastructure limitation, not product failure). Instead, task/run correlation came from real Codex transcripts: parent rollout `sub_agent_activity` events (task path -> agent thread id) + each agent's own rollout JSONL (ordered tool calls, arguments, outputs, final answers).

# 15. Tests

- `pytest`: **72 passed, 3 skipped** (includes router V2-V5, security paths, server smoke, feasibility, candidate retrieval).
- MCP smoke: live in-session call + fresh-process `tools/list` (13 tools).
- Ratchet: PASS (see #13). 45-task parity: identical except timing noise.
- Feasibility suite invariants: covered by `tests/test_feasibility.py` (passing).
- Diff check: zero tracked-file modifications (see #16).

# 16. Git / Files

- HEAD: `b18d97c`; branch: `main`; no upstream tracking configured (no ahead/behind data); no commits; no push.
- Modified tracked: **none**. Untracked/generated: `benchmarks/feasibility/`, `benchmarks/phase16_analyze.py`, `benchmarks/probe_list_tools_debug.py`, `benchmarks/probe_v05_scan.{py,json}`, `benchmarks/probe_v05_trace{,_compact}.py`, `tests/test_feasibility.py`, `section_diag.obj` (stray object file, unrelated, left in place).
- Generated (ignored): `benchmarks/results/phase16-live-report.json`, `candidate-retrieval-feasibility-phase16.json`, `phase16-pytest.log`.
- Scratch run env (outside repo): `C:\Users\91991\.codex\phase16-live\`.

# 17. Scope Guard

Confirmed untouched: ranking weights (80/40), `rrf_k=10`, `candidate_limit=10`, BM25, normalization, roles, document family, title/path/headings logic, Candidate Pack format, summary/reasons, Query Coverage, `get_project_context` legacy behavior, ratchet baseline, metadata, ground truth, blind sets, repos, Router V2-V5, the formal project-context Skill (repo/installed/deployed copies all unchanged), the deployed runtime (no redeploy; hashes re-verified), no new Blind/external repo, no embeddings/vector DB/reranker, no commit/push. Experiment artifacts are untracked/ignored and were kept.

# 18. Token 消耗与优化记录

Real telemetry (OpenCodex proxy `usage.jsonl`, this thread's conversation id — not chars/4):

- Requests: 744; Input: 72,508,301; Output: 422,492; Cache Read: 69,900,800 (96.4% of input); Cache Creation: 0; Reasoning Output: 253,201; Total: 72,930,793.
- Provider mix: deepseek/deepseek-v4-pro 596 reqs (565x200, 30x402, 1x502); opencode-go/deepseek-v4-pro 106 (105x200); openai/gpt-5.6-luna 30 (29x200); opencode-go/deepseek-v4-flash 12 (12x403).
- Main context sources: 25 sub-agent transcripts (tool calls + outputs + final answers), frozen suite `tasks.json`, candidate packs, source spot-checks, `usage.jsonl`.
- Skills actually used: none by the experiment agents (natural runs were intentionally skill-hint-free; guided agents used the generic sentence only). The `agent-reach` skill was used only in a separate, unrelated provider-diagnosis turn.
- Loaded-but-unused skills: ~300+ skill entries sit in the system prompt by default (plus the disabled `.bak-*` config list) — pure fixed context overhead for every one of the 25 agents.
- Dominant context cost: re-reading full transcripts for evidence extraction + the per-agent system-prompt overhead (25 agents x large skills list), not the retrieval tool itself.

# 19. Final Verdict

**B. Candidate Retrieval 正常，但 Natural Tool Adoption 不足，应先优化正式 Skill / Integration；不应修改 Metadata，也不应进入 Blind。**

Evidence chain: exposure OK (#1) -> natural adoption 0/17 (#3) -> guided adoption jumps to 28.6% candidate / 57.1% context-MCP (#4) -> the two real adoptions show healthy selection (75% recall, no over-read, no false adoption) (#5-#8) -> the formal Skill names only legacy tools and tool_search discovery is inconsistent, so the bottleneck is integration/trigger, not retrieval or metadata (#12).
