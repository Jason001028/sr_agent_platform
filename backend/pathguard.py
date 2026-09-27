"""盘阵路径归一化与前缀白名单（零 backend 依赖，谁都能 import）。

为什么单独一个模块：`api/paths.py` 已经 import 了 `services.preview_jpg`，
而 `services/run_sr.py` / `tools/run_sr.py` 也要归一 `lq_path` —— 如果它们
反过来 import `api/paths` 就成环。本模块不 import 任何 backend 包。

两个概念分开：
* **归一化**（纯词法）：用户从资源管理器粘来的 `W:\\GSHC2IMPS\\PRODUCT\\...`
  译成服务端 POSIX 绝对路径 `/DiskArray/GSHC2IMPS/PRODUCT/...`。
* **白名单**（前缀策略）：只放行 `SR_ALLOWED_ROOTS` 之下的路径。

归一化必须 REST 入口与 agent 工具入口共用：同一个场景写成两种形态若算出
不同的 `task_fingerprint`，幂等层就失效、重复投作业。

约定
----
* SR_DRIVE_MAP    盘符映射，`;` 分隔的多条 `KEY=VAL`，默认 `W:=/DiskArray`。
* SR_ALLOWED_ROOTS 允许访问的盘阵前缀，`;` 分隔，默认 `/DiskArray`。
* SR_SCENE_PATH_TEMPLATE 反推模板（含盘符，便于沿用用户侧写法）。可用占位符
                   `{y} {m} {d} {name} {sat} {mid}`；**配了只按它一条走**，未配
                   则按默认的生产树模板。后两个占位符由生产命名规则从文件名拆出
                   （见 docs/sr_code/production-scene-naming.md），拆不出就跳过
                   该条。**日期给两天**（成像日与次日），见 `infer_scene_paths`。
* 白名单是**词法**策略，不做 `is_dir()` 检查（路径可能还没 stat，开发机也
  没有 /DiskArray）；与 `api/paths.py::scenes_root()` 的"必须存在"不同。
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path

_DEFAULT_DRIVE_MAP = "W:=/DiskArray"
_DEFAULT_ALLOWED_ROOTS = "/DiskArray"

#: 形如 `W:` / `W:\` / `W:/`；只认盘符本身，后面的分隔符原样留给下一步处理。
_DRIVE_RE = re.compile(r"^([A-Za-z]):(?=[\\/]|$)")

_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


class PathDeniedError(Exception):
    """路径未通过归一化或白名单校验（穿越 / 白名单外 / fake 占位）。"""


# --------------------------------------------------------------------------
# 盘符映射
# --------------------------------------------------------------------------
def drive_map() -> dict[str, str]:
    """盘符 → POSIX 根。键统一大写（`w:` 也认），值去尾斜杠。"""
    raw = os.environ.get("SR_DRIVE_MAP") or _DEFAULT_DRIVE_MAP
    out: dict[str, str] = {}
    for item in raw.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, val = item.split("=", 1)
        key = key.strip().upper()
        if not key:
            continue                      # `  =/x` 这类空键忽略
        if not key.endswith(":"):
            key += ":"
        out[key] = val.strip().replace("\\", "/").rstrip("/") or "/"
    return out


def to_posix_array_path(raw: str | Path) -> str:
    """用户给的任意形态 → 盘阵 POSIX 绝对路径。**纯词法，不碰文件系统。**

    接受 `/DiskArray/...`（服务端形态）与 `W:\\GSHC2IMPS\\...`（Windows
    形态，经 SR_DRIVE_MAP 映射）。拒绝：空、控制字符、UNC（`\\\\srv\\share`）、
    相对路径、`..` 穿越段、未知盘符。

    不做 `lower()`（Linux 挂载区分大小写）；`~` 段原样保留（Python 不做
    shell 展开，无风险）。
    """
    s = str(raw).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()          # 从资源管理器"复制路径"常带引号
    if not s:
        raise PathDeniedError("路径为空")
    if _CTRL_RE.search(s):
        raise PathDeniedError("路径含控制字符")
    if s.startswith("\\\\") or s.startswith("//"):
        raise PathDeniedError("不接受 UNC 网络路径（\\\\server\\share）")

    m = _DRIVE_RE.match(s)
    if m:
        key = m.group(1).upper() + ":"
        target = drive_map().get(key)
        if target is None:
            known = "、".join(sorted(drive_map())) or "（未配置）"
            raise PathDeniedError(f"未知盘符 {key}（已配置：{known}）")
        rest = s[m.end():].replace("\\", "/").lstrip("/")
        s = target + ("/" + rest if rest else "")
    else:
        s = s.replace("\\", "/")
        if not s.startswith("/"):
            raise PathDeniedError(
                "路径须为绝对路径：服务端形态 /DiskArray/... 或 Windows 形态 "
                "W:\\GSHC2IMPS\\PRODUCT\\<年>\\<月>\\<日>\\<生产编号>")

    # 段级规整：`.` 与空段丢掉，`..` 一律拒。
    # 映射目标若本身是 Windows 形态（把 W: 映到开发机的某个目录时），盘符要留
    # 住 —— 否则拼出来的 `/C:/Users/...` 既不是 POSIX 也不是 Windows 路径，
    # 后续 is_absolute()/resolve() 全都不认。
    def _segs(text: str) -> list[str]:
        parts = [seg for seg in text.split("/") if seg not in ("", ".")]
        if any(seg == ".." for seg in parts):
            raise PathDeniedError("路径含 .. 穿越段")
        return parts

    if _DRIVE_RE.match(s):
        head, _, tail = s.partition("/")
        rest_segs = _segs(tail)
        return head + ("/" + "/".join(rest_segs) if rest_segs else "/")
    return "/" + "/".join(_segs(s))


# --------------------------------------------------------------------------
# 前缀白名单
# --------------------------------------------------------------------------
def allowed_roots() -> list[Path]:
    """SR_ALLOWED_ROOTS 解析结果（`;` 分隔）。词法解析，不要求存在。"""
    raw = os.environ.get("SR_ALLOWED_ROOTS")
    items = raw.split(";") if raw is not None else [_DEFAULT_ALLOWED_ROOTS]
    roots: list[Path] = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        try:
            roots.append(Path(to_posix_array_path(item)))
        except PathDeniedError as e:
            raise PathDeniedError(f"SR_ALLOWED_ROOTS 条目非法（{item}）：{e}") from e
    return roots


def is_within(child: str | Path, root: str | Path) -> bool:
    """白名单包含判定：resolved child 严格在 resolved root 之下。

    `resolve()` 保证 `../` 与符号链接逃逸都过不了；`relative_to` 是段级
    比较，`/DiskArrayX/a` 不会被 `/DiskArray` 误判命中。
    """
    try:
        Path(child).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def is_allowed(path: str | Path) -> bool:
    return any(is_within(path, r) for r in allowed_roots())


def ensure_allowed(path: str | Path, *, kind: str = "any") -> Path:
    """校验绝对路径落在白名单内。``kind`` ∈ ``any`` / ``file`` / ``dir``。

    ``kind="any"`` 允许目标尚不存在（只做词法 + 白名单）—— 供"路径还没
    stat 就要先判可否"的场景用。注意这里**不做 is_file() 兜底**：调用方
    要文件就显式传 ``kind="file"``，免得目录被误拒。
    """
    if kind not in ("any", "file", "dir"):
        raise ValueError(f"kind 取值非法：{kind}")
    p = Path(path)
    if "<fake>" in str(p):
        raise PathDeniedError("fake 占位路径不可访问")
    if any(seg == ".." for seg in p.parts):
        raise PathDeniedError("路径含路径穿越")
    # 绝对性：Windows 上 `Path("/DiskArray/x").is_absolute()` 是 False（缺盘符），
    # 可盘阵路径本来就是 POSIX 形态，必须放行 —— 所以两种都认。
    if not (p.is_absolute() or p.as_posix().startswith("/")):
        raise PathDeniedError("仅接受绝对路径")
    roots = allowed_roots()
    if not roots:
        raise PathDeniedError("未配置允许的盘阵前缀（SR_ALLOWED_ROOTS）")
    if not is_allowed(p):
        listed = "、".join(str(r) for r in roots)
        raise PathDeniedError(f"路径不在允许的盘阵前缀内（SR_ALLOWED_ROOTS={listed}）：{p}")
    if kind == "file" and not p.is_file():
        raise PathDeniedError(f"文件不存在：{p}")
    if kind == "dir" and not p.is_dir():
        raise PathDeniedError(f"目录不存在：{p}")
    return p


def normalize_submit_path(raw: str | Path) -> str:
    """提交侧的路径归一：任意形态 → 盘阵 POSIX 绝对路径，并过前缀白名单。

    REST 入口（`api/platform.py::_norm_sr_params`）与 agent 工具入口
    （`tools/run_sr.py::run_run_sr`）**必须共用这一个函数**：同一个场景写成
    `W:\\GSHC2IMPS\\...` 和写成 `/DiskArray/GSHC2IMPS/...`，若归一化结果不同，
    `task_fingerprint` 就不同，幂等层失效、同一个场景被投两次作业。

    抛 `PathDeniedError`，由调用方决定转成 HTTP 400 还是工具 err。
    """
    return ensure_allowed(to_posix_array_path(raw)).as_posix()


# --------------------------------------------------------------------------
# 由「文件名 + 日期」反推场景目录
# --------------------------------------------------------------------------
#: 生产树模板：`{y}/{m}/{d}` 之下还有两层 —— 卫星型号与**段级**产品目录，
#: 后者是把景级目录名去掉「景号」那一段得到的（`scene_name_layers`）。
_DEFAULT_SCENE_TEMPLATE = (
    "W:\\GSHC2IMPS\\PRODUCT\\{y}\\{m}\\{d}\\{sat}\\{mid}\\{name}")

#: 日期偏移：名字里的日期只当**下界**用 —— 盘阵按生产日建目录，深夜成像的景记在
#: 第二天，所以每个模板按「成像日、次日」各渲染一条（`0, 1` 天）。
_DAY_OFFSETS = (0, 1)

#: 文件名里可解析出的日期：优先 14 位（YYYYMMDDHHMMSS），其次 8 位。
_TS_RE = re.compile(r"(?<!\d)(\d{14}|\d{8})(?!\d)")

_BAD_STEM_RE = re.compile(r"[\\/\x00-\x1f\x7f]")

#: 光栅后缀。前端直接把**用户拖进来的文件名**（含后缀）发过来，而场景目录名
#: 从来不带后缀 —— 不剥掉就会拼出 `<目录名>.tif` 这种目录。
_RASTER_EXT_RE = re.compile(r"\.(?:tiff?|img|jpe?g)$", re.IGNORECASE)


def strip_raster_ext(name: str) -> str:
    """去掉影像后缀（.tif/.tiff/.img/.jpg/.jpeg，大小写不敏感）。"""
    return _RASTER_EXT_RE.sub("", str(name).strip())

#: 生产命名规则里各段的下标（下划线分段），详见
#: docs/sr_code/production-scene-naming.md —— 段号 3 位、景号 4 位，两位都不是
#: 数字就认为这个名字不是生产命名形态，不硬拼那两层。
_SEG_IDX, _SCENE_IDX = 4, 5


def scene_path_template() -> str:
    return os.environ.get("SR_SCENE_PATH_TEMPLATE") or _DEFAULT_SCENE_TEMPLATE


#: 生产名各段之间的分隔符。真机是下划线（`…_102_0025_001_L1_PAN`），但用户
#: 口径里也有空格形态（`JXGF07D03 PMS 20260622052600 … MSS`）—— 两种都认。
_FIELD_SEP_RE = re.compile(r"[_\s]+")


def scene_name_layers(name: str) -> tuple[str, str] | None:
    """按生产命名规则拆出「卫星型号」与「段级产品目录名」；不合规则返回 None。

    分段（`JXGF07D03_PMS_20260622052600_200516571_101_0006_001_L1_MSS`）：
    卫星型号 · 传感器 · 成像时刻(14 位) · 任务计划号 · 段号(3 位) · 景号(4 位) ·
    生产次数 · 级别 · 产品。

    卫星型号就是第 0 段；段级目录名 = **去掉景号那一段**（真机实测：景级目录
    `…_102_0025_001_L1_PAN` 的父目录是 `…_102_001_L1_PAN`），并**沿用原文的
    分隔符**重建 —— 空格形态的名字拼出带下划线的段级目录必然 stat 不到。
    """
    raw = str(name).strip()
    tokens = _FIELD_SEP_RE.split(raw)
    # 至少 `_SCENE_IDX + 2` 段：段号那段（tokens[_SEG_IDX]）与景号那段
    # （tokens[_SCENE_IDX]）都要在**名字里**，而且下面还要读分隔符 seps[_SCENE_IDX]
    # —— 分隔符比段少一个，所以门槛比「够读到景号」再高一段。少了就判不合规则
    # （调用方按 400 处理）；早先只挡到 `_SCENE_IDX`，六段的名字会走进
    # `seps[_SCENE_IDX]` 越界，一个本该 400 的输入变成 500。
    if len(tokens) <= _SCENE_IDX + 1:
        return None
    seps = _FIELD_SEP_RE.findall(raw)
    if len(seps) != len(tokens) - 1:         # 首尾有分隔符：形态不整，不猜
        return None
    seg, scene = tokens[_SEG_IDX], tokens[_SCENE_IDX]
    if not (len(seg) == 3 and seg.isdigit()
            and len(scene) == 4 and scene.isdigit()):
        return None
    sep = seps[_SCENE_IDX] if len(set(seps)) == 1 else "_"
    mid = sep.join(tokens[:_SCENE_IDX] + tokens[_SCENE_IDX + 1:])
    return tokens[0], mid


#: 日期目录的三段形态 `<年>/<月>/<日>`（生产树的前三层）。
_YEAR_SEG_RE = re.compile(r"(?:19|20|21)\d{2}")
_MONTH_SEG_RE = re.compile(r"0[1-9]|1[0-2]")
_MDAY_SEG_RE = re.compile(r"0[1-9]|[12]\d|3[01]")


def _segments(path: str | Path) -> list[str]:
    return [seg for seg in Path(str(path)).as_posix().split("/") if seg]


def _rightmost_day_index(parts: list[str]) -> int | None:
    """`<年>/<月>/<日>` 那一段里**最靠右**的起始下标；没有返回 None。"""
    for i in range(len(parts) - 3, -1, -1):
        if (_YEAR_SEG_RE.fullmatch(parts[i])
                and _MONTH_SEG_RE.fullmatch(parts[i + 1])
                and _MDAY_SEG_RE.fullmatch(parts[i + 2])):
            return i
    return None


def production_tree_depth(path: str | Path) -> int | None:
    """目录落在日期目录之下第几层：日期目录 0、卫星型号 1、段级 2、景级 3；认不出 None。

    **纯词法**（只看路径末尾有没有 `<年>/<月>/<日>` 这一段、它后面还剩几段），
    不 stat 任何东西 —— 它服务的是「404 时告诉用户粘到了哪一层」，不是准入判定。
    旧扁平形态（`<年>/<月>/<日>/<生产编号>`）会得到 1，与六层树的卫星型号层同值；
    调用方要用 `flat_scene_layout` 区分这两种拓扑，再定措辞。
    """
    parts = _segments(path)
    i = _rightmost_day_index(parts)
    return None if i is None else len(parts) - (i + 3)


def flat_scene_layout(path: str | Path) -> bool:
    """路径是不是**旧扁平形态**：日期目录紧接着的那个目录名就是生产名。

    六层生产树里日期目录下面那一段是**卫星型号**（`JL1KF02B03`：不含 14 位成像
    时刻），扁平形态（`<年>/<月>/<日>/<生产名>`，平铺部署与 e2e 假拓扑）里就是
    场景目录本身。判它只为一件事：同一段路径在两种拓扑里含义不同 —— 扁平形态下
    再深一层是场景目录**内部**，生产树下才是段级层。纯词法，不 stat。
    """
    parts = _segments(path)
    i = _rightmost_day_index(parts)
    return i is not None and i + 3 < len(parts) and looks_like_scene_name(parts[i + 3])


def looks_like_scene_name(name: str) -> bool:
    """名字像不像一个**完整生产名**（即 `<名>_meta.xml` 的前缀）。

    判据只有一条：名字里认不认得出 14/8 位成像时刻。生产名至少是
    `卫星型号_传感器_<14 位北京时间>_任务计划号_…_产品`，被截掉一段的名字
    （日期目录的 `18`、卫星型号层的 `JXGF07D03`、随手建的 `scenes`）都拿不出它。

    **只决定诊断措辞，不参与「是不是场景目录」的判定** —— 判定始终是
    `<目录名>_meta.xml` 在不在（与 SR 的 `osp.basename(lq_path) + "_meta.xml"`
    同一口径），非生产树部署里存在短名场景目录。
    """
    return parse_scene_date(name) is not None


def parse_scene_date(name: str) -> str | None:
    """从文件名里取成像日期，返回 ``YYYY-MM-DD``；取不到返回 None。

    生产场景名形如 `JL1KF02B03_PMS02_20260910124710_..._L1_PAN`，中间那段
    14 位就是成像时间。**取不到就返回 None** —— 调用方据此提示用户手填，
    绝不猜一个日期出来（猜错会指到别的场景目录）。
    """
    m = _TS_RE.search(str(name))
    if not m:
        return None
    ymd = m.group(1)[:8]                 # 14 位时尾部是 HHMMSS，取前 8 位
    y, mo, d = ymd[:4], ymd[4:6], ymd[6:8]
    if not ("1900" <= y <= "2999" and "01" <= mo <= "12" and "01" <= d <= "31"):
        return None
    return f"{y}-{mo}-{d}"


def infer_scene_paths(name: str, iso_date: str) -> list[str]:
    """由「场景名 + 日期」反推候选场景目录（纯词法，不做任何 stat）。

    返回服务端 POSIX 绝对路径列表，交给调用方逐个 ``stat`` 试探。``name``
    必须是裸文件名（不含路径分隔符，可带或不带光栅后缀）—— 否则模板会被注入
    出一条越界路径；后缀会先剥掉，因为场景目录名从不带后缀。

    候选来源：
    * 配了 ``SR_SCENE_PATH_TEMPLATE`` → **只按它一条**渲染；
    * 未配 → 生产树模板（``{sat}/{mid}`` 两层）。

    每个模板**渲染两条**：成像日与次日（``_DAY_OFFSETS``）。名字里的 14 位是
    **成像**时刻，而盘阵按**生产日**建目录 —— 深夜成像的景落进第二天，
    `…_20260917124710_…` 常躺在 ``…/09/18/…``，只按名字里的日子拼必然找不到。
    模板若不含日期占位符，两天渲染出同一条，去重后仍只有一条。

    名字不符合生产命名规则（拆不出卫星型号/段级目录名）时跳过用到
    ``{sat}``/``{mid}`` 的模板 —— **不拿空串硬拼一条注定不存在的路径**；
    若因此一条候选也构造不出来，返回空列表，由调用方报错说明原因。
    """
    raw = str(name).strip()
    if not raw or _BAD_STEM_RE.search(raw):
        raise PathDeniedError(f"场景名非法（不得含路径分隔符或控制字符）：{name!r}")
    stem = strip_raster_ext(raw)          # 用户拖进来的是带后缀的文件名
    if not stem:
        raise PathDeniedError(f"场景名非法（去掉后缀后为空）：{name!r}")
    try:
        y, mo, d = str(iso_date).split("-")
    except ValueError as e:
        raise PathDeniedError(f"日期须为 YYYY-MM-DD：{iso_date!r}") from e
    if not (len(y) == 4 and len(mo) == 2 and len(d) == 2 and
            y.isdigit() and mo.isdigit() and d.isdigit()):
        raise PathDeniedError(f"日期须为 YYYY-MM-DD：{iso_date!r}")
    try:
        first = date(int(y), int(mo), int(d))
    except ValueError as e:                  # 形如 2026-09-31：位数对、日子不存在
        raise PathDeniedError(f"日期须为 YYYY-MM-DD：{iso_date!r}") from e

    layers = scene_name_layers(stem)
    sat, mid = layers if layers else ("", "")
    templates = ([scene_path_template()] if os.environ.get("SR_SCENE_PATH_TEMPLATE")
                 else [_DEFAULT_SCENE_TEMPLATE])
    out: list[str] = []
    for tpl in templates:
        if layers is None and ("{sat}" in tpl or "{mid}" in tpl):
            continue
        for offset in _DAY_OFFSETS:
            day = first + timedelta(days=offset)
            fields = {"y": f"{day.year:04d}", "m": f"{day.month:02d}",
                      "d": f"{day.day:02d}", "name": stem, "sat": sat, "mid": mid}
            try:
                rendered = tpl.format(**fields)
            except (KeyError, IndexError, ValueError) as e:
                raise PathDeniedError(f"反推模板无法渲染（{tpl}）：{e}") from e
            posix = to_posix_array_path(rendered)
            if posix not in out:
                out.append(posix)
    return out
