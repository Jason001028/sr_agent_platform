"""平台级设置：服务端 SQLite 持久化，所有客户端机器共用一份。

与 `localStorage` 里的本机偏好分开：这些值改一次，连到同一后端的其它机器
（刷新页面 / 重新打开设置面板后）也看到同一份。目前只有「真分辨率长边上限」，
但表是 key/value，加下一项不用再动 schema。

为什么要有上限：长边 ≤ 上限的预览按该档位的真实采样交付；超过它浏览器画布
（16384² 面积）与渲染内存必然截断。上限放在服务端，前端解码、服务端生成和
缓存判据才能用同一个数；只改前端会出现「服务端给了 16384、前端仍按 4096 截」。
"""
from __future__ import annotations

import os
import threading
import time

from backend.services.store import Store, default_store

PREVIEW_MAX_EDGE_KEY = "preview_max_edge"

#: 三档都以 2 的幂增长；16384 = 4096×4，正好是 Chromium 画布 16384² 的面积上限。
#: 16383 刻意不做选项：长边只差 1 像素却会误导用户以为「更安全」，实际面积开销
#: 几乎相同，反而让状态栏出现一个奇怪的分母。真要在低内存机器上省，选 8192。
PREVIEW_MAX_EDGE_OPTIONS = (4096, 8192, 16384)
DEFAULT_PREVIEW_MAX_EDGE = 16384

DEFAULTS = {PREVIEW_MAX_EDGE_KEY: DEFAULT_PREVIEW_MAX_EDGE}

# 缓存 2 秒：一次场景列表会逐行走 cache_meta，不能每行开一次 SQLite 查询；
# 但改设置后要在下一拍生效，所以 TTL 不能太长。API 保存时会主动失效。
_CACHE_TTL = 2.0
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


class SettingsError(ValueError):
    """非法设置值（HTTP 层转 400）。"""


def _cache_key(store: Store) -> str | None:
    # `:memory:` 每个 Store 都是独立库，路径相同却不能共用缓存。
    path = str(getattr(store, "path", "") or "")
    return None if not path or path == ":memory:" else path


def _normalize(values: dict) -> dict:
    out = dict(DEFAULTS)
    for key in DEFAULTS:
        if key not in values:
            continue
        value = values[key]
        if key == PREVIEW_MAX_EDGE_KEY:
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value not in PREVIEW_MAX_EDGE_OPTIONS:
                # 坏值（手工改库、旧客户端）一律回落默认，不把预览打成 500。
                continue
        out[key] = value
    return out


def load_settings(store: Store | None = None, *, refresh: bool = False) -> dict:
    """当前平台设置（缺省值补齐）。`refresh=True` 绕过 2 秒缓存。"""
    store = store or default_store()
    key = _cache_key(store)
    now = time.monotonic()
    if key is not None and not refresh:
        with _cache_lock:
            hit = _cache.get(key)
            if hit is not None and now - hit[0] < _CACHE_TTL:
                return dict(hit[1])
    if key is not None and not os.path.exists(key):
        # 库还没建过：直接回默认，别为了读一个设置把 sr_agent.db 建出来。
        return dict(DEFAULTS)
    try:
        saved = store.list_platform_settings()
    except Exception:  # noqa: BLE001 — 读库失败按默认值走，预览不能因此不可用
        saved = {}
    merged = _normalize(saved)
    if key is not None:
        with _cache_lock:
            _cache[key] = (now, dict(merged))
    return merged


def save_settings(patch: dict, store: Store | None = None) -> dict:
    """校验并写入部分设置；返回写入后的全量值。"""
    if not isinstance(patch, dict) or not patch:
        raise SettingsError("设置内容不能为空")
    unknown = sorted(set(patch) - set(DEFAULTS))
    if unknown:
        raise SettingsError(f"未知设置项：{'、'.join(unknown)}")
    if PREVIEW_MAX_EDGE_KEY in patch:
        value = patch[PREVIEW_MAX_EDGE_KEY]
        if isinstance(value, bool) or not isinstance(value, int) \
                or value not in PREVIEW_MAX_EDGE_OPTIONS:
            raise SettingsError(
                "真分辨率长边上限只支持 "
                + " / ".join(map(str, PREVIEW_MAX_EDGE_OPTIONS)))
    store = store or default_store()
    saved = store.save_platform_settings(patch)
    merged = _normalize(saved)
    key = _cache_key(store)
    if key is not None:
        with _cache_lock:
            _cache[key] = (time.monotonic(), dict(merged))
    return merged


def configured_preview_max_edge(store: Store | None = None) -> int:
    """服务端生成 / 缓存判据共用的当前长边上限。"""
    return int(load_settings(store)[PREVIEW_MAX_EDGE_KEY])
