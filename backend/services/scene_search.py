"""Scene search over the satellite-image archive (intranet disk array).

Defines the search interface and provides two backends behind it:

  * disk — walk a real directory (set SR_SCENES_ROOT) for TIFF/IMG/JPG files and
    parse scene metadata (satellite, sensor, date) from the filename. This is
    the real-array shape; pointing the root at the array mount is all the
    "integration" needed.
  * fake — deterministic synthetic rows used on the dev machine where the
    array is unreachable. Fake rows carry `fake: True` so callers never
    mistake placeholder paths for real files.

Filename convention (matches SR_code, e.g.
JL1KF02B03_PMS05_20260722125045_200524168_102_0034_001_L1_PAN_mask.txt):
the first `_`-separated parts are satellite and sensor, and the first
8..14-digit run is the acquisition timestamp (YYYYMMDD...). Unknown parts → None.

What counts as a scene (disk backend) is a whitelist, not a blacklist: the file
must sit in a *scene directory* — one containing `<dirname>_meta.xml` — and be
named either `<dirname>.<ext>` (the SC step's input) or `PAN.<ext>` (the RC
step's). Everything else in a scene directory is something else's input or
output: SR products and the input backup (_sr/_NOSR/_ori), the cloud map
(_cloud), thumbnails (_thumb), the ROI mask (_mask — it is an *input* to
"submit SR", not a scene), the backend's own `<stem>.preview.jpg` cache, and the
dozens of debug renders under `Debug/`. See is_scene_file.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Raster extensions treated as scenes (TIFF primary; IMG common on the array).
# 有序元组是给 input_candidates 用的（"试过哪些"的诊断信息要求顺序稳定）；
# 集合供成员测试。
_RASTER_EXT_ORDER = (".tif", ".tiff", ".img")
_RASTER_EXTS = set(_RASTER_EXT_ORDER)
# 最小原型 §4.7：盘阵目录里预生成的 JPG 也要能列出并直接打开（8bit 显示就绪，
# 不需要后端再烘焙预览）。真值见 docs/status/phase4 —— 盘阵读 JPG 是既有做法。
_IMAGE_EXTS = {".jpg", ".jpeg"}
_SCENE_EXTS = _RASTER_EXTS | _IMAGE_EXTS
#: 场景目录的判据：目录里躺着一份 <目录名>_meta.xml。SR 脚本靠它判 RC/SC
#: （util.check_sr_previous_step），没有它的目录提交也跑不起来 —— 所以它同时也是
#: 「这个目录里的东西能不能提交」的判据。
_META_SUFFIX = "_meta.xml"

#: RC 步骤的输入文件名（util.get_l1_pan_tif_rcsc 的 RC 分支读的就是 PAN.tif）。
_RC_INPUT_STEM = "pan"

_TS_RE = re.compile(r"\d{8,14}")


def is_scene_dir(path) -> bool:
    """该目录是不是一个可提交的场景目录（内含 <目录名>_meta.xml）。"""
    d = Path(path)
    return d.is_dir() and (d / (d.name + _META_SUFFIX)).is_file()


def is_scene_file(path) -> bool:
    """该文件是否算一个可列出的盘阵场景。

    白名单，两条同时成立：所在目录是场景目录（is_scene_dir），且文件名要么等于目录名
    （SC 步骤的输入 `<目录名>.<ext>`），要么是 `PAN.<ext>`（RC 步骤的输入）。

    这样一次挡住全部「别的东西的输入/产物」：SR 产物与输入备份（_sr/_NOSR/_ori）、
    云量图（_cloud）、缩略图（_thumb）、提交 SR 的输入掩膜（_mask）、后端自己烘焙的
    `<stem>.preview.jpg` 缓存，以及 Debug/ 下十几张调试图。此前用的是黑名单，每冒出
    一类新派生件就得补一条 —— 2026-09-15 真机接上盘阵时，18 行里有 16 行是这种脏数据。
    """
    p = Path(path)
    if not p.is_file():
        return False
    if p.suffix.lower() not in _SCENE_EXTS:
        return False
    if not is_scene_dir(p.parent):
        return False
    stem = p.stem.lower()
    return stem == p.parent.name.lower() or stem == _RC_INPUT_STEM


def input_candidates(dir_path) -> list[Path]:
    """场景目录里输入影像的候选路径（有序，恰好 6 个）。

    顺序即优先级：SC 步骤读 `<目录名>.<ext>`，RC 步骤读 `PAN.<ext>`；扩展名按
    `_RASTER_EXT_ORDER`。**只拼名字，不 stat、不列举** —— 调用方自己挑第一个
    存在的，或者把整串拿去做"试过哪些"的诊断信息。
    """
    d = Path(dir_path)
    return [d / f"{stem}{ext}"
            for stem in (d.name, _RC_INPUT_STEM.upper())
            for ext in _RASTER_EXT_ORDER]


def input_scene_path(dir_path) -> Path | None:
    """该场景目录里的输入影像（= 提交 SR 时的 `lq_path` 指向的那份文件）。

    与 `is_scene_file` 同一套判据，但**只试 6 个固定文件名、绝不列举目录**
    （盘阵数据量大，任何形式的目录扫描都不可接受）。目录不是场景目录（缺
    `<目录名>_meta.xml`）、或 6 个候选一个都不在 → None。

    这里的判据同时决定两件事：能不能打开/提交，以及掩码该叫什么名字
    （见 `api/platform.derived_mask_path`）—— 两处必须同源，否则 PAN
    场景会写成 `PAN_mask.tif` 而提交时找的是 `<目录名>_mask.tif`。
    """
    if not is_scene_dir(dir_path):
        return None
    for cand in input_candidates(dir_path):
        if cand.is_file():
            return cand
    return None


def mask_stem(lq_path) -> str:
    """掩码文件名的主干：**输入影像的 stem**，不是目录名。

    SC 场景里两者相同（`<目录名>.tif` 躺在以目录名命名的目录里），RC 场景里
    不同（输入叫 `PAN.tif`）。以前 `derived_mask_path` 取目录名、`bake_mask`
    取输入文件名，RC 场景下掩码写进去叫 `PAN_mask.tif`、提交时却去找
    `<目录名>_mask.tif` → 必然 400。`<MaskPath>` 是平台自己写进配置 XML 的
    （services/run_sr.py），SR 脚本只照读，所以只要这两处同源即可。

    目录不构成场景目录（缺 meta.xml）时退回目录名：`derived_mask_path`
    也服务于"目录里手工放了掩码"的诊断，不该因为拿不到输入文件就变空。
    """
    d = str(lq_path).replace("\\", "/").rstrip("/") or "/"
    inp = input_scene_path(d)
    return inp.stem if inp is not None else d.rsplit("/", 1)[-1]


def derived_mask_path(lq_path) -> str:
    """场景目录该带的掩码：`<输入名>_mask.tif`，与影像同目录。

    这就是 `POST /api/masks` 写出去的那份，也是提交 SR 时去找的那份；不传
    `mask_path` 时由它推导（REST 与 agent 工具两个入口共用 —— 各自推一份的话
    同一次提交会算出两个 `task_fingerprint`，幂等层失效、重复投作业）。

    不用 os.path.join、且把反斜杠一并转正：这条路径会被存进 params、参与
    `task_fingerprint`，还必须在「resolve 告诉前端的掩码路径」与「提交侧自己
    推导的掩码路径」之间逐字节相同，不能随宿主平台（谁的分隔符）变。传进来的
    lq_path 可能还是宿主形态（`str(Path(...))` 在 Windows 上带反斜杠）。
    """
    base = str(lq_path).replace("\\", "/").rstrip("/")
    return base + "/" + mask_stem(lq_path) + "_mask.tif"


# --------------------------------------------------------------------------
# SR 三类图（输入影像 / 本次产物 / 上一次产物）的命名推导
# --------------------------------------------------------------------------
#: 产物的扩展名候选。SR 侧 `writeTiff(result_sr, lq_path + "/" + img_name[0:-4]
#: + "_" + suffix, tiftype=tiftype, ...)` 里的 `tiftype` 是配置给定值，生产上是
#: `.tif`；`.tiff` 一并试是因为盘阵上两种拼写都出现过。加名字是一行的事。
_PRODUCT_EXT_ORDER = (".tif", ".tiff")


def sibling_raster_path(image_path) -> Path | None:
    """与 `image_path` **同目录同名**的栅格文件（只换后缀），没有则 None。

    用来认「盘阵里的显示件 jpg 配着一张同名栅格」：SC 场景里 `<目录名>.jpg` 配
    `<目录名>.tif`，RC 场景里 `PAN.jpg` 配 `PAN.tif`。按 `_RASTER_EXT_ORDER` 拼
    候选名逐个 `is_file()`，**只拼名字、不列举目录**（与 input_candidates 同一纪律）。

    `.hdr` 之类伴随文件不参与：调用方要的是能拿去烘焙像素的栅格。
    """
    p = Path(image_path)
    for ext in _RASTER_EXT_ORDER:
        cand = p.with_suffix(ext)
        if cand.is_file():
            return cand
    return None


def product_candidates(input_path, suffix: str) -> list[Path]:
    """本次 SR 产物的候选路径（有序，恰好 `len(_PRODUCT_EXT_ORDER)` 个）。

    真源是 `SR_code/code_0817_prod.py`：`img_name = basename(输入影像)`，
    `writeTiff(..., lq_path + "/" + img_name[0:-4] + "_" + suffix, ...)` —— 产物就
    躺在**输入影像的目录**里，名字是「输入名去掉最后 4 个字符 + 下划线 + suffix」。

    **必须字面切片 `[:-4]`，不能用 `Path.with_suffix`**：`verify_sr_run.py::
    output_path_for` 用的也是 `img_name[:-4]`，而输入名是 `a.tiff` 时
    `with_suffix("")` 得 `a`、字面切片得 `a.ti` —— 两者不等，用 with_suffix 会在
    `.tiff` 场景下永远算出「产物不存在」。这里的字符串运算与 SR 侧同源，不要
    「顺手改漂亮」。

    只拼名字，**不 stat**：调用方自己挑第一个存在的，或把整串拿去做「试过哪些」的
    诊断信息（`GET /api/scenes/{id}/siblings` 就是这么用的）。
    """
    p = Path(input_path)
    base = p.name[:-4]
    return [p.parent / f"{base}_{suffix}{ext}" for ext in _PRODUCT_EXT_ORDER]


def nosr_path_for(product_path) -> Path:
    """上一次产物的路径：`<产物 stem>_NOSR<ext>`，与产物同目录。

    对齐 `SR_code/util.py::writeTiff` 的改名规则 —— 它改的是**输出路径**
    （`os.rename(path + tiftype, path + "_NOSR" + tiftype)`），改名的前提是目标
    已存在。所以这个文件：

      * 是**上一次**同一 suffix 的产物，**不是**输入影像的备份（输入影像全程不动）；
      * 只在同一 suffix 跑过**两次以上**时才存在 —— 首跑那次 rename 撞
        `FileNotFoundError` 被 `except` 吞掉，什么都不留下。

    调用方不能把它当成恒定存在的第三项。特例 `_ori`（输出名恰好等于 `PAN`、即空
    suffix 跑 RC）走不到：平台侧 `run_sr.normalize_suffix` 保证 suffix 非空。
    """
    p = Path(product_path)
    return p.with_name(p.stem + "_NOSR" + p.suffix)


def parse_filename(path) -> dict:
    """Extract {satellite, sensor, date} from a scene filename; unknown → None."""
    parts = Path(path).stem.split("_")
    sat = parts[0] if parts else None
    sensor = parts[1] if len(parts) > 1 else None
    date = None
    ts = next((p for p in parts if _TS_RE.fullmatch(p)), None)
    if ts:
        try:
            datetime.strptime(ts[:8], "%Y%m%d")
            date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
        except ValueError:
            date = None
    return {"satellite": sat, "sensor": sensor, "date": date}


def scan_root(root) -> list[dict]:
    """Recursively list raster scenes under root with parsed metadata."""
    scenes = []
    if not root:
        return scenes
    base = Path(root)
    if not base.is_dir():
        return scenes
    for p in sorted(base.rglob("*")):
        if is_scene_file(p):
            meta = parse_filename(p)
            scenes.append({**meta, "id": p.stem, "path": str(p),
                           "size_bytes": p.stat().st_size, "fake": False})
    return scenes


def fake_scenes(n=12) -> list[dict]:
    """Deterministic synthetic scene rows for local testing (fake=True)."""
    combos = [
        ("GF07A03", "PMS01"), ("KF02B04", "PMS05"), ("JL1KF02B03", "PAN"),
        ("GF04", "PMS02"), ("ZY302", "MUX"), ("GF07A03", "PAN"),
    ]
    dates = ["20260722", "20260723", "20260724", "20260801", "20260805", "20260810"]
    scenes = []
    for i in range(n):
        sat, sensor = combos[i % len(combos)]
        # date advances every len(combos) rows → no duplicate scene ids
        ts = f"{dates[(i // len(combos)) % len(dates)]}120000"
        sid = f"{sat}_{sensor}_{ts}"
        scenes.append({"id": sid, "path": f"<fake>/{sid}.tif",
                       "satellite": sat, "sensor": sensor,
                       "date": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}",
                       "size_bytes": 0, "fake": True})
    return scenes


def search_scenes(root, query="", satellite=None, sensor=None, date_from=None,
                  date_to=None, limit=20):
    """Search the archive; returns {"source", "scanned", "count", "results"}.

    root — real directory (disk backend) or None/not-a-dir (falls back to fake).
    query — case-insensitive substring on scene id/path.
    satellite — case-insensitive substring on the parsed satellite id.
    sensor — case-insensitive substring on the parsed sensor id.
    date_from/date_to — inclusive "YYYY-MM-DD" bounds; scenes without a parsed
    date are excluded once a bound is given.
    """
    base = Path(root) if root else None
    if base and base.is_dir():
        scenes = scan_root(root)
        source = "disk"
    else:
        scenes = fake_scenes()
        source = "fake"

    q = (query or "").strip().lower()
    sat = (satellite or "").strip().lower()
    sen = (sensor or "").strip().lower()

    def keep(s):
        if q and q not in s["id"].lower() and q not in s["path"].lower():
            return False
        if sat and sat not in (s.get("satellite") or "").lower():
            return False
        if sen and sen not in (s.get("sensor") or "").lower():
            return False
        if s.get("date"):
            if date_from and s["date"] < date_from:
                return False
            if date_to and s["date"] > date_to:
                return False
        elif date_from or date_to:
            return False
        return True

    matched = [s for s in scenes if keep(s)]
    matched.sort(key=lambda s: (s.get("date") or "", s["id"]), reverse=True)
    return {"source": source, "scanned": len(scenes), "count": len(matched),
            "results": matched[:limit]}
