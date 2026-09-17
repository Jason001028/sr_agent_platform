"""临时预览 JPG 缓存（拖拽入口专用）—— 独立目录 + 日期分桶 + 次日 0 点清除。

为什么单独一个模块：预览缓存有**两种**生命周期，混在一起会互相咬。

* **长期缓存**（场景库 / 「盘阵场景」栏打开）：语义在
  `api/paths.py::preview_jpg_path` —— 落在源同目录或 `SR_PREVIEWS_ROOT` 的
  镜像树下，跟着场景数据长期存在。烤一份要读一遍大图（真机几十秒），
  天天重烤对 HDD 是负担。
* **临时缓存**（用户把盘阵上的 .tif 拖进浏览器）：本模块。源可能是盘阵上
  **任意**一张图，不该往生产数据目录里撒文件，所以统一落独立目录、按
  `YYYY-MM-DD` 分桶、只保留当天那一桶。

**分桶而不是按 mtime 判过期**：这样「第二天 0 点清除」就是一次目录删除
（`rmtree` 整个过期桶），而不是逐文件比时间戳 —— 后者在跨时区/时钟回拨时
容易算错，也慢。

**清理的判据不止「目录名像日期」**：桶里必须**有本模块写的标记文件**才删。
否则一旦 `SR_TEMP_PREVIEWS_ROOT` 被指到一个恰好有 `2026-09-16` 这类子目录的
数据盘上，清理就成了灾难。标记文件把「这个目录是我们建的」变成可判定的事实，
比任何路径白名单都可靠。

本模块**不 import `backend.api.paths`**（那个模块已经 import
`services.preview_jpg`，反向依赖会成环），只依赖标准库与零依赖的
`backend.pathguard`。

约定
----
SR_TEMP_PREVIEWS_ROOT 临时预览缓存根。**生产必须显式配**：默认值是系统临时
                      目录，而 CentOS7 的 `/tmp` 常常是 tmpfs（内存盘），
                      1/2 尺度的预览单张可能上百 MB，写爆内存。见
                      deploy/README.md。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

from backend.pathguard import is_within

#: 日期桶名。也是清理时**唯一**会匹配的目录形态。
_BUCKET_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 桶内的标记文件 —— 清理只删带它的桶（见模块 docstring）。
MARKER_NAME = ".sr-tmp-preview"

__all__ = ["MARKER_NAME", "purge_temp_previews", "temp_previews_root",
           "tmp_preview_path"]


def temp_previews_root() -> Path:
    """临时缓存根。**每次调用读 env**（测试要能按请求换根，别在 create_app 快照）。"""
    raw = os.environ.get("SR_TEMP_PREVIEWS_ROOT")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "sr-tmp-previews"


def _bucket_name(now: date | datetime | None = None) -> str:
    """当天桶名（`YYYY-MM-DD`，本地日期）。`now` 可注入以便测试跨天行为。"""
    if isinstance(now, datetime):
        return now.date().isoformat()
    return (now or date.today()).isoformat()


def tmp_preview_path(source_abs: str | Path,
                     now: date | datetime | None = None) -> Path:
    """源影像 → 临时预览图路径：`<root>/<YYYY-MM-DD>/<sha256(abs)[:16]>.jpg`。

    只 `mkdir`，**绝不列举目录** —— `tests/test_scene_resolve.py` 的
    `TestNeverListsDirectories` 把 `os.listdir/scandir/walk` 与
    `Path.rglob/glob/iterdir` 全打成了 AssertionError，任何列表调用都会在
    请求里炸掉。这里连「桶在不在」都只用 `exists()`（stat）判断。

    哈希**只取绝对路径、不掺 mtime**：缓存新鲜度由
    `preview_jpg.ensure_preview_jpg` 自己的 mtime + 规则戳判定，本函数只保证
    「同一个源在当天的桶里落在同一个文件名上」。
    """
    root = temp_previews_root()
    bucket = root / _bucket_name(now)
    if not bucket.exists():
        bucket.mkdir(parents=True, exist_ok=True)
        try:
            (bucket / MARKER_NAME).touch()
        except OSError:
            pass        # 标记写不上不该挡住看图；代价只是这个桶不会被自动清理
    key = hashlib.sha256(Path(source_abs).as_posix().encode("utf-8")).hexdigest()
    return bucket / (key[:16] + ".jpg")


def _refuse_reason(root: Path) -> str | None:
    """拒绝清理的配置错误（宁可不删，也不删错）。返回原因，None = 可以清。"""
    try:
        if root.is_symlink():
            return f"根是符号链接：{root}"
        resolved = root.resolve()
        if resolved == Path(resolved.anchor):
            return f"根指向文件系统根：{root}"
    except OSError as e:
        return f"根无法解析：{e}"
    return None


def purge_temp_previews(now: date | datetime | None = None) -> list[str]:
    """删掉所有过期日期桶（名 < 今天 **且带标记文件**），返回被删的桶名。

    启动时与每天 0 点各跑一次（`api/app.py::_tmp_preview_purge_loop`）。
    **不要在请求路径里调用** —— 它要 `iterdir` 根目录，会踩上面那条
    「禁止扫盘」的测试钉子。

    任何异常都吞掉并返回已删列表：清理失败是运维问题，不该让服务起不来，
    更不该让清理循环自己死掉。幂等。
    """
    removed: list[str] = []
    root = temp_previews_root()
    today = _bucket_name(now)
    try:
        if _refuse_reason(root) or not root.is_dir():
            return removed
        for entry in root.iterdir():
            name = entry.name
            # 桶名是 ISO 日期，字符串比较即时间比较；今天的与未来的一律留着
            if not _BUCKET_RE.match(name) or name >= today:
                continue
            if entry.is_symlink() or not entry.is_dir():
                continue            # rmtree 会跟着符号链接走，先挡掉
            if not (entry / MARKER_NAME).is_file():
                continue            # 不是我们建的目录，一个字节都不碰
            if not is_within(entry, root):
                continue
            shutil.rmtree(entry)
            removed.append(name)
    except Exception:               # noqa: BLE001 — 清理失败不向外抛
        pass
    return removed
