"""Intelligent difference localization (read-only CV service).

给定两张已经落在**同一分析网格**上的影像，输出：

* ``differenceMap`` —— 连续差异强度图（0..1 → 8-bit PNG），热力图直接用；
* ``binaryMask`` —— 形态学清理 + 最小面积筛选后的精确二值掩码；
* ``regions[]`` —— 8 邻域连通域（面积、BBox、质心、平均/最大差异、轮廓）。

两种方法共用同一套输入准备与区域后处理，只替换核心差异计算：

* ``absdiff`` —— ``D = |A - B|``；
* ``ssim`` —— 局部结构相似度图 ``S``，``D = clip(1 - S, 0, 1)``。

**这是只读服务**：不写盘、不触发 SR、不修改任何影像。本模块不 import
``backend.api``，供 Viewer、Batch Goal 与 Agent 共用。

依赖策略
--------
形态学、连通域、轮廓追踪、重采样**只保留一套 SciPy/NumPy 实现**，避免同一份
输入在「装了 OpenCV」与「没装」的机器上给出不同的面积、阈值或坐标。SciPy 是
必需依赖（见 ``deploy/requirements-api.txt``）。scikit-image 只用于 SSIM：
装了就走 ``structural_similarity(full=True)``，没装则用本模块的等效兜底；
结果里的 ``parameters.backend`` 会写明实际使用的实现。

像素口径
--------
调用方传进来的如果是预览灰度（0..255），走的就是**预览分析**；原始 ROI 分析由
``api/app.py`` 的 ``/api/diff/analyze`` 用 ``precision='source-roi'`` 另行读取。
两者的结果里都有 ``source`` 字段，前端据此如实标注，不把预览结果当成原始 DN 差分。
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import (
    binary_closing, binary_fill_holes, binary_opening, find_objects,
    gaussian_filter, label as ndlabel, map_coordinates, uniform_filter,
)

try:  # pragma: no cover - 取决于部署环境
    from skimage.metrics import structural_similarity as _skimage_ssim
except Exception:  # noqa: BLE001 - 没装 scikit-image 时走兜底
    _skimage_ssim = None

from backend.services import preview_jpg


METHODS = ("absdiff", "ssim")
SENSITIVITIES = ("low", "standard", "high")

#: 分析网格长边上限。前端默认按 2048 生成；后端只做兜底校验（再大内存与耗时都失控）。
DEFAULT_MAX_EDGE = 2048
MAX_EDGE = 4096

#: 灵敏度预设。数值是**归一化后**的差异强度（0..1），不是 DN。
#: 灵敏度越高 → 阈值越低 → 保留更多候选。
#: 前端 ``lib/diffLocate.ts`` 有一份同值副本，只用于高级参数的占位提示。
THRESHOLD_PRESETS = {
    "absdiff": {"low": 0.20, "standard": 0.12, "high": 0.07},
    "ssim": {"low": 0.26, "standard": 0.15, "high": 0.08},
}

DEFAULT_MIN_AREA_PX = 64
MAX_REGIONS = 200
MAX_CONTOUR_POINTS = 96
#: 简化轮廓与真实连通域掩码的最小 IoU。低于它的轮廓不标 ``exact``，
#: 前端也不允许直接写成掩码 —— 只给坐标与显示，不冒充精确边界。
CONTOUR_MIN_IOU = 0.85


class DiffError(ValueError):
    """输入 / 参数问题；HTTP 层转 400。"""


def _b64_png(arr: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(arr, mode="L").save(buf, format="PNG", compress_level=6)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def decode_gray_png(data: str) -> tuple[np.ndarray, np.ndarray]:
    """PNG（灰/RGBA）→ ``(float32 0..1, bool valid)``。

    前端用 alpha=0 表示 NoData（本地影像里的非有限值）；没有 alpha 的图整幅有效。
    """
    raw = data.strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as e:  # noqa: BLE001
        raise DiffError(f"影像数据不是合法 base64：{e}") from e
    try:
        with Image.open(io.BytesIO(blob)) as im:
            im.load()
            if im.mode in ("RGBA", "LA"):
                arr = np.asarray(im.convert("RGBA"))
                gray = arr[..., 0].astype(np.float32) / 255.0
                valid = arr[..., 3] > 0
            else:
                gray = np.asarray(im.convert("L")).astype(np.float32) / 255.0
                valid = np.ones(gray.shape, dtype=bool)
    except Exception as e:  # noqa: BLE001
        raise DiffError(f"无法解码影像 PNG：{e}") from e
    return gray, valid


def load_gray_image(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """服务端文件（预览 JPG / 普通图片）→ 灰度与有效掩码。"""
    try:
        with Image.open(path) as im:
            im.load()
            if im.mode in ("RGBA", "LA"):
                arr = np.asarray(im.convert("RGBA"))
                gray = arr[..., 0].astype(np.float32) / 255.0
                valid = arr[..., 3] > 0
            else:
                gray = np.asarray(im.convert("L")).astype(np.float32) / 255.0
                valid = np.ones(gray.shape, dtype=bool)
    except Exception as e:  # noqa: BLE001
        raise DiffError(f"无法读取影像：{Path(path).name}（{e}）") from e
    return gray, valid


def resolve_max_edge(raw) -> int:
    try:
        value = int(raw) if raw is not None else DEFAULT_MAX_EDGE
    except (TypeError, ValueError) as e:
        raise DiffError(f"分析分辨率必须是整数：{raw!r}") from e
    if value < 128:
        raise DiffError("分析分辨率下限 128 px")
    return min(value, MAX_EDGE)


def common_grid_shape(shape_a, shape_b, max_edge: int = DEFAULT_MAX_EDGE) -> tuple[int, int]:
    """两张影像 → 受限共同分析网格 ``(H, W)``。

    与前端 ``lib/diffLocate.ts::commonGrid`` 同口径：宽高比相差超过 2% 直接拒绝
    （没有配准关系时硬拉成同一网格只会制造假差异）；长边取两张图的较大者并限制
    在 ``max_edge`` 内。**先规划网格再重采样**，不能先按整幅加载、最后才拒绝。
    """
    ha, wa = int(shape_a[0]), int(shape_a[1])
    hb, wb = int(shape_b[0]), int(shape_b[1])
    if min(ha, wa, hb, wb) <= 0:
        raise DiffError("影像尺寸未知，无法建立共同分析网格")
    ratio_a, ratio_b = wa / ha, wb / hb
    if abs(ratio_a - ratio_b) / max(ratio_a, ratio_b) > 0.02:
        raise DiffError(
            f"两张影像宽高比不一致（{ratio_a:.3f} / {ratio_b:.3f}），"
            "无法建立共同分析网格")
    long_edge = min(int(max_edge), max(wa, ha, wb, hb))
    if wa >= ha:
        width = long_edge
        height = max(2, int(round(long_edge / ratio_a)))
    else:
        height = long_edge
        width = max(2, int(round(long_edge * ratio_a)))
    return height, width


def _odd_kernel(raw, default: int, label: str) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError) as e:
        raise DiffError(f"{label}必须是整数：{raw!r}") from e
    if value < 1:
        raise DiffError(f"{label}至少为 1")
    if value % 2 == 0:
        value += 1
    return min(value, 31)


def _method(raw) -> str:
    if raw is None:
        return "absdiff"
    method = str(raw).strip().lower()
    if method not in METHODS:
        raise DiffError(f"未知的分析方法：{raw!r}（只认 {' / '.join(METHODS)}）")
    return method


def _sensitivity(raw) -> str:
    if raw is None:
        return "standard"
    level = str(raw).strip().lower()
    if level not in SENSITIVITIES:
        raise DiffError(f"未知的差异灵敏度：{raw!r}")
    return level


def _shared_normalize(a: np.ndarray, b: np.ndarray,
                      valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """按**同一灰度范围**归一化到 [0,1]。

    预览 PNG 已是 0..1，直接返回；原始 DN 走这张图与另一张图合起来的 1%..99%
    分位（同一对数，绝不分别自动拉伸）。
    """
    if float(np.nanmax(a)) <= 1.5 and float(np.nanmax(b)) <= 1.5:
        return a, b
    vals = np.concatenate([a[valid], b[valid]])
    if vals.size == 0:
        raise DiffError("两张影像没有共同的有效像素")
    # 分位采样：原始分辨率 ROI 可能有上亿像元，取 20 万点已足够稳定。
    step = max(1, vals.size // 200_000)
    sample = vals[::step]
    lo, hi = np.percentile(sample, (1.0, 99.0))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 1e-6:
        lo, hi = float(np.nanmin(vals)), float(np.nanmax(vals))
    if hi - lo < 1e-6:
        return np.zeros_like(a), np.zeros_like(b)

    def scale(x: np.ndarray) -> np.ndarray:
        return np.clip((x - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)

    return scale(a), scale(b)


def _masked_blur(arr: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """只在有效像素上归一化平滑；NoData 不当作 0 参与邻域平均。"""
    weights = valid.astype(np.float32)
    num = gaussian_filter(np.where(valid, arr, 0.0), sigma=sigma, mode="nearest")
    den = gaussian_filter(weights, sigma=sigma, mode="nearest")
    out = num / np.maximum(den, 1e-6)
    out[den < 1e-6] = 0.0
    return out.astype(np.float32)


def _fill_invalid(arr: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """把 NoData 用最近的有效像素填上，避免 NaN 进入 SSIM 窗口。"""
    if bool(valid.all()):
        return arr
    if not bool(valid.any()):
        return np.zeros_like(arr)
    from scipy.ndimage import distance_transform_edt
    idx = distance_transform_edt(~valid, return_distances=False, return_indices=True)
    return np.asarray(arr[tuple(idx)], dtype=np.float32)


def _morphology(mask: np.ndarray, open_k: int, close_k: int) -> np.ndarray:
    """开运算（去孤立点）→ 闭运算（连接小间隙）。不做膨胀过度合并。

    边界固定为 0（图外视为背景），与坐标/面积口径一起保证跨环境一致。
    """
    m = mask.astype(bool)
    if open_k > 1:
        m = binary_opening(m, structure=np.ones((open_k, open_k), bool), border_value=0)
    if close_k > 1:
        m = binary_closing(m, structure=np.ones((close_k, close_k), bool), border_value=0)
    return m.astype(bool)


def _ssim_map(a: np.ndarray, b: np.ndarray, win: int, gaussian_weights: bool) -> np.ndarray:
    """逐位置 SSIM 图。数据范围固定 1.0，不做整图平均。"""
    win = _odd_kernel(win, 7, "SSIM 窗口")
    h, w = a.shape
    if min(h, w) < win:
        raise DiffError(f"分析区域太小，放不下 {win}×{win} 的 SSIM 窗口")
    if _skimage_ssim is not None:
        return _skimage_ssim(
            a, b, data_range=1.0, win_size=win, gaussian_weights=gaussian_weights,
            full=True)[1].astype(np.float32)
    # SciPy 兜底：均匀窗口按 skimage 的样本协方差修正做，尽量贴近主路径。
    if gaussian_weights:
        filt = lambda x: gaussian_filter(x, sigma=1.5, truncate=3.5, mode="reflect")  # noqa: E731
        cov_norm = 1.0
    else:
        filt = lambda x: uniform_filter(x, size=win, mode="reflect")  # noqa: E731
        npx = float(win * win)
        cov_norm = npx / (npx - 1.0) if npx > 1 else 1.0
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    mu_a, mu_b = filt(a), filt(b)
    var_a = cov_norm * (filt(a * a) - mu_a * mu_a)
    var_b = cov_norm * (filt(b * b) - mu_b * mu_b)
    cov = cov_norm * (filt(a * b) - mu_a * mu_b)
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a * mu_a + mu_b * mu_b + c1) * (var_a + var_b + c2)
    return np.clip(num / np.maximum(den, 1e-12), -1.0, 1.0).astype(np.float32)


@dataclass
class _Region:
    label: int
    area: int
    x: int
    y: int
    w: int
    h: int
    cx: float
    cy: float
    mean_diff: float
    max_diff: float


def _regions(mask: np.ndarray, diff: np.ndarray) -> tuple[np.ndarray, list[_Region]]:
    """8 邻域连通域。质心用像素索引均值（不加 0.5），与区域坐标同一口径。"""
    labels, n = ndlabel(mask, structure=np.ones((3, 3), bool))
    if n == 0:
        return labels, []
    counts = np.bincount(labels.ravel())
    out: list[_Region] = []
    for i, sl in enumerate(find_objects(labels), start=1):
        if sl is None:
            continue
        ys, xs = sl
        sub_labels = labels[ys, xs]
        m = sub_labels == i
        sub = diff[ys, xs][m]
        area = int(counts[i])
        yy, xx = np.nonzero(m)
        out.append(_Region(
            i, area, int(xs.start), int(ys.start),
            int(xs.stop - xs.start), int(ys.stop - ys.start),
            float(xs.start + xx.mean()), float(ys.start + yy.mean()),
            float(sub.mean()) if sub.size else 0.0,
            float(sub.max()) if sub.size else 0.0,
        ))
    return labels, out


#: Moore 邻域顺时针方向（从西开始）。
_MOORE = [(-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1)]


def _trace_outer(m: np.ndarray) -> list[tuple[int, int]]:
    """Moore-Neighbor 外边界追踪，返回像素坐标 ``(x, y)``。

    不依赖 OpenCV；只跟踪区域外轮廓。区域中间的孔洞不会被当成外轮廓的一部分。
    一个像素宽的区域也能得到一个闭合的细轮廓；不足 3 点时由调用方退回 BBox。
    """
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return []
    start = (int(xs[0]), int(ys[0]))          # 最上行的最左像素，西侧必是背景
    h, w = m.shape

    def fg(x: int, y: int) -> bool:
        return 0 <= y < h and 0 <= x < w and bool(m[y, x])

    b = start
    c = (start[0] - 1, start[1])
    poly = [start]
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    while True:
        di = _MOORE.index((c[0] - b[0], c[1] - b[1]))
        nxt = None
        for k in range(1, 9):
            nd = _MOORE[(di + k) % 8]
            cand = (b[0] + nd[0], b[1] + nd[1])
            if fg(*cand):
                prev_d = _MOORE[(di + k - 1) % 8]
                nxt = (cand, (b[0] + prev_d[0], b[1] + prev_d[1]))
                break
        if nxt is None:                        # 孤立单像素
            break
        nb, nc = nxt
        edge = (b, nb)
        if edge in seen:                       # 回到起点且同一方向 → 闭合
            break
        seen.add(edge)
        if nb != start:
            poly.append(nb)
        b, c = nb, nc
    return poly


def _rdp_open(points: list[tuple[int, int]], eps: float) -> list[tuple[int, int]]:
    """开放折线的 RDP 简化（首尾都保留）。"""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        x0, y0 = points[i0]
        x1, y1 = points[i1]
        dx, dy = x1 - x0, y1 - y0
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        best, best_i = -1.0, i0 + 1
        for i in range(i0 + 1, i1):
            px, py = points[i]
            dist = abs(dy * (px - x0) - dx * (py - y0)) / norm
            if dist > best:
                best, best_i = dist, i
        if best > eps:
            keep[best_i] = True
            stack.append((i0, best_i))
            stack.append((best_i, i1))
    return [points[i] for i in range(len(points)) if keep[i]]


def _rdp(points: list[tuple[int, int]], eps: float) -> list[tuple[int, int]]:
    """闭合多边形 RDP：按离首点最远的顶点拆成两条开放折线分别简化。

    不能直接把首点追加成终点再跑开放 RDP —— 首尾重合，基线长度为零，
    所有距离都会算成零，结果退化成单点。
    """
    n = len(points)
    if n < 3:
        return list(points)
    x0, y0 = points[0]
    far = max(range(n), key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
    if far == 0:                       # 所有点重合
        return [points[0]]
    first = _rdp_open(points[:far + 1], eps)
    second = _rdp_open(points[far:] + [points[0]], eps)
    return first[:-1] + second[:-1]


def _polygon_iou(poly: list[tuple[int, int]], sub: np.ndarray) -> float:
    """简化多边形与真实连通域掩码的 IoU（亚像素栅格化，按覆盖即算）。

    只用像素中心连线画多边形会让每个边界像素少算半格，小区域会被系统性判低；
    这里在 4× 分辨率上填充，再按「任一子像素被覆盖」归并，矩形能取到 1.0。
    """
    h, w = sub.shape
    s = 4
    img = Image.new("1", (w * s, h * s), 0)
    ImageDraw.Draw(img).polygon(
        [((float(x) + 0.5) * s, (float(y) + 0.5) * s) for x, y in poly], fill=1)
    filled = np.asarray(img, dtype=bool).reshape(h, s, w, s).any(axis=(1, 3))
    union = np.count_nonzero(filled | sub)
    if union == 0:
        return 0.0
    return float(np.count_nonzero(filled & sub)) / float(union)


def _bbox_contour(x: int, y: int, w: int, h: int) -> list[list[int]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _contour_and_holes(labels: np.ndarray, r: _Region):
    """→ ``(contour, contour_kind, hole_count)``，坐标相对分析网格左上角。

    ``contour_kind``：
    * ``exact`` —— 真实外轮廓，简化后与连通域掩码 IoU 达标，可直接写掩码；
    * ``outer`` —— 含孔洞，只给外边界，写掩码会填掉孔洞，前端拒绝；
    * ``approx`` —— 点数受限后轮廓与真实掩码偏差过大，只供显示，前端拒绝；
    * ``bbox`` —— 无法成形，退回外接矩形，前端拒绝。
    """
    sub = labels[r.y:r.y + r.h, r.x:r.x + r.w] == r.label
    holes = binary_fill_holes(sub) & ~sub
    hole_count = int(ndlabel(holes, structure=np.ones((3, 3), bool))[1]) if holes.any() else 0
    traced = _trace_outer(sub)
    if len(traced) >= 3:
        # 点数有上限：先按 1px 容差保形，超限就逐步放宽容差，不再等距抽点。
        eps = 1.0
        simplified = _rdp(traced, eps)
        limit = float(max(r.w, r.h, 8))
        while len(simplified) > MAX_CONTOUR_POINTS and eps < limit:
            eps *= 2.0
            simplified = _rdp(traced, eps)
        if len(simplified) >= 3:
            contour = [[int(px) + r.x, int(py) + r.y] for px, py in simplified]
            if hole_count:
                return contour, "outer", hole_count
            if _polygon_iou(simplified, sub) >= CONTOUR_MIN_IOU:
                return contour, "exact", hole_count
            return contour, "approx", hole_count
    return _bbox_contour(r.x, r.y, r.w, r.h), "bbox", hole_count


def _pick_regions(regions: list[_Region], min_area: int) -> list[_Region]:
    kept = [r for r in regions if r.area >= min_area]
    # 排序综合面积与平均差异；**不**把这个分数叫置信度。
    kept.sort(key=lambda r: r.mean_diff * np.sqrt(r.area), reverse=True)
    return kept


def _roi_slice(roi, shape) -> tuple[int, int, int, int]:
    if roi is None:
        return 0, 0, int(shape[1]), int(shape[0])
    if not isinstance(roi, dict):
        raise DiffError("分析范围参数格式不正确")
    try:
        x = int(roi.get("x", 0))
        y = int(roi.get("y", 0))
        w = int(roi.get("w"))
        h = int(roi.get("h"))
    except (TypeError, ValueError) as e:
        raise DiffError(f"分析范围参数格式不正确：{e}") from e
    H, W = shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise DiffError("分析范围太小（至少 8×8 px）")
    return x0, y0, x1, y1


def analyze_arrays(
    a: np.ndarray,
    b: np.ndarray,
    *,
    valid_a: np.ndarray | None = None,
    valid_b: np.ndarray | None = None,
    method: str = "absdiff",
    sensitivity: str = "standard",
    threshold: float | None = None,
    min_area_px: int = DEFAULT_MIN_AREA_PX,
    blur: bool = True,
    open_kernel: int = 3,
    close_kernel: int = 3,
    ssim_window: int = 7,
    ssim_gaussian: bool = False,
    roi: dict | None = None,
    source: str = "preview",
    scale_a: tuple[float, float] | None = None,
    scale_b: tuple[float, float] | None = None,
    nodata_a: float | None = None,
    nodata_b: float | None = None,
    warnings: list[str] | None = None,
    elapsed_ms: float | None = None,
) -> dict:
    """核心入口。数组形状必须一致；不一致由调用方先重采样。

    ``roi`` 只裁剪本次分析范围：返回的区域坐标与差异图都相对该 ROI 左上角，
    ROI 在原数组里的偏移写进 ``analysisGrid.origin``。调用方不要自己再加偏移。
    """
    started = time.perf_counter()
    method = _method(method)
    sensitivity = _sensitivity(sensitivity)
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.shape != b.shape:
        raise DiffError(f"两张影像的分析网格不一致：{a.shape} / {b.shape}")
    if a.ndim != 2:
        raise DiffError("差异分析只支持单通道灰度分析网格")

    valid = np.isfinite(a) & np.isfinite(b)
    if valid_a is not None:
        valid &= np.asarray(valid_a, dtype=bool)
    if valid_b is not None:
        valid &= np.asarray(valid_b, dtype=bool)

    def _drop_sentinel(mask: np.ndarray, arr: np.ndarray, nodata) -> np.ndarray:
        if nodata is None:
            return mask
        try:
            nd = float(nodata)
        except (TypeError, ValueError):
            return mask
        if not np.isfinite(nd):
            return mask & np.isfinite(arr)
        return mask & (arr != np.float32(nd))

    valid = _drop_sentinel(valid, a, nodata_a)
    valid = _drop_sentinel(valid, b, nodata_b)
    # 非有限值与有限哨兵都在这里退出计算；后面不再出现 NaN。
    a = np.where(valid, a, 0.0).astype(np.float32)
    b = np.where(valid, b, 0.0).astype(np.float32)

    # 同一灰度范围归一化（绝不对两张图分别自动拉伸）。
    a, b = _shared_normalize(a, b, valid)

    x0, y0, x1, y1 = _roi_slice(roi, a.shape)
    a = a[y0:y1, x0:x1]
    b = b[y0:y1, x0:x1]
    valid = valid[y0:y1, x0:x1]
    if not valid.any():
        raise DiffError("分析范围内没有共同有效像素（全部是 NoData）")

    if blur:
        # 有效邻域加权平滑；图外/NoData 不当 0 参与平均。
        a = _masked_blur(a, valid, sigma=1.0 / 3.0)
        b = _masked_blur(b, valid, sigma=1.0 / 3.0)

    if method == "absdiff":
        diff = np.abs(a - b).astype(np.float32)
    else:
        # SSIM 窗口不能吃 NaN；用最近有效像素填充无效位置，最后仍按 valid 清零。
        ssim = _ssim_map(_fill_invalid(a, valid), _fill_invalid(b, valid),
                         ssim_window, ssim_gaussian)
        diff = np.clip(1.0 - ssim, 0.0, 1.0).astype(np.float32)
    diff[~valid] = 0.0

    if threshold is None:
        thr = THRESHOLD_PRESETS[method][sensitivity]
    else:
        try:
            thr = float(threshold)
        except (TypeError, ValueError) as e:
            raise DiffError(f"差异阈值必须是数字：{threshold!r}") from e
        if not 0.0 < thr < 1.0:
            raise DiffError("差异阈值必须在 (0, 1) 之间")
    try:
        min_area = int(min_area_px)
    except (TypeError, ValueError) as e:
        raise DiffError(f"最小区域面积必须是整数：{min_area_px!r}") from e
    if min_area < 1:
        raise DiffError("最小区域面积至少为 1 px²")

    raw_mask = (diff >= thr) & valid
    mask = _morphology(raw_mask, _odd_kernel(open_kernel, 3, "开运算核"),
                       _odd_kernel(close_kernel, 3, "闭运算核")) & valid

    labels, regions = _regions(mask, diff)
    kept = _pick_regions(regions, min_area)

    # binaryMask = 通过面积筛选后的精确结果（不是阈值原图）。
    keep_labels = np.array([r.label for r in kept], dtype=labels.dtype)
    if keep_labels.size:
        keep = np.isin(labels, keep_labels)
    else:
        keep = np.zeros_like(mask, dtype=bool)
    final_mask = (mask & keep)

    truncated = len(kept) > MAX_REGIONS
    shown = kept[:MAX_REGIONS]
    grid_h, grid_w = diff.shape
    # 相对面积按**本次分析范围**算（ROI 模式下不是整幅）。
    scope_px = max(1, int((x1 - x0) * (y1 - y0)))
    out_regions = []
    hole_regions = 0
    for rank, r in enumerate(shown):
        contour, contour_kind, holes = _contour_and_holes(labels, r)
        if holes:
            hole_regions += 1
        out_regions.append({
            "id": f"r{rank + 1:03d}",
            "rank": rank + 1,
            "areaPx": r.area,
            "relativeArea": round(r.area / float(scope_px), 6),
            # 坐标相对本次分析网格（ROI 时就是 ROI 局部坐标，见 analysisGrid.origin）。
            "bbox": [r.x, r.y, r.w, r.h],
            "centroid": [round(r.cx, 2), round(r.cy, 2)],
            "meanDiff": round(r.mean_diff, 4),
            "maxDiff": round(r.max_diff, 4),
            "rankScore": round(float(r.mean_diff * np.sqrt(r.area)), 4),
            "contour": contour,
            "contourKind": contour_kind,
            "holes": holes,
        })

    notes = list(warnings or [])
    if hole_regions:
        notes.append(
            f"{hole_regions} 个候选含孔洞；轮廓只给外边界，加入掩码前需人工确认填充范围。")
    if method == "ssim" and _skimage_ssim is None:
        notes.append("未安装 scikit-image，SSIM 使用 SciPy 兜底实现，数值与 skimage 可能略有差异。")

    diff8 = np.nan_to_num(np.clip(diff, 0.0, 1.0), nan=0.0, posinf=1.0, neginf=0.0)
    mask8 = np.where(final_mask, 255, 0).astype(np.uint8)

    took = (time.perf_counter() - started) * 1000 if elapsed_ms is None else elapsed_ms
    return {
        "method": method,
        "source": source,
        "imageA": {"name": None},
        "imageB": {"name": None},
        "analysisGrid": {
            "width": grid_w,
            "height": grid_h,
            "origin": [x0, y0],
            "scaleA": list(scale_a) if scale_a else [1.0, 1.0],
            "scaleB": list(scale_b) if scale_b else [1.0, 1.0],
        },
        "differenceMap": _b64_png((diff8 * 255.0).round().astype(np.uint8)),
        "binaryMask": _b64_png(mask8),
        "regions": out_regions,
        "truncated": truncated,
        "coverage": {
            "validPixels": int(valid.sum()),
            "gridPixels": int(valid.size),
            "validRatio": round(float(valid.sum()) / float(valid.size), 4),
        },
        "parameters": {
            "method": method,
            "sensitivity": sensitivity,
            "threshold": round(float(thr), 4),
            "minAreaPx": min_area,
            "blur": bool(blur),
            "openKernel": _odd_kernel(open_kernel, 3, "开运算核"),
            "closeKernel": _odd_kernel(close_kernel, 3, "闭运算核"),
            "ssimWindow": _odd_kernel(ssim_window, 7, "SSIM 窗口"),
            "ssimGaussian": bool(ssim_gaussian),
            "backend": "scipy+skimage" if (method == "ssim" and _skimage_ssim is not None) else "scipy",
        },
        "warnings": notes,
        "elapsedMs": int(round(took)),
    }


def analyze_payload(payload: dict, *, load_scene=None) -> dict:
    """HTTP 请求体 → 分析结果。

    ``image_a`` / ``image_b`` 支持两种形态：

    * ``{"grayPng": "data:image/png;base64,..."}`` —— 调用方已经采样到同一网格；
    * ``{"sceneId": "..."}`` —— 服务端从预览缓存读图（需要 ``load_scene`` 回调）。

    两者的 ``sourceWidth`` / ``sourceHeight`` 仅用于结果里的坐标映射。
    """
    if not isinstance(payload, dict):
        raise DiffError("请求体须为 JSON 对象")
    started = time.perf_counter()
    method = _method(payload.get("method"))
    sensitivity = _sensitivity(payload.get("sensitivity"))
    source = str(payload.get("source") or "preview")
    warnings: list[str] = []
    if source == "preview":
        warnings.append(
            "预览分析：基于显示层预览灰度计算差异，不等同于原始 DN 差分。"
            "预览本身逐图做过显示拉伸（直方图均衡），共同归一化只能对齐显示灰度，"
            "不能据此判断真实亮度变化。")
    elif source == "source-roi":
        warnings.append("原始 ROI 分析：只在候选区域内按原始分辨率重算，区域外仍是预览结果。")

    decoded: list[tuple[np.ndarray, np.ndarray, dict]] = []
    for key, label in (("image_a", "A"), ("image_b", "B")):
        spec = payload.get(key)
        if not isinstance(spec, dict):
            raise DiffError(f"缺少影像 {label}")
        if spec.get("grayPng"):
            gray, valid = decode_gray_png(str(spec["grayPng"]))
        elif spec.get("sceneId") and load_scene is not None:
            gray, valid, extra = load_scene(str(spec["sceneId"]))
            warnings.extend(extra or [])
        else:
            raise DiffError(f"影像 {label} 既没有图像数据也没有可读的场景 id")
        decoded.append((gray, valid, spec))
    (a, va, spec_a), (b, vb, spec_b) = decoded

    max_edge = resolve_max_edge(payload.get("maxEdge"))
    orig_a, orig_b = a.shape, b.shape
    if orig_a == orig_b and max(orig_a) <= max_edge:
        grid = orig_a
    else:
        # sceneId 路径读到的是整幅预览；先规划受限共同网格，再把两张图
        # （含有效掩码）采样过去，不能先加载/重采样大数组再拒绝。
        grid = common_grid_shape(orig_a, orig_b, max_edge)
    if a.shape != grid:
        a, va = _resize_pair(grid, a, va)
    if b.shape != grid:
        b, vb = _resize_pair(grid, b, vb)
    if a.shape != orig_a or b.shape != orig_b:
        warnings.append(
            f"两张影像尺寸不一致或预览超过分析上限，已采样到共同分析网格 "
            f"{grid[1]}×{grid[0]}。")

    grid_h, grid_w = a.shape

    result = analyze_arrays(
        a, b,
        valid_a=va, valid_b=vb,
        method=method,
        sensitivity=sensitivity,
        threshold=payload.get("threshold"),
        min_area_px=payload.get("minAreaPx", DEFAULT_MIN_AREA_PX),
        blur=bool(payload.get("blur", True)),
        open_kernel=payload.get("openKernel", 3),
        close_kernel=payload.get("closeKernel", 3),
        ssim_window=payload.get("ssimWindow", 7),
        ssim_gaussian=bool(payload.get("ssimGaussian", False)),
        roi=payload.get("roi"),
        source=source,
        warnings=warnings,
    )
    result["imageA"] = {
        "id": spec_a.get("id"), "name": spec_a.get("name"),
        "sourceWidth": spec_a.get("sourceWidth"), "sourceHeight": spec_a.get("sourceHeight"),
        "thumbWidth": spec_a.get("thumbWidth"), "thumbHeight": spec_a.get("thumbHeight"),
        "gridWidth": grid_w, "gridHeight": grid_h,
    }
    result["imageB"] = {
        "id": spec_b.get("id"), "name": spec_b.get("name"),
        "sourceWidth": spec_b.get("sourceWidth"), "sourceHeight": spec_b.get("sourceHeight"),
        "thumbWidth": spec_b.get("thumbWidth"), "thumbHeight": spec_b.get("thumbHeight"),
        "gridWidth": grid_w, "gridHeight": grid_h,
    }
    if source == "preview":
        result["analysisGrid"]["mode"] = "preview"
    else:
        result["analysisGrid"]["mode"] = "source"
    result["elapsedMs"] = int(round((time.perf_counter() - started) * 1000))
    result["operation"] = "difference-localization"
    return result


def resize_to(shape, b: np.ndarray, valid: np.ndarray):
    """把 B 重采样到 A 的网格（同一地面范围）。供原始 ROI 分析共用。"""
    return _resize_pair(shape, b, valid)


def _resize_pair(shape, b: np.ndarray, valid: np.ndarray):
    """把 B 重采样到 A 的网格（同一地面范围）。

    灰度按**有效权重重采样并归一化**：无效像素先清零再参与双线性插值，权重图
    同步插值；只要插值邻点里有一个无效，输出就判无效。这样有限 NoData 哨兵
    （如 -9999）不会混进插值结果，有效性与灰度共用同一套规则。
    """
    h, w = shape
    valid = np.asarray(valid, dtype=bool)
    if b.shape == (h, w):
        return b.astype(np.float32), valid
    bh, bw = b.shape
    ys = (np.arange(h, dtype=np.float64) + 0.5) * (bh / max(1, h)) - 0.5
    xs = (np.arange(w, dtype=np.float64) + 0.5) * (bw / max(1, w)) - 0.5
    gy, gx = np.meshgrid(ys, xs, indexing="ij")
    bf = np.nan_to_num(np.asarray(b, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    vals = np.where(valid, bf, 0.0)
    wts = valid.astype(np.float32)
    num = map_coordinates(vals, [gy, gx], order=1, mode="nearest")
    den = map_coordinates(wts, [gy, gx], order=1, mode="nearest")
    rv = den > 1.0 - 1e-4              # 全部插值邻点有效才算有效
    rb = np.where(rv, num / np.maximum(den, 1e-6), 0.0)
    return rb.astype(np.float32), rv


# --------------------------------------------------------------------------
# 原始 ROI 分析（可选）：只读无压缩条带 TIFF 的有界窗口
# --------------------------------------------------------------------------

_TAG_GDAL_NODATA = 42113


def _tiff_nodata(path, scalars, arrays) -> float | None:
    """读 GDAL_NODATA（ASCII）标签；读不到或不是数字时返回 None。"""
    try:
        if _TAG_GDAL_NODATA in arrays:
            count, size, loc = arrays[_TAG_GDAL_NODATA]
            if isinstance(loc, (bytes, bytearray)):
                data = bytes(loc)
            else:
                with open(path, "rb") as f:
                    f.seek(int(loc))
                    data = f.read(int(count) * int(size))
        elif _TAG_GDAL_NODATA in scalars:
            value = int(scalars[_TAG_GDAL_NODATA])
            data = bytes([value]) if 0 <= value <= 255 else b""
        else:
            return None
        text = data.rstrip(b"\x00").decode("ascii", "ignore").strip()
        if not text:
            return None
        return float(text)
    except Exception:  # noqa: BLE001 - 标签损坏不影响读取
        return None


def read_source_window(path: str | Path, x0: int, y0: int, x1: int, y1: int,
                       *, max_edge: int = DEFAULT_MAX_EDGE):
    """读取无压缩条带 TIFF 的窗口。

    返回 ``(float32 原始 DN, valid, (W, H), (ax0, ay0, ax1, ay1))``：最后一项是
    **实际读取**（夹紧到影像边界后）的窗口，调用方必须用它做坐标映射，不能拿
    请求值当成已读范围。有限 NoData 哨兵（GDAL_NODATA）会从 valid 中排除。

    只读采样行、行内只取采样列：一景两万级宽度、2048 行窗口约一百多 MB 顺序读，
    不会把整幅解进内存。压缩 / tiled / 混合位深一律拒绝（与 ``preview_jpg`` 的
    预览生成同一门槛），调用方据此退回预览分析。

    **只支持单波段输入**：多波段 TIFF 不猜「取第一波段还是灰度合成」，直接拒绝，
    避免按错误 DN 算出一份看起来正常的结果。``SampleFormat`` 支持标量与数组
    两种写法，各波段类型必须一致。
    """
    src = Path(path)
    try:
        info = preview_jpg.probe_tiff(src)
    except preview_jpg.PreviewError as e:
        raise DiffError(f"无法读取影像头：{src.name}（{e}）") from e
    W, H = int(info["W"]), int(info["H"])
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(x1)), min(H, int(y1))
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise DiffError("ROI 超出影像范围或过小")
    if info["compression"] != 1 or info["tiled"]:
        raise DiffError("原始 ROI 分析需要无压缩条带 TIFF（压缩/tiled 请退回预览分析）")
    if int(info["spp"]) != 1:
        # 不猜「取第一波段还是灰度合成」：多波段输入直接拒绝，避免按错误 DN
        # 算出一份看起来正常的结果。需要彩色差分时先在外层合成单波段。
        raise DiffError(
            f"原始 ROI 分析只支持单波段 TIFF（检测到 {int(info['spp'])} 个波段）；"
            "请退回预览分析，或改用单波段影像。")
    if int(info["sample_format"]) not in (1, 2, 3):
        raise DiffError(f"不支持的 SampleFormat={info['sample_format']}")

    pf = preview_jpg
    is_big, endian, ifd_off = pf._parse_tiff_header(path)
    scalars, arrays = pf._read_ifd(path, is_big, endian, ifd_off)
    rps = scalars.get(pf._TAG_RPS) or H
    bits = info["bits"]
    spp = int(info["spp"])
    planar = int(info["planar"])
    dt = pf._np_dtype(bits, int(info["sample_format"]), endian)
    bpp = bits // 8
    stride = spp if planar == 1 else 1
    row_bytes = W * bpp * stride
    offs = (pf._read_array(path, arrays[pf._TAG_OFFS], endian) if pf._TAG_OFFS in arrays
            else np.array([scalars[pf._TAG_OFFS]], dtype=np.int64))
    lens = (pf._read_array(path, arrays[pf._TAG_BYTES], endian) if pf._TAG_BYTES in arrays
            else np.array([scalars[pf._TAG_BYTES]], dtype=np.int64))

    w, h = x1 - x0, y1 - y0
    scale = min(1.0, max_edge / max(w, h))
    pw, ph = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    cols = (np.round(np.arange(pw) * (w - 1) / (pw - 1)).astype(np.int64) + x0
            if pw > 1 else np.array([x0], dtype=np.int64))
    rows = ([int(round(i * (h - 1) / (ph - 1))) + y0 for i in range(ph)]
            if ph > 1 else [y0])

    out = np.empty((ph, pw), dtype=np.float32)
    with open(path, "rb") as f:
        for i, r in enumerate(rows):
            si = r // rps
            row_off = (r % rps) * row_bytes
            avail = int(lens[si]) if si < len(lens) else 0
            if row_off >= avail:
                raise DiffError("条带偏移越界（文件布局与头信息不一致）")
            f.seek(int(offs[si]) + row_off)
            data = f.read(min(avail - row_off, row_bytes))
            if len(data) < row_bytes:
                raise DiffError("读取条带失败（文件被截断？）")
            arr = np.frombuffer(data, dtype=dt, count=len(data) // bpp)
            out[i] = arr[cols * stride]
    valid = np.isfinite(out)
    nodata = _tiff_nodata(path, scalars, arrays)
    if nodata is not None and np.isfinite(nodata):
        # 只有有限哨兵才做值比较；GDAL_NODATA 可以是 "nan"/"inf"，
        # 那类已经由上面的 isfinite(out) 排除，不能再拿 isfinite(nodata) 与掩码相与。
        valid &= out != np.float32(nodata)
    if info.get("photometric") == 0 and np.issubdtype(dt, np.integer):
        out = np.iinfo(dt).max - out
    return out.astype(np.float32), valid, (W, H), (x0, y0, x1, y1)
