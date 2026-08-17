# Value Pilot V1

Measures whether Candidate Retrieval produces incremental value over Native
Codex on real coding tasks, and whether a generic intent hint alone explains
that value. This is a product-value experiment, not algorithm tuning.

## Design

12 real coding tasks x 3 arms = 36 independent runs.

- T1 Localized x3: native is expected to suffice; observes workflow tax.
- T2 Source-discoverable x3: answer discoverable from source/tests.
- T3 Knowledge-helpful x3: docs/architecture/conventions materially help.
- T4 Knowledge-critical x3: project constraints exist; a runnable but wrong
  implementation must count as failure (P0 constraint compliance).

Arms:

- A Native Codex: task prompt only.
- B Intent-aware Native: task prompt + one generic project-knowledge hint.
- C Candidate-assisted: B + fixed statement that a candidate retrieval tool is
  available (identical for all C runs; no forced adoption).

## Files

- `tasks.json`: frozen ground truth (frozen before any run; hash in protocol).
- `protocol.json`: frozen protocol (arms, metrics, go/no-go, cost protection).
- `build_suite.py`: isolated per-run workspace clones (same commit per task).
- `runner.py`: materializes per-run prompts and task copies.
- `analyze.py`: stable result schema + summary helpers.
- `tests/test_value_pilot_protocol.py`: integrity checks on frozen files.

## Execution

1. `python benchmarks/value_pilot/build_suite.py`
2. `python benchmarks/value_pilot/runner.py`
3. One context-free Codex sub-agent per run (fork_turns=none), serial, cwd =
   the run workspace, working only inside it.
4. Score each run from transcript + workspace diff + validation commands.

Results live under `benchmarks/results/pilot-runs/` (gitignored; never
committed).

## Metrics priority

P0 constraint compliance > P1 task success > P2 rework > P3 exploration cost >
P4 tokens > P5 latency. Go/no-go thresholds are frozen in `protocol.json`.
