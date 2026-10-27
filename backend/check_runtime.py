"""Dependency presence preflight; run with the Python used by sr-api.service.

Uses only the standard library so missing application dependencies cannot prevent
this diagnostic from running. It neither installs packages nor starts the API.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

REQUIRED_MODULES = (
    ("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("numpy", "numpy"),
    ("PIL", "pillow"), ("openai", "openai"), ("yaml", "PyYAML"),
    ("jsonschema", "jsonschema"),
)


def main() -> int:
    required = list(REQUIRED_MODULES)
    if sys.version_info < (3, 10):
        required.append(("eval_type_backport", "eval-type-backport"))
    missing = [package for module, package in required
               if importlib.util.find_spec(module) is None]
    print(f"API Python: {sys.executable}")
    if not missing:
        print("Runtime dependency modules found. Run python -m pip check and API import validation next.")
        return 0
    requirements = Path(__file__).with_name("requirements-api.txt")
    source_requirements = Path(__file__).resolve().parents[1] / "deploy" / "requirements-api.txt"
    if not requirements.is_file() and source_requirements.is_file():
        requirements = source_requirements
    print("Missing runtime dependencies: " + ", ".join(missing), file=sys.stderr)
    print(f'Install using this interpreter: "{sys.executable}" -m pip install -r "{requirements}"', file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
