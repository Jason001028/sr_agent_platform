"""fix_bad_lines — first real workflow-level agent tool (thin wrapper).

Business logic lives in backend/services/fix_bad_lines.py; this module is the
agent-facing shell: LLM description + JSON-schema params (the contract) and
the ok/err boundary. Contract errors never raise — the loop feeds err() back
to the model for self-healing (see agent/loop.py).
"""

from __future__ import annotations

from backend.services import fix_bad_lines as svc
from .contract import err, ok, tool


@tool(
    name="fix_bad_lines",
    description=(
        "检测并修复二维灰度影像中整行或整列亮度异常：以每行/列的中位数与邻域中位数比较，"
        "偏差超过 threshold 倍稳健标准差（1.4826×MAD，下限 1e-6）时标记。"
        "修复用邻域未标记行/列对应像素的中位数，邻域无参考时使用全图未标记行/列；结果取整并转回源 dtype。"
        "返回 size、dtype、axis、bad_lines（从 0 起的检测索引）和 count，不代表所有异常均已消除；连续异常可能漏检。"
        "省略 output_path 仅在内存计算并返回统计；指定后保存修复影像并可能覆盖文件，输出不保证保留地理参考等元数据。"
        "Pillow 整图读入内存，不适合直接处理超大 TIF，也不能替代一般视觉分析或全面质量验收。"
    ),
    params_schema={
        "type": "object",
        "properties": {
            "input_path": {
                "type": "string",
                "description": "后端可读取、Pillow 可解码的二维灰度影像文件路径；不是 Viewer 本地文件名、影像 id 或场景目录。",
            },
            "output_path": {
                "type": "string",
                "description": "后端输出文件路径；省略或空串不写文件。指定后经 Pillow 保存，已有同名文件可被覆盖，与 input_path 相同会覆盖输入。",
            },
            "axis": {
                "type": "string", "enum": ["row", "column"], "default": "row",
                "description": "检测方向：row 为水平整行，column 为垂直整列；省略为 row，返回索引属于此方向。",
            },
            "window": {
                "type": "integer", "minimum": 1, "default": 5,
                "description": "邻域半宽，单位为行/列数，至少 1，省略为 5；检测时取两侧各至多 window 行/列（排除自身），修复时排除已标记异常。",
            },
            "threshold": {
                "type": "number", "minimum": 0, "default": 3.0,
                "description": "非负稳健 z-score 阈值，省略为 3.0；绝对偏差除以稳健标准差严格大于此值才标记，越低通常越敏感。",
            },
        },
        "required": ["input_path"],
    },
)
def run_fix_bad_lines(**params) -> dict:
    try:
        input_path = str(params["input_path"])
        output_path = params.get("output_path")
        axis = params.get("axis", "row")
        window = int(params.get("window", 5))
        threshold = float(params.get("threshold", 3.0))
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")

    if axis not in ("row", "column"):
        return err("axis must be 'row' or 'column'")
    if window < 1:
        return err("window must be >= 1")
    if threshold < 0:
        return err("threshold must be >= 0")

    try:
        stats = svc.run(input_path, output_path=output_path, axis=axis,
                        window=window, threshold=threshold)
    except FileNotFoundError:
        return err(f"input not found: {input_path}")
    except Exception as e:  # noqa: BLE001 — contract boundary: tool never raises
        return err(f"{type(e).__name__}: {e}")
    return ok(stats)
