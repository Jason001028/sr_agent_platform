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

Dragging a jpg in is a separate, equally closed rule (2026-09-21): its name must cut
cleanly to the scene directory name plus one of the three stage tails, *and* that
stage's own raster must sit next to it — `<目录名>.jpg` / `<目录名>_<suffix>.jpg` /
`<目录名>_<suffix>_NOSR.jpg`. See stage_of_jpg. Nothing here ever lists a directory.
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


# --------------------------------------------------------------------------
# 拖进来的 jpg 属于哪个环节（2026-09-21：中间产物也要能关联到盘阵）
# --------------------------------------------------------------------------
#: 平台已知的**非环节**派生件尾段：云量图、缩略图、掩码、上一次的输入备份。
#: 真机上这些栅格可能真的存在，只靠下面的「同级栅格在」挡不住它们 —— 显式点名。
#: 与 is_scene_file 的黑名单不同，这里没有「每冒一类就得补一条」的负担：这个名单
#: 不决定什么能**提交**，只决定拖进来的 jpg 能不能被认成「产物」这个身份标签。
_NON_STAGE_TAILS = ("cloud", "thumb", "mask", "ori", "preview")

#: SR 产物/上一次产物的后缀上限（与 services.run_sr.SUFFIX_RE 同口径：1..16 个
#: `[A-Za-z0-9_-]`）。这里自己写一份正则而不是 import run_sr，是为了让 scene_search
#: 保持「纯文件名推导、无服务依赖」——app.py 那边仍会用 run_sr 的权威值。
_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")

#: 上一次产物的尾标记（对齐 SR_code/util.py 的改名规则）。
_NOSR_TAIL = "_NOSR"

#: 平台自烤的预览缓存尾标记（`api/paths.drop_preview_path` 产出的
#: `<栅格 stem>_preview.jpg`）。它在 `_NON_STAGE_TAILS` 里也有一份，但**是唯一
#: 能剥掉的那一个** —— 剥掉它就是那份栅格的 stem：
#: * `<目录名>_preview` 剥出来正好等于场景目录名（本体显示件的预览）；
#: * `<目录名>_sr_preview` 剥出来是 `<目录名>_sr`，即产物的栅格 stem。
#: 另四个（cloud/thumb/mask/ori）是盘阵侧的派生件，剥掉会正好落到真实场景目录上，
#: 云量图就被认成本体了 —— 剥与不剥的界线在这里（见 `de_suffixed_stems`）。
#:
#: 两个形态各一个常量，因为它们在不同的比较里：`_NON_STAGE_TAILS` 那份名单比的是
#: **切出来的尾段**（不含前导下划线），`endswith` / 切片要的是**带上它**的形态。
_PREVIEW_SEG = "preview"
_PREVIEW_TAIL = "_" + _PREVIEW_SEG


def strip_preview_tail(stem: str) -> str:
    """`<栅格 stem>_preview` → `<栅格 stem>`；不带这个尾巴的原样返回。

    拖入链烤的那份（后台静默烤、长期落盘在场景目录里）名字长这样，用户把它再拖
    回来时按它代表的那份栅格认：本体那份等价于拖场景显示件，产物那份等价于拖
    `<目录名>_sr.jpg`。**只剥一层** —— 盘阵上不存在 `..._preview_preview`。
    """
    if stem.lower().endswith(_PREVIEW_TAIL):
        return stem[:-len(_PREVIEW_TAIL)]
    return stem


def de_suffixed_stems(name: str, max_segments: int = 2) -> list[str]:
    """「去掉环节尾段」的候选 stem（拖进来的 jpg 是中间产物时反推目录名用）。

    场景目录名 = 生产全名，而中间产物的文件名是**在生产全名后面**再粘一段
    （`<目录名>_sr.jpg`、`<目录名>_sr_NOSR.jpg`，见 `jpg_stage_name`）；按原名字
    反推出的目录名会带上那条尾巴，盘阵上没有那个目录。

    切成几条候选，而不是只认一条：**suffix 里可以带下划线**
    （`SUFFIX_RE` 允许 `_`，用户的 suffix 是自定短串），所以「切一段」与「切两段」
    两种读法都成立 —— `<目录名>_sr_2.jpg` 可能是 `<目录名>` 的 `sr_2` 产物，也可能是
    `<目录名>_sr` 的 `2` 产物。哪一条是真的由盘阵回答（目录在不在、同级栅格在不在、
    名字对不对得上），这里不猜。名字以 `_NOSR` 结尾时先去它，再从剩下的部分切 ——
    目录名里从不含 `_NOSR`。

    每一条都要求切出来的尾巴是**干净的 suffix 形态**（与 `jpg_stage_name` 同一套
    字符约束）：不干净（` - 副本`、`.preview` 那种）就不再往下切，一条也不生成 ——
    免得拿一个明明是别的名字的东西去反推目录，把 404 的原因写得莫名其妙。

    平台自己烤的那份（`<栅格 stem>_preview.jpg`）也走这条路：`preview` 是
    `_NON_STAGE_TAILS` 里唯一能剥的尾巴，剥掉就回到了那份栅格的真名 ——
    `<目录名>_preview` 剥出 `<目录名>`（本体），`<目录名>_sr_preview` 剥出
    `<目录名>_sr` 再切一段才得到 `<目录名>`（产物，见 `_PREVIEW_TAIL`）。

    返回的是**去后缀的 stem**（调用方自己拼回原后缀再交给 pathguard）：这条链上
    下游看后缀分流（`_fingerprint_mismatch`），也看后缀读 W/H。
    """
    stem = Path(name).stem
    if stem.lower().endswith(_NOSR_TAIL.lower()):
        stem = stem[:-len(_NOSR_TAIL)]
    out: list[str] = []
    rest, cut = stem, []
    for _ in range(max_segments):
        i = rest.rfind("_")
        if i <= 0:
            break
        cut.insert(0, rest[i + 1:])
        rest = rest[:i]
        tail = "_".join(cut)
        if not _SUFFIX_RE.match(tail):
            break
        # `_NON_STAGE_TAILS` 里只有 `preview` 能剥（见 `_PREVIEW_TAIL`）：它是平台自己
        # 烤的那份，剥掉才是真名字。剥完**接着往下切**而不是就地停 —— `<目录名>_sr_preview`
        # 要先剥 preview 才切得出 `<目录名>`。另四个照旧当场停：它们剥掉会落到真实场景
        # 目录上。
        if tail.lower() in _NON_STAGE_TAILS and tail.lower() != _PREVIEW_SEG:
            break
        if rest not in out:
            out.append(rest)
    return out


def jpg_stage_name(stem: str, dir_name: str) -> tuple[str, str] | None:
    """纯词法：`<目录名>_<suffix>.jpg` / `<目录名>_<suffix>_NOSR.jpg` → (环节, suffix)。

    **不 stat、不查任务库、不查配置**。返回 `('product', suffix)` /
    `('nosr', suffix)`，两种形状都不符合时 None（调用方要么按本体处理，要么报
    「这不是场景里的图」）。

    为什么 suffix 从**文件名本身**切，而不是查最近跑过的任务：SR 常常在平台外跑
    （用户在别的机器上直接调 SR 脚本），库里没有记录时照样得认得出这三类图；而且
    「名字写着 `_sr`」本身就是比库里那条记录更直接的事实。查库那条路留给
    `/siblings`（它要在不知道 suffix 的情况下**拼**产物名，只能靠配置与任务）。

    目录名是判断的基准：产物/上一次产物都躺在场景目录里，名字是「目录名 + 尾段」。
    尾段两种形态，且 NOSR 只在末尾、只出现一次。
    """
    if not stem.lower().startswith(dir_name.lower() + "_"):
        return None
    tail = stem[len(dir_name) + 1:]
    if not tail:
        return None
    is_nosr = tail.lower().endswith(_NOSR_TAIL.lower())
    suffix = tail[:-len(_NOSR_TAIL)] if is_nosr else tail
    if not suffix or not _SUFFIX_RE.match(suffix):
        return None
    if suffix.lower() in _NON_STAGE_TAILS:
        return None
    return ("nosr" if is_nosr else "product"), suffix


def stage_of_jpg(dir_path, input_path, stem: str) -> tuple[str, str, Path] | None:
    """拖进来的这份 jpg 是该场景的哪个环节 →
    `(('input'|'product'|'nosr'), suffix, **这一环节自己的栅格**)`；不是 → None。

    返回栅格路径是这条判据的一半用处：命中之后这一行要**描述那个环节自己**，
    不是描述本体（产物的 W/H 是本体的倍数，拿本体的尺寸建画布整张比例都是错的）。
    所以三个值一起给，调用方不必再拼一次名字 —— 也就不可能拼错。

    判据有两条，**两条都得成立**：

    1. 名字切得干净：要么就是本体的显示件（stem == 目录名，或 == 输入影像的 stem
       —— RC 场景的 `PAN.jpg`），要么是 `<目录名>_<suffix>[_NOSR]` 这种产物名
       （见 `jpg_stage_name`）；
    2. **同级栅格真的在**：`<目录>/<stem>.tif|.tiff` 存在（按 `_PRODUCT_EXT_ORDER`
       的顺序试，命中即止）。

    第 2 条才是真门。第 1 条只说明「名字切得干净」，而盘阵上一个场景目录里躺着
    十几样东西，`<目录名>_cloud.jpg` 这种名字同样切得干净 —— 只有「它有一份同名的
    栅格」才能说明这份 jpg 是**某个环节影像的显示件**，而不是随手导出的图。
    `_NON_STAGE_TAILS` 挡的是另一半（云量图这类真有同名栅格的派生件）。

    本体那两种形态**不花任何额外 stat**（不试同级栅格）：它们是既有的关联对象，
    判据在 `is_scene_file` 与 `_fingerprint_mismatch` 里已经写过一遍了。

    `stem` 先过一遍 `strip_preview_tail`：平台自己烤的 `<栅格 stem>_preview.jpg`
    被拖回来时，判的是**它代表的那份栅格**（`_PREVIEW_TAIL`）。这一步不改本体的
    零 stat 性质 —— 剥完照样先与目录名比。
    """
    p = Path(dir_path)
    stem = strip_preview_tail(stem)
    low = stem.lower()
    if low == p.name.lower() or low == Path(input_path).stem.lower():
        # 本体的显示件：环节的栅格就是本体的输入影像（`input_path` 非空由调用方
        # 保证 —— 它是「这个目录算不算场景」的另一半判据）。
        return "input", "", Path(input_path)
    named = jpg_stage_name(stem, p.name)
    if named is None:
        return None
    kind, suffix = named
    for ext in _PRODUCT_EXT_ORDER:
        cand = p / (stem + ext)
        if cand.is_file():
            return kind, suffix, cand
    return None


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
