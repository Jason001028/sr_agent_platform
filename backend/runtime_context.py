"""Explicit runtime dependencies shared by API and agent entry points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from backend.config import Config
    from backend.services.store import Store
    from backend.tools.contract import ToolRegistry


@dataclass(frozen=True)
class AppContext:
    """已构造的应用依赖快照，不承载业务决策。"""

    config: "Config"
    store: "Store"
    tool_registry: "ToolRegistry"
    scenes_root: Optional[Path]
    poll_sec: float
    raster_runner: Optional[object] = None
