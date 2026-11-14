"""Python 3.9 运行时注解兼容：`X | None` 只能出现在带 future import 的模块里。

平台 API venv 由 py3.9 建出（deploy/README.md §三）。py3.9 下注解在 def/class 创建
时就求值，`float | None` 抛 ``TypeError: unsupported operand type(s) for |``；
requirements 里的 eval-type-backport 只覆盖 fastapi/pydantic 那条求值链，
dataclass 字段不走它——2026-11-14 的 LTD 500 就是这么来的。
"""
import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _union_annotation_lines(tree):
    def lines_of(node):
        return [sub.lineno for sub in ast.walk(node)
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)]

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield from lines_of(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                yield from lines_of(node.returns)
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                    *([node.args.vararg] if node.args.vararg else []),
                    *([node.args.kwarg] if node.args.kwarg else [])]
            for arg in args:
                if arg.annotation is not None:
                    yield from lines_of(arg.annotation)


def test_pep604_annotations_only_in_future_import_modules():
    offenders = []
    for path in sorted(BACKEND.rglob('*.py')):
        if 'tests' in path.parts:
            continue
        source = path.read_text(encoding='utf-8-sig')
        if 'from __future__ import annotations' in source:
            continue
        lines = sorted(set(_union_annotation_lines(ast.parse(source))))
        if lines:
            offenders.append(f"{path.relative_to(BACKEND.parent).as_posix()}:{lines}")
    assert offenders == [], f"缺少 `from __future__ import annotations`：{offenders}"
