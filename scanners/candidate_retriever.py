"""Candidate retrieval: raw RRF ranking -> bounded metadata candidates.

V0.1 product boundary: narrow the search space, never pick the final documents.
The returned pack is metadata-only and is built from the raw/full RRF ranking
before Selection (hard dedup / soft diversity / threshold / cap).
"""

from __future__ import annotations

from typing import Any


DEFAULT_LIMIT = 10
MIN_LIMIT = 1
MAX_LIMIT = 20


def validate_limit(limit: Any) -> int | None:
    """Return the normalized limit, or None when the value is invalid."""

    if isinstance(limit, bool) or not isinstance(limit, int):
        return None
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        return None
    return limit


def build_candidates(project: dict, task: str, limit: int) -> dict:
    """Build the metadata-only candidate pack for ``get_context_candidates``.

    Returns a stable dict; any ranking/validation failure is caught by the
    caller (the MCP tool) so this never raises into the transport.
    """

    from scanners.context_matcher import ContextMatcher

    settings = _load_settings()
    matcher = ContextMatcher(settings, ranking_mode="rrf")
    ranked = matcher.candidate_ranking(project, task, limit=limit)

    candidates: list[dict[str, Any]] = []
    for rank, (_, doc, reasons) in enumerate(ranked, start=1):
        candidates.append(
            {
                "path": str(doc.get("path") or ""),
                "title": str(doc.get("title") or doc.get("path") or ""),
                "summary": str(doc.get("summary") or ""),
                "role": str(doc.get("role") or ""),
                "rank": rank,
                "reasons": ContextMatcher.candidate_reasons(reasons),
            }
        )

    return {
        "project": str(project.get("project") or ""),
        "project_path": str(project.get("project_path") or ""),
        "project_type": project.get("project_type"),
        "task": task,
        "candidate_limit": limit,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def _load_settings() -> dict:
    # Local import keeps the module importable without a server import cycle.
    import server

    return server._load_settings()
