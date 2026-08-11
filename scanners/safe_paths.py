"""安全的项目内路径解析工具。

所有来自 manifest、扫描配置或项目文档的相对路径都必须经过这里解析。
解析同时检查词法上的绝对/越界路径和真实文件系统路径，避免目录 symlink
把读取或写入带到项目根目录之外。
"""

from __future__ import annotations

import ntpath
import os
import posixpath
from pathlib import Path, PurePosixPath, PureWindowsPath


def normalize_relative_path(candidate: object) -> str | None:
    """规范化一个 manifest 路径，拒绝绝对路径和词法越界。"""

    try:
        raw = os.fspath(candidate)
    except TypeError:
        return None
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or "\x00" in raw:
        return None

    # 在 Windows 和 Unix 上都拒绝绝对路径；PureWindowsPath 让 Unix CI
    # 也能识别 C:\\... 和 \\server\\share 形式的 Windows 路径。
    normalized = raw.replace("\\", "/")
    if (
        Path(raw).is_absolute()
        or PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(raw).is_absolute()
        or ntpath.splitdrive(raw)[0]
    ):
        return None

    relative = posixpath.normpath(normalized)
    if relative in ("", ".", "..") or relative.startswith("../"):
        return None
    return relative


def resolve_safe_path(root: Path, candidate: object) -> Path | None:
    """解析项目内路径；真实路径越出 root 时返回 ``None``。

    ``strict=False`` 允许调用方检查尚不存在的合法目标，同时仍会解析已
    存在的父目录或文件 symlink。调用方在读取/写入前仍应检查 is_file/is_dir。
    """

    relative = normalize_relative_path(candidate)
    if relative is None:
        return None
    try:
        project_root = Path(root).resolve()
        target = project_root / Path(relative)
        resolved = target.resolve(strict=False)
        resolved.relative_to(project_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved


def safe_relative_path(root: Path, candidate: object) -> str | None:
    """返回可安全保留在索引中的项目相对路径，否则返回 ``None``。"""

    relative = normalize_relative_path(candidate)
    if relative is None or resolve_safe_path(root, relative) is None:
        return None
    return relative
