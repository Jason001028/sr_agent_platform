"""测试包共享夹具。"""

from __future__ import annotations

from pathlib import Path


def allowed_roots_env(*roots) -> dict:
    """把给定目录配成盘阵前缀白名单，返回要注入的环境变量。

    开发机是 Windows：临时目录带盘符（`C:\\Users\\...`），而盘阵路径一律经
    `pathguard.to_posix_array_path` 归一 —— 不补一条"该盘符映射到自身"的规则，
    本机绝对路径会因「未知盘符」被拒。Linux 上 `Path.drive` 为空，这条例外
    自然不生效。

    返回 dict 而不是直接改 `os.environ`：调用方可能是 `mock.patch.dict`，
    那种用法下事后改的键不会被还原。
    """
    env = {"SR_ALLOWED_ROOTS": ";".join(str(r) for r in roots)}
    drives = {Path(r).drive for r in roots}
    drives.discard("")
    if drives:
        env["SR_DRIVE_MAP"] = ";".join(f"{d}={d}" for d in sorted(drives))
    return env
