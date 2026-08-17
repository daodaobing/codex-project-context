# Incremental Value Pilot V1

## Goal

Measure whether Candidate Retrieval produces incremental value over native
Codex on real coding tasks, and whether a generic project-knowledge intent
hint alone explains that value. This is a product-value experiment, not
algorithm tuning.

## Protocol

Frozen A/B/C protocol across 12 real coding tasks x 3 arms = 36 independent
runs. See `protocol.json` (frozen) and `tasks.json` (frozen ground truth).

- **Arm A — Native Codex**: task prompt only.
- **Arm B — Intent-aware Native**: task prompt + one generic project-knowledge
  hint.
- **Arm C — Candidate-assisted**: Arm B + a fixed statement that a candidate
  retrieval tool is available (identical for all C runs; adoption is not
  forced).

## Arms

See `protocol.json` for exact prompt suffixes and treatment definitions.

## Task Classes

- **T1 Localized x3**: single-file changes where native Codex should suffice
  (observes workflow tax).
- **T2 Source-discoverable x3**: answers recoverable from source/tests.
- **T3 Knowledge-helpful x3**: project docs/conventions materially help.
- **T4 Knowledge-critical x3**: project constraints exist; a runnable but
  wrong implementation must count as failure.

## Dry Run

A 6-run dry run (2 tasks x 3 arms) was executed to validate the harness. The
three T1 runs completed and scored as valid controls. The three T4-2 runs
could not complete: the task implementations were produced and focused tests
passed, but every long-running T4 run was externally terminated before final
validation (full test suite) completed.

## Infrastructure Failure

Current result: **DRY_RUN_FAIL_INFRA**.

Reason: **EXECUTION_CARRIER_UNRELIABLE_FOR_LONG_RUNNING_KNOWLEDGE_CRITICAL_TASKS**.

Multiple long-running T4 runs were externally terminated before final
validation completed, under the available execution carrier (desktop-hosted
agent/exec sessions). Short tasks (3-5 minutes) completed reliably; long
tasks (10+ minutes) were repeatedly interrupted during the
post-implementation full-test phase. This is an infrastructure limitation,
not a product-value conclusion.

## What This Does NOT Prove

- NOT a product failure
- NOT a retrieval failure
- NOT an incremental-value failure
- NOT a task failure (task quality was not measurable)
- NOT a ground-truth or protocol failure

The Pilot cannot currently give a Go/No-Go verdict on the incremental value
of Project Context.

## Frozen Integrity

`tasks.json` and `protocol.json` are frozen and unchanged; hashes are recorded
inside `protocol.json` (`frozen_hashes`) and verified by
`tests/test_value_pilot_protocol.py`.

## Resume Conditions

The Pilot may resume only when all of the following hold:

- A stable execution carrier independent of a desktop session lifecycle
- Reliable completion of 15+ minute real coding tasks (including full tests)
- Complete transcript/telemetry capture
- Identical carrier for all A/B/C arms
- Unchanged frozen protocol

See `README.md` for the harness usage and `protocol.json` for the frozen
design.
