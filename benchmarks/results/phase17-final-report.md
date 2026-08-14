# Phase 1.7 — Candidate Retrieval Integration / Skill Trigger V0.1

Date: 2026-08-14. Checkpoint: `b18d97c`. No commit, no push (per instructions).

---

# 1. Executive Verdict

**PARTIAL.** The skill-trigger work itself is complete, synced, and test-covered, and a real platform-side defect was found and fixed in the skill layer (the discoverability-critical text was truncated out of the skills list). However, live Natural Adoption remained **0/9** even with the corrected description visible to every fresh agent. The remaining blocker is platform-level skill/tool consultation behavior, not skill content — so the verdict is PARTIAL with recommendation D below.

# 2. Initial Git State

`HEAD=b18d97c`, branch `main`, no upstream. Tracked tree clean at start (only pre-existing untracked Phase 1.6 artifacts). No reset/clean/rebase/restore performed; all pre-existing untracked files preserved.

# 3. Skill Copy Audit

| Path | Role | Before Version | After Version | Synced |
|---|---|---|---|---|
| repo `skill/project-context/SKILL.md` | source of truth (tracked) | 3773 B CRLF, legacy-only workflow | 5685 B LF, two-stage candidate-first | YES |
| `C:\Users\91991\.codex\skills\project-context\SKILL.md` | installed copy; loaded by current Codex skills list; also scanned by the MCP skill registry | identical to source | byte-identical to source | YES |
| `C:\Users\91991\.codex\context-server\skill\project-context\SKILL.md` | deployed/packaged copy (content was equal, LF line endings); not read at runtime (registry roots point at `~/.codex/skills`) | content-equal to source | byte-identical to source | YES |

- `skill/project-context/agents/openai.yaml`: identical across all three copies (SHA 84c0b2…) — unchanged.
- Deployed skill registry (`context-server/index/skill-registry.json`, generated): rebuilt after the edit; project-context entry now carries the new description.
- Related 4th surface NOT modified: `trae/project-context.rules.md` (Trae-only rules, still legacy-biased) — flagged for follow-up, out of scope for the three Codex-relevant copies.

# 4. Root Cause

Why Phase 1.6 Natural Adoption was 0% (and stayed 0% in Phase 1.7):

- Tool Exposure: NOT the problem — `get_context_candidates` is deployed, listed (13 tools), and callable in-session.
- Tool Discoverability: PARTLY the problem — the tool is a deferred MCP tool; discovery depends on `tool_search`, which is unstable (Phase 1.6: one query returned only the Vercel namespace; another returned the full `context_server` namespace).
- Skill Trigger: the skill was never consulted by fresh natural agents; its "Use when..." description was generic and, in the rendered skills list, truncated before any mention of the candidate tool.
- Legacy Bias: the skill body named only `get_project_context` / `get_context_pack` as the workflow; agents that did consult context tools therefore used the legacy full-context path.

Phase 1.7 fixed Skill Trigger content and Legacy Bias, and mitigated Discoverability at the skill layer (tool name now inside the visible description window). The residual 0% adoption is platform-level consultation behavior.

# 5. Trigger Decision Model

1. Do I already have enough context? (exact file/function given; small known change) -> work directly.
2. Does the task depend on project-specific knowledge (architecture/conventions, multi-doc rules, design decisions, unfamiliar repo) rather than pure code logic? No -> work directly.
3. Do I already know exactly which document to read? Yes -> read it directly.
4. Otherwise -> `get_context_candidates(project_path, task)` -> inspect metadata -> read only the chosen candidates -> verify in source/tests.

Anti-triggers (work directly): exact file path or function given; small localized fix; clear function-level bug; simple command/usage question; the unique relevant file already known; generic non-repo task.

# 6. Changes Made

- `skill/project-context/SKILL.md` (source): new description leading with `get_context_candidates` inside the visible window; new "When to use this skill" trigger/anti-trigger lists; new "Two-stage workflow" (Stage 1 candidate discovery, Stage 2 selective reading); new "Decision model" (4 steps); "Full context (legacy path)" section explicitly says legacy tools are no longer the default first step while remaining supported; guardrails against mechanical Top-N reads, false adoption, and doc-over-trust.
- Installed + deployed SKILL.md copies: mechanically synced from source (byte-identical).
- `context-server/index/skill-registry.json` (generated): rebuilt via the server's own registry scan; now serves the new description.
- `tests/test_skill_trigger.py` (new): 12 deterministic tests (copy sync, structure, no benchmark terms, no absolute paths, no unconditional mandate, 8 trigger/anti-trigger scenarios, registry freshness).
- `benchmarks/phase17_analyze.py` (new, untracked): transcript analyzer for the live smoke cohort.

# 7. Candidate Retrieval Workflow

- Task -> Candidates -> Selective Read -> Source/Test Verification: when project knowledge is likely needed but which docs matter is unknown (unfamiliar repo, multi-doc rules, design context).
- Task -> Direct Work: exact file/function given, localized fix, known unique file, simple/generic tasks.
- Task -> Legacy Full Context: only when the complete pack is genuinely needed, existing tooling depends on `get_project_context`/`get_context_pack`, or candidates are unavailable/errored.

# 8. Legacy Compatibility

`get_project_context` and `get_context_pack` both retained, in code and in the skill text. MCP schema untouched. The skill now presents them as an explicit compatibility path, with candidate discovery as the default first step for "which docs?" situations.

# 9. Static Validation

- All three SKILL.md copies byte-identical (SHA 4291dd34… / 5685 B after final description fix).
- No benchmark terms (Click/Jest/Axios/45-task/Phase 1.6/benchmark/Recall@10/Blind/required_docs/ground truth) in the skill.
- No absolute local paths; no "Always call …"; no "must read Top-N" mandates.
- Legacy tools present but explicitly repositioned; source/tests declared the final truth.

# 10. Trigger Scenario Validation

| Scenario | Expected | Actual | PASS |
|---|---|---|---|
| plugin registration respecting architecture/conventions | trigger | trigger | YES |
| multi-doc config rule location | trigger | trigger | YES |
| unfamiliar codebase conventions | trigger | trigger | YES |
| recover design decisions | trigger | trigger | YES |
| exact file path constant change | no_trigger | no_trigger | YES |
| clear function-level bug | no_trigger | no_trigger | YES |
| simple command question | no_trigger | no_trigger | YES |
| exact file path + line known | no_trigger | no_trigger | YES |

Deterministic spec-mirror classification; explicitly NOT a proof of live adoption.

# 11. Live Agent Validation

- Sample: 9 context-free agents (6 project-knowledge + 3 simple), prompts without any tool names / context hints / benchmark terms; neutral scratch environment. Two cohorts: `p17` (first description, whose critical tail was truncated) and `p17c` (corrected description). Three additional infra-failed runs (provider stream disconnects during a proxy restart) excluded from rates.
- Verified per-agent that the skills-list entry in the agent's context contained the new description (`p17c` cohort: "…Prefer get_context_candidates: discover a s…" visible inside the truncation window).
- Natural Candidate Adoption: **0/6 PK, 0/3 simple (0/9)** in both cohorts.
- Any Context-MCP Adoption: **0/9**. tool_search: **0/9**.
- False Adoption: **0/3**.
- Candidate Read Ratio: N/A (no candidate calls). Top-10 Full Read Count: 0. Candidate Selection Recall: N/A.
- Task Success: **9/9** (all answers correct; spot-checked against repo source).
- Observation: even with the tool name visible in the skills list, fresh agents never consulted the skill or the Context MCP for these tasks; they proceeded source-first and still produced fully correct answers. Live behavior reported as observed behavior only; n=9, no significance claims.

# 12. Retrieval Ratchet

Unchanged (no retrieval edits made): Required Recall@10 0.945652 (>=0.93), Worst Dataset 0.900000 (>=0.88), All-required-found 0.888889, Full Context Reduction 0.616439 (>=0.60), Metadata Reduction 0.949619, measured p95 metadata 14112 (<=1.15x baseline). 45-task parity re-verified identical (timing noise only). Ratchet check: PASS.

# 13. tool_search Discoverability Analysis

- Solved at skill layer: partially — the candidate tool is now named in the skill description that fresh agents actually see (this also removes the dependence on tool_search for agents that do consult the skill).
- Dependency reduced: yes in principle; not observable in practice because natural agents consulted neither.
- Remaining problem: fresh natural agents do not consult the skills list for these source-first tasks, and tool_search result quality remains inconsistent (one Phase 1.6 query returned only the Vercel namespace). Both behaviors are platform-side.
- Classification: **External Platform Limitation** (Codex skill-consultation trigger + deferred-tool search behavior). Not modifiable from this project without touching Codex internals (forbidden).

# 14. Failure / Edge Cases

- Skill not loaded: possible; nothing in this project can force it. Mitigated by putting the tool name in the description (loaded whenever the skills list is rendered) — still insufficient alone.
- tool_search unstable: real, observed; skill layer now avoids requiring it.
- Simple-task false trigger: anti-trigger list + guardrail; live false adoption 0/3.
- Tool visible but agent doesn't call: the current live reality (0/9); documented as platform limitation.
- Candidate tool called then Top-10 full read: guarded by explicit "read only what you need / never read every candidate" guardrails; not observable live (no adoption).
- Candidate docs insufficient: skill points to fallback (legacy full context or direct doc reads); the MCP tool itself fail-opens with a fallback message naming `get_project_context`.
- Candidates conflict with source: skill declares source/tests the final truth; docs only narrow scope.

# 15. Scope Guard

Unmodified and re-verified: ranking weights, RRF/rrf_k, candidate_limit, BM25, normalization, roles, document family, title/path/headings, Candidate Pack, summary/reasons, Query Coverage, metadata schema, MCP contract/schema, legacy tools, frozen ground truth/required_docs, Blind sets, Router V2-V5, Codex/tool_search/provider/system prompt, deployed runtime files (server.py/scanners hashes unchanged). No new repos/blinds/embedding/vector DB/reranker. No commit, no push.

# 16. Tests

`pytest`: **84 passed, 3 skipped** (previous 72 + new 12 skill-trigger tests). Skill trigger suite: 12/12. Ratchet: PASS.

# 17. Files Changed

- source: `skill/project-context/SKILL.md` (modified; the only tracked-file change)
- installed/deployed: `~/.codex/skills/project-context/SKILL.md`, `~/.codex/context-server/skill/project-context/SKILL.md` (synced), `~/.codex/context-server/index/skill-registry.json` (generated, refreshed)
- tests: `tests/test_skill_trigger.py` (new)
- generated: `benchmarks/results/phase17-live-report.json`
- untracked historical probes preserved untouched: `benchmarks/feasibility/`, `benchmarks/phase16_analyze.py`, `benchmarks/phase17_analyze.py`, `benchmarks/probe_*`, `tests/test_feasibility.py`, `section_diag.obj`

# 18. Git State

HEAD `b18d97c`, branch `main`, no upstream. Dirty: `M skill/project-context/SKILL.md` + untracked artifacts listed above. Commit: **NO**. Push: **NO**.

# 19. Final Recommendation

**D. 平台 Discoverability 是主要阻塞，应停止 Skill 优化并记录 External Limitation.**

Rationale: the skill layer has now done everything it can (correct two-stage model, visible tool name, triggers/anti-triggers, legacy repositioning, synced copies, registry refresh, tests), yet natural adoption remains 0/9 while Phase 1.6 guided intent cues produced 28.6% candidate / 57.1% context-MCP adoption. That pattern localizes the remaining gap to the platform's skill-consultation/tool-discovery behavior, which this project must not modify. Next step, if pursued, should be a platform-level change (e.g., global instruction or tool-discovery improvement) followed by a re-run of this same smoke protocol. Do not proceed to Router V0.6, Ranking tuning, Metadata V0.1, Blind, or Reranker.
