"""Canonical document family normalization for diversity-aware routing.

The router keeps documents comparable across locale variants and filename
spellings without heavy language modeling.  A document family is a stable
key derived from its path: locale segments are removed, the filename stem is
normalized (lowercase, dash/underscore/camelCase), and the remaining non-locale
directory segments are kept as structural context.

The implementation never uses repository names or benchmark-specific feature
names, so it stays generic across public-OSS codebases.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath


# Common marketplace / locale single-segment names.  They are always treated
# as locale markers when they appear as a path segment, so
# ``docs/fr/request-config.md`` and ``docs/en/request-config.md`` share one
# canonical family.
LOCALE_SEGMENTS = frozenset(
    {
        "en",
        "en-us",
        "en-gb",
        "zh",
        "zh-cn",
        "zh-hans",
        "zh-hant",
        "zh-tw",
        "fr",
        "de",
        "es",
        "ja",
        "ko",
        "pt",
        "pt-br",
        "ru",
        "it",
        "ar",
        "nl",
        "pl",
        "tr",
        "vi",
        "uk",
        "cs",
        "sv",
        "id",
        "th",
    }
)

# Tiny generic software-engineering alias map.  Only universal concepts are
# included; benchmark-specific feature names are deliberately absent.
SW_ENGINEERING_ALIASES: dict[str, str] = {
    "config": "configuration",
    "auth": "authentication",
    "lifecycle": "lifespan",
    "cli": "commandline",
    "docs": "documentation",
}

_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_TOKEN_RE = re.compile(r"[a-z0-9]+|[^\x00-\x7f]+", re.IGNORECASE)


@staticmethod
def _split_filename_stem(stem: str) -> list[str]:
    """Split a filename stem into lowercase token pieces."""

    prepared = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", str(stem or ""))
    return [token.lower() for token in _TOKEN_RE.findall(prepared) if token]


def canonical_family(path: str) -> str:
    """Return a stable, locale-normalized family key for a document path.

    ``docs/en/request-config.md``, ``docs/fr/request-config.md`` and
    ``docs/zh-cn/request-config.md`` all collapse to the same family.  A file
    directly under ``docs/auth/configuration.md`` keeps a structural component
    so it does not collide with ``docs/request-config.md``.
    """

    if not path:
        return ""
    parts = [part for part in path.replace("\\", "/").split("/") if part]
    kept: list[str] = []
    for part in parts:
        if part in LOCALE_SEGMENTS:
            continue
        kept.append(part)
    if not kept:
        return ""
    filename = kept[-1]
    directory = kept[:-1]
    stem = PurePosixPath(filename).stem
    stem_tokens = _split_filename_stem(stem)
    if not stem_tokens:
        return "/".join(directory)
    normalized_stem = "-".join(stem_tokens)
    return "/".join([*directory, normalized_stem])


def locale_of(path: str) -> str:
    """Return the primary locale marker of a path, or an empty string."""

    for part in path.replace("\\", "/").split("/"):
        if part in LOCALE_SEGMENTS:
            return part
    return ""


@staticmethod
def _pluralize(token: str) -> str:
    """Very small plural-form normalizer used only inside alias matching."""

    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 2 and token.endswith("e") and not token.endswith("ss"):
        return token
    if len(token) > 2 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def alias_normalize(token: str) -> str:
    """Map a token to its canonical alias form, if one exists."""

    lowered = str(token or "").lower().strip()
    if not lowered:
        return lowered
    if lowered in SW_ENGINEERING_ALIASES:
        return SW_ENGINEERING_ALIASES[lowered]
    return lowered


def alias_similar(a_token: str, b_token: str) -> bool:
    """Return True when two tokens are alias-equivalent (post normalization)."""

    return alias_normalize(a_token) == alias_normalize(b_token)


def token_overlap_ratio(left: list[str], right: list[str]) -> float:
    """Jaccard-style overlap between two token lists, used for light penalties."""

    if not left or not right:
        return 0.0
    left_set = {alias_normalize(token) for token in left}
    right_set = {alias_normalize(token) for token in right}
    intersection = len(left_set & right_set)
    union = len(left_set | right_set)
    return intersection / union if union else 0.0
