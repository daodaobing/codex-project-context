# Benchmark (V0.1 / V0.2)

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

The runner uses the existing `server.get_project_context(project_path, task)`
algorithm without benchmark-only parameters or LLM/OpenAI calls. It uses a
temporary index under the ignored workspace directory, so the normal project
index is not changed.

Each invocation preserves raw JSON, CSV, and summary output under
`benchmarks/results/runs/<run-id>/` and mirrors the latest run at
`benchmarks/results/`. The raw record includes the pinned commit verification,
definition hashes, task text, selected paths, route output, and measured
latencies, but not source code.

Validation outputs use `benchmarks/results/validation/` so they cannot overwrite
the diagnostic latest result.

## Metrics

The full-context baseline is all indexed entry files (`AGENTS.md` and
`README.md` when present) plus all indexed Markdown files under `docs/`. The
selected context is the unique `relevant_files` list returned by the current
matcher.

- Context reduction: `1 - selected document characters / full document characters`.
- File reduction: `1 - selected document count / full document count`.
- Required-document recall: required paths returned / required paths defined.
- Precision: selected paths that are required or acceptable / selected paths.
- Cold scan latency: one forced scan per repository.
- Cached routing latency: one route call per task after the scan.

Characters, UTF-8 bytes, and document counts are reported explicitly. Latency
percentiles use a deterministic nearest-rank p95 calculation. No token savings
or model-quality claim is inferred from these measurements.

## Limitations

This is a 15-task, human-authored sample, not a statistical evaluation of all
OSS projects. The Gin snapshot exposes one indexed Markdown guide, so its five
tasks are intentionally a small routing sanity check rather than a broad
documentation corpus. Ground truth reflects documentation relevance, not
source-code navigation quality or end-to-end Codex token usage. Network
availability is needed only when a pinned workspace is not already present;
runtime latency is machine-dependent. Results must be read together with the
original repository test suite.
