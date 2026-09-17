"""Scene / preview path resolution for the REST API (whitelist-guarded).

盘阵路径双层保险（09-02 决策）：nginx 整块暴露场景根，后端再按白名单校验
返回/生成的 path —— 拒绝 `../` 穿越、白名单外绝对路径、fake 占位路径。

约定
----
* SR_SCENES_ROOT   盘阵场景根目录（disk 后端与白名单根；unset → fake 回退）。
* SR_PREVIEWS_ROOT 可选：预览 JPG 缓存根。默认 = 与源同目录
                   （<源dir>/<basename>.preview.jpg）。若设置则必须落在
                   SR_SCENES_ROOT 之下（nginx 单根 alias 即可同时覆盖
                   raw TIF 与预览 JPG）。URL 一律用相对 scenes 根的
                   `/disk-array/<rel>` 表达。
* 场景 id（/api/scenes/{id}）两种形态：
    - **库行** = base64url(相对 scenes 根的 rel path)，无歧义、URL 安全；
    - **手工行**（用户手填/反推的盘阵路径，见 POST /api/scenes/resolve）
      = `~` + base64url(绝对路径)。`~` 不在 base64url 字母表里，与库行
      天然不冲突；解码后经 `pathguard.ensure_allowed` 兜白名单。
  fake/越权 id 在 resolve 阶段被拒。
"""

from __future__ import annotations

import base64
import os
import urllib.parse
from pathlib import Path

from backend.pathguard import PathDeniedError, ensure_allowed
from backend.pathguard import is_within as _is_within
from backend.services.preview_jpg import PreviewError

_DISK_URL_PREFIX = "/disk-array/"   # nginx `location /disk-array/ { alias <root>/; }`

#: 手工行 id 的前缀。`~` 不在 base64url 字母表里，与库行 rel id 不会撞。
_ABS_ID_PREFIX = "~"

__all__ = [
    "PathDeniedError",
    "scenes_root", "previews_root", "disk_url_prefix",
    "ensure_within", "rel_of_scene", "scene_id", "scene_id_to_abs",
    "rel_url", "preview_jpg_path", "preview_jpg_for",
]


def scenes_root() -> Path | None:
    raw = os.environ.get("SR_SCENES_ROOT")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def previews_root() -> Path | None:
    """Optional preview cache root (must sit under scenes_root)."""
    raw = os.environ.get("SR_PREVIEWS_ROOT")
    return Path(raw) if raw else None


def disk_url_prefix() -> str:
    return os.environ.get("SR_DISK_URL_PREFIX", _DISK_URL_PREFIX)


def ensure_within(path: str | Path, root: Path) -> Path:
    """Validate an absolute candidate path is a file under the whitelist root.

    Resolves symlinks (../ 与链接逃逸都过不了 realpath 比较)。Raise
    PathDeniedError otherwise.
    """
    if not root or not root.is_dir():
        raise PathDeniedError("盘阵根未配置（SR_SCENES_ROOT）")
    p = Path(path)
    if "<fake>" in str(p):
        raise PathDeniedError("fake 占位路径不可访问")
    if not p.is_absolute():
        raise PathDeniedError("仅接受绝对路径")
    if not _is_within(p, root):
        raise PathDeniedError("路径在白名单之外")
    if not p.is_file():
        raise PathDeniedError("场景文件不存在")
    return p


# --------------------------------------------------------------------------
# 场景 id ↔ rel path
# --------------------------------------------------------------------------
def rel_of_scene(abs_path: Path, root: Path) -> str:
    """Scene rel path under root (posix); abs_path must already be inside."""
    try:
        rel = Path(abs_path).resolve().relative_to(root.resolve())
    except ValueError as e:
        raise PathDeniedError("路径在白名单之外") from e
    return rel.as_posix()


def scene_id(rel: str) -> str:
    """Opaque, URL-safe id from a rel path (base64url without padding)."""
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")


def scene_id_abs(abs_path: str | Path) -> str:
    """手工行的场景 id：`~` + base64url(绝对路径)，URL 安全、与库行不冲突。"""
    posix = Path(abs_path).as_posix().encode("utf-8")
    body = base64.urlsafe_b64encode(posix).decode("ascii").rstrip("=")
    return _ABS_ID_PREFIX + body


def scene_id_to_abs(id_token: str, root: Path | None = None) -> Path:
    """Resolve an opaque scene id to a whitelisted absolute file path.

    两种形态都吃：`~` 前缀 = 绝对路径（走 pathguard 前缀白名单），其余 =
    legacy 的"相对 SR_SCENES_ROOT 的 rel"（语义逐字未变，保证库行零回归）。
    """
    if id_token.startswith(_ABS_ID_PREFIX):
        body = id_token[len(_ABS_ID_PREFIX):]
        try:
            pad = "=" * (-len(body) % 4)
            posix = base64.urlsafe_b64decode(body + pad).decode("utf-8")
        except Exception as e:  # noqa: BLE001 — 坏 id 一律拒绝
            raise PathDeniedError(f"无效场景 id：{e}") from e
        # 用 is_absolute() 而不是 startswith("/")：开发机（Windows）上手工 id
        # 编出来的是 `C:/...`，同样绝对，只是不以 `/` 开头。
        if not Path(posix).is_absolute():
            raise PathDeniedError("无效场景 id（非绝对路径）")
        return ensure_allowed(posix, kind="file")

    if root is None:
        raise PathDeniedError("盘阵根未配置（SR_SCENES_ROOT）")
    try:
        pad = "=" * (-len(id_token) % 4)
        rel = base64.urlsafe_b64decode(id_token + pad).decode("utf-8")
    except Exception as e:  # noqa: BLE001 — 坏 id 一律拒绝
        raise PathDeniedError(f"无效场景 id：{e}") from e
    if ".." in rel.split("/"):
        raise PathDeniedError("场景 id 含路径穿越")
    abs_path = ensure_within(root / rel, root)
    return abs_path


def rel_url(path: Path, root: Path) -> str:
    """URL path for a file under root: `/disk-array/<quoted rel segments>`."""
    rel = rel_of_scene(path, root)
    quoted = "/".join(urllib.parse.quote(seg, safe="") for seg in rel.split("/"))
    return disk_url_prefix() + quoted


def preview_jpg_path(source_abs: Path, root: Path) -> Path:
    """Cache location for a scene's preview JPG.

    Default = `<源同目录>/<basename>.preview.jpg`（09-02 决策首选）；若配了
    SR_PREVIEWS_ROOT（必须仍在 scenes root 内）则放 `<previews_root>/<rel 目录>
    /<basename>.preview.jpg`，nginx 单根 alias 下 URL 不变。
    """
    if not _is_within(source_abs, root):
        raise PathDeniedError("源路径在白名单之外")
    pre = previews_root()
    if pre is not None:
        if not _is_within(pre, root):
            raise PreviewError(
                f"SR_PREVIEWS_ROOT（{pre}）必须在 SR_SCENES_ROOT 之下，"
                "否则 nginx 单根暴露覆盖不到")
        rel_dir = source_abs.resolve().relative_to(root.resolve()).parent
        return (pre.resolve() / rel_dir) / (source_abs.stem + ".preview.jpg")
    return source_abs.with_suffix(".preview.jpg")


def preview_jpg_for(source_abs: Path, root: Path | None) -> Path:
    """预览 JPG 的落点：库内沿用老规则，库外（手工路径）落源同目录。

    库内（source 在 SR_SCENES_ROOT 之下）语义与 `preview_jpg_path` 完全一致
    （含 SR_PREVIEWS_ROOT 缓存搬家，URL 仍可被 nginx 单根 alias 覆盖）。
    库外没有 scenes 根可用，直接 `<源同目录>/<stem>.preview.jpg` —— 这类
    场景不走 nginx 静态 URL，由 `GET /api/scenes/{id}/preview` 直接回字节，
    所以不需要在 URL 层面可映射。
    """
    if root is not None and _is_within(source_abs, root):
        return preview_jpg_path(source_abs, root)
    return source_abs.with_suffix(".preview.jpg")
