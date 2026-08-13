"""Opt-in, local-only benchmark instrumentation.

Default (normal users): OFF.  Nothing is recorded and no network, telemetry,
analytics, or remote logging is ever performed.

Enable explicitly by setting ``PROJECT_CONTEXT_BENCHMARK_RUN_ID`` and
``PROJECT_CONTEXT_BENCHMARK_TASK_ID``.  Events are appended as JSONL to a local
git-ignored directory (``PROJECT_CONTEXT_BENCHMARK_DIR``, falling back to the
project ``benchmarks/results/instrumentation/`` tree).

The event records the minimum needed to prove ``get_context_candidates`` was
actually called; it never records source code or full document contents.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


ENV_RUN_ID = "PROJECT_CONTEXT_BENCHMARK_RUN_ID"
ENV_TASK_ID = "PROJECT_CONTEXT_BENCHMARK_TASK_ID"
ENV_DIR = "PROJECT_CONTEXT_BENCHMARK_DIR"

# Fields that must never be persisted even if a caller passes them by mistake.
_DENYLIST = {"source_content", "document_content", "full_text", "content", "task_text"}


def enabled() -> bool:
    """True only when both benchmark identifiers are explicitly set."""

    return bool(os.environ.get(ENV_RUN_ID)) and bool(os.environ.get(ENV_TASK_ID))


def output_dir() -> Path:
    explicit = os.environ.get(ENV_DIR)
    if explicit:
        return Path(explicit)
    root = Path(__file__).resolve().parents[1]
    preferred = root / "benchmarks" / "results" / "instrumentation"
    # Runtime installs do not ship the benchmarks tree; fall back to a local,
    # still-ignored directory under the source root.
    if preferred.parent.parent.is_dir():
        return preferred
    return root / ".instrumentation"


def record_event(event: dict[str, Any]) -> None:
    """Append one JSONL event when instrumentation is enabled.

    Fail-open and quiet: instrumentation errors must never break the tool.
    """

    if not enabled():
        return
    try:
        directory = output_dir()
        directory.mkdir(parents=True, exist_ok=True)
        event = {key: value for key, value in event.items() if key not in _DENYLIST}
        event["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return
