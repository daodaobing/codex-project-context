# Benchmark (V0.1 / V0.2 / V0.3 / V0.5)

This benchmark measures the current project-context routing algorithm on a small,
fixed public-OSS dataset. It is intentionally separate from the matcher and does
not change production behavior.

The original V0.1 result is preserved as
`baselines/v0.1-summary.json` (`baseline-v0.1`). The root definition files are
the V0.2 diagnostic set. Axios and Catch2 were originally used as an
independent holdout during Router V0.2, but their results have now been
inspected. They are therefore treated as a validation set for V0.3 and are not
used as the final blind evaluation. The frozen validation definitions live
under `validation/` and are not used as the final blind evaluation.

For Router V0.3 a fresh blind holdout was introduced under `validation-v0.3/`. It pins
two repositories that were never consulted while developing the V0.3 router:

- `astral-sh/ruff` (Rust, MIT) at `d08b174e09a23c0a0413b7e7db7dc67d69593eac`
- `encode/httpx` (Python, BSD-3-Clause) at `b5addb64f0161ff6bfe94c124ef76f6a1fba5254`

The five tasks per repository were frozen from the pinned file trees before any
holdout routing run (`"dataset": "blind-holdout-v0.3"`, `"frozen": true`). The
holdout was executed exactly once, after the V0.3 router implementation was
locked, and the router is intentionally not tuned against these results.

> Ruff and HTTPX were used as the blind holdout for Router V0.3. Their results
> have now been inspected, so they are treated as a validation set for
> subsequent router development and are never reused as a blind evaluation.

## Dataset

`repos.json` pins three public repositories to immutable commit SHAs:

- `pallets/click` (Python)
- `encode/uvicorn` (Python)
- `gin-gonic/gin` (Go)

`tasks.json` contains five manually written tasks per repository. Each task has
`required_docs` (documents that should be loaded) and `acceptable_docs` (related
documents that are also valid). The file is marked `frozen` and must be reviewed
before running the benchmark; its ground truth is never generated from matcher
output.

The repositories are cloned into `benchmarks/workspaces/`, and no repository
source is copied into the benchmark source tree or result files. Workspaces and
generated results are ignored by Git.

## Reproduction

From the formal repository root:

```text
python benchmarks/run_benchmark.py --run-id run-1
python benchmarks/run_benchmark.py --run-id run-2
```

The same runner accepts `--dataset validation`; the V0.2 validation command was
executed once after the frozen definitions were created:

```text
python benchmarks/run_benchmark.py --dataset validation --run-id v0.2-validation-final
```

The V0.3 blind holdout was executed once after the router was locked:

```text
python benchmarks/run_benchmark.py --dataset validation-v0.3 --run-id v0.3-holdout-final
```

The runner uses the same `ContextMatcher` implementation used by
`server.get_project_context(project_path, task)`, enabling an explicit
diagnostic trace without changing production response fields. It makes no
LLM/OpenAI calls and uses a temporary index under the ignored workspace
directory, so the normal project index is not changed.

Router V0.5 ranking calibration uses the same four fixed datasets and three
ranking modes: `structural` (BM25 and coverage off), `bm25` (coverage off), and
`full` (the production ranking path). Run the reproducible ablation report with:

```text
python benchmarks/analyze_v05.py
```

The report records query/document tokens, repository-local IDF ordering, score
components, raw ranks, competitors, and score-scale percentiles under the
ignored `benchmarks/results/` tree. Selection remains the V0.4 hard-dedup,
threshold, role-coverage, diversity, and cap layer.

Each invocation preserves raw JSON, CSV, and summary output under
`benchmarks/results/runs/<run-id>/` and mirrors the latest run at
`benchmarks/results/`. The raw record includes the pinned commit verification,
definition hashes, task text, selected paths, route output, and measured
latencies, but not source code.

Validation outputs use `benchmarks/results/validation/` so they cannot overwrite
the diagnostic latest result. V0.3 holdout outputs use
`benchmarks/results/validation-v0.3/` for the same reason.

## Metrics

The full-context baseline is all indexed entry files (`AGENTS.md` and
`README.md` when present) plus all indexed Markdown files under `docs/`. The
selected context is the unique `relevant_files` list returned by the current
matcher.

- Context reduction: `1 - selected document characters / full document characters`.
- File reduction: `1 - selected document count / full document count`.
- Required-document recall: required paths returned / required paths defined.
- Precision: selected paths that are required or acceptable / selected paths.
- Required-document mean rank: mean raw pre-selection rank of required paths
  that are present in the indexed corpus. The raw rank is assigned before
  thresholding, hard deduplication, or soft-diversity selection.
- Required-document top-3/top-6 rate: fraction of ranked required paths whose
  raw rank is at most 3/6.
- Required-document MRR: mean reciprocal raw rank for required paths. Aggregate
  values are weighted by the number of ranked required paths.
- Cold scan latency: one forced scan per repository.
- Cached routing latency: one route call per task after the scan.

Characters, UTF-8 bytes, and document counts are reported explicitly. Latency
percentiles use a deterministic nearest-rank p95 calculation. No token savings
or model-quality claim is inferred from these measurements.

## Limitations

This is a 15-task diagnostic/validation sample plus a 10-task blind holdout,
all human-authored, not a statistical evaluation of all
OSS projects. The Gin snapshot exposes one indexed Markdown guide, so its five
tasks are intentionally a small routing sanity check rather than a broad
documentation corpus. Ground truth reflects documentation relevance, not
source-code navigation quality or end-to-end Codex token usage. Network
availability is needed only when a pinned workspace is not already present;
runtime latency is machine-dependent. Results must be read together with the
original repository test suite.
