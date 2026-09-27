"""人工清除预览缓存 —— 场景库「清除选定 / 全部清除」的服务端一半。

背景：预览 JPG 由三条链写入（打开时的惰性烘焙 / 拖入链 / 超分跑完后的急烤），
**只增不减**。换档位或换烘焙规则之后想立刻看新效果、或者单纯想回收盘阵空间，
此前都没有任何入口 —— 只能逐个场景点开等它惰性重烤。本模块提供「按场景目录清掉
预览」这一步，由 `api/app.py::clear_scene_previews` 端点调用。

**判据（宁可不删，也不删错）** —— 一个文件要被删，必须同时满足：

1. 就在传进来的这个目录**这一层**（不递归、不扫根）；
2. 名字是 `<stem>_preview.jpg`，或是改名前的点号件 `<stem>.preview.jpg`
   （2026-09-22 起预览只有一个名字，点号那份是同一份缓存的旧名，与三条链上
   `app._sweep_legacy_preview` 的口径一致）；
3. **JPEG 注释带本平台的规则戳**（`preview_jpg.has_rule_stamp`，只判 `srprev:`
   前缀，v1/v2/v3 三代都认）。读不出戳的一律不碰 —— 盘阵上别人手放的同名图、
   以及万一后缀恰好叫 `preview` 的**产物**，都没有这个戳；
4. 不是符号链接、是普通文件、且 `scene_search.is_scene_file` 不认它是场景源。

3 与 4 是两道独立的锁：3 防「误删不是我们写的文件」，4 防「目录名以 `_preview`
结尾时把场景源自己删了」。

**与「后端不列目录」那条红线的关系**：`tests/test_scene_resolve.py::
TestNeverListsDirectories` 把 `os.listdir/scandir/walk` 与 `Path.glob/rglob/iterdir`
全打成 AssertionError，钉的是**路径解析与取图**那条链（每个请求都跑、盘阵目录又大）。
本模块是**用户按下的删除动作**，必须知道目录里有什么才谈得上删，所以它列
`iterdir` —— 但只列**用户点名的那一个场景目录**这一层（外加镜像树里对应的那一层），
不递归、不扫 `SR_SCENES_ROOT`。两条链的取舍不同，别把这条纪律推广到解析链上。

**并发**：本模块不看数据库，也不知道急烤循环正在做什么。与急烤的竞态由调用方处理
（先把 `preview_state` 标成 `cleared` 挡住后续认领，再删）—— 但那只挡住**后续**认领：
恰好在我们列举与删除之间抢到认领的那一件，仍可能把文件再写回来一次。这个残余窗口
无法在此模块内消除，调用方在响应里如实呈现「删了之后文件又出现」的可能。

约定
----
* 只依赖标准库、`backend.pathguard`、`backend.services.scene_search` 与
  `preview_jpg`（后两个都不 import `api/`，不成环）。**不 import `api/paths.py`**。
* 镜像树（`SR_PREVIEWS_ROOT`）的目录由调用方算好传进来，本模块不认识那个 env。
* 空目录**不删**：镜像树骨架留着，下次烘焙直接落进去。
"""

from __future__ import annotations

from pathlib import Path

from backend.pathguard import is_within
from backend.services import scene_search
from backend.services.preview_jpg import has_rule_stamp

#: 预览文件名的两种形态（新名 / 改名前的点号名）。判据用 `lower()`：
#: 盘上真名是小写，但大小写混写的同名件同样是缓存，没有理由放过。
_NAME_NEW = "_preview.jpg"
_NAME_LEGACY = ".preview.jpg"

__all__ = ["is_preview_name", "refusal_reason", "clear_dir_previews"]


def is_preview_name(name: str) -> bool:
    """这个名字是不是本平台的预览件（两种形态之一）。纯词法。"""
    low = str(name).lower()
    return low.endswith(_NAME_NEW) or low.endswith(_NAME_LEGACY)


def refusal_reason(entry: Path) -> str | None:
    """这个文件**不能删**的原因；None = 可以删。

    调用方只在名字像预览件时调它（否则场景目录里每个 `.tif` 都会带回一条原因，
    明细会被噪声淹掉）。
    """
    if entry.is_symlink():
        return "符号链接，不跟着删"
    if not entry.is_file():
        return "不是普通文件"
    if scene_search.is_scene_file(entry):
        return "是场景源文件（is_scene_file），不是缓存"
    if not has_rule_stamp(entry):
        return "没有本平台的规则戳（srprev:），不是这里烤出来的"
    return None


def clear_dir_previews(directory) -> dict:
    """清掉这个目录**这一层**里的预览件。目录不存在 → 三个列表都空。

    返回 `{"dir", "removed", "skipped", "failed"}`，其中 `removed` 是删掉的文件名，
    `skipped` / `failed` 是 `(名字, 原因)`：

    * **skip** = 按判据不该删（不是我们的件 / 是场景源 / 符号链接）；
    * **fail** = 该删但删不掉（权限、被占用、目录读不了）—— 真机上服务账号对盘阵
      目录可能没有写权限，这条要如实往上带，不能吞掉、更不能整批放弃。

    进入删除清单是**逐文件**判的，所以一个目录里「删掉两份、跳掉一份」是常态。
    """
    d = Path(directory)
    out = {"dir": d.as_posix(), "removed": [], "skipped": [], "failed": []}
    if not d.is_dir():
        return out
    try:
        entries = list(d.iterdir())
    except OSError as e:
        # 目录存在但读不了（权限）：整份场景算失败，别报成「无需清除」——
        # 后者会让用户以为盘上本来就没什么可清的。
        out["failed"].append((d.name, f"目录无法列举：{type(e).__name__}: {e}"))
        return out
    for entry in entries:
        if not is_preview_name(entry.name):
            continue
        if not is_within(entry, d):
            out["skipped"].append((entry.name, "路径不在本目录下"))
            continue
        why = refusal_reason(entry)
        if why is not None:
            out["skipped"].append((entry.name, why))
            continue
        try:
            entry.unlink()
        except OSError as e:
            out["failed"].append((entry.name, f"{type(e).__name__}: {e}"))
        else:
            out["removed"].append(entry.name)
    return out
