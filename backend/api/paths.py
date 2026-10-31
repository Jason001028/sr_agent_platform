"""Scene / preview path resolution for the REST API (whitelist-guarded).

盘阵路径双层保险（09-02 决策）：nginx 整块暴露场景根，后端再按白名单校验
返回/生成的 path —— 拒绝 `../` 穿越、白名单外绝对路径、fake 占位路径。

约定
----
* SR_SCENES_ROOT   盘阵场景根目录（disk 后端与白名单根；unset → fake 回退）。
* SR_PREVIEWS_ROOT 可选：预览 JPG 缓存根。默认 = 与源同目录
                   （<源dir>/<源 stem>_preview.jpg）。若设置则必须落在
                   SR_SCENES_ROOT 之下（nginx 单根 alias 即可同时覆盖
                   raw TIF 与预览 JPG）。URL 一律用相对 scenes 根的
                   `/disk-array/<rel>` 表达。
* SR_PREVIEW_ROOTS 盘阵外、只读预览前缀（`;` 分隔，缺省空）。这里的源
                   没有静态 URL，预览落 `SR_PREVIEW_CACHE_ROOT`（缺省
                   `<SR_TEMP_PREVIEWS_ROOT>/generic`），按源路径哈希隔离。
                   权限边界见 `backend/pathguard.py`：能看，不能提交/写掩码。
* 场景 id（/api/scenes/{id}）两种形态：
    - **库行** = base64url(相对 scenes 根的 rel path)，无歧义、URL 安全；
    - **手工行**（用户手填/反推的盘阵路径，见 POST /api/scenes/resolve）
      = `~` + base64url(绝对路径)。`~` 不在 base64url 字母表里，与库行
      天然不冲突；解码后经 `pathguard.ensure_allowed` 兜白名单。
  fake/越权 id 在 resolve 阶段被拒。
"""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile
import urllib.parse
from pathlib import Path

from backend.pathguard import (PathDeniedError, ensure_allowed,
                               ensure_preview_allowed, is_preview_only,
                               preview_roots)
from backend.pathguard import is_within as _is_within
from backend.services.preview_jpg import PreviewError, source_key

_DISK_URL_PREFIX = "/disk-array/"   # nginx `location /disk-array/ { alias <root>/; }`

#: 手工行 id 的前缀。`~` 不在 base64url 字母表里，与库行 rel id 不会撞。
_ABS_ID_PREFIX = "~"

__all__ = [
    "PathDeniedError",
    "scenes_root", "previews_root", "disk_url_prefix",
    "ensure_within", "rel_of_scene", "scene_id", "scene_id_to_abs",
    "rel_url", "preview_jpg_name", "preview_jpg_path", "preview_jpg_for",
    "drop_preview_path", "legacy_preview_path",
    "generic_preview_root", "generic_preview_path",
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


def scene_id_to_abs(id_token: str, root: Path | None = None, *,
                    allow_preview: bool = False) -> Path:
    """Resolve an opaque scene id to a whitelisted absolute file path.

    两种形态都吃：`~` 前缀 = 绝对路径（走 pathguard 前缀白名单），其余 =
    legacy 的"相对 SR_SCENES_ROOT 的 rel"（语义逐字未变，保证库行零回归）。

    ``allow_preview=True`` 时，`~` 形态额外接受 `SR_PREVIEW_ROOTS` 之下的路径
    （盘阵外的只读预览源）。**只有查看链路传它** —— 提交、掩码、siblings、清除
    缓存、隐藏仍走默认值，盘阵外的一张 TIFF 换不来那些业务权限。
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
        checker = ensure_preview_allowed if allow_preview else ensure_allowed
        return checker(posix, kind="file")

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


#: 共用同一个 stem 会撞缓存落点的栅格扩展名。盘阵里 `.tif` / `.tiff` 并存不是
#: 常态，但两份都合法，且都按 stem 拼预览名。
_RASTER_EXTS = (".tif", ".tiff")


def preview_jpg_name(source_abs: Path) -> str:
    """预览 JPG 的文件名 —— 默认 `<源 stem>_preview.jpg`。

    2026-09-22 起由 `<stem>.preview.jpg` 改名而来。理由不是好看：同一份栅格被三条链
    各生成一份（急生成 / 打开时的惰性 / 拖入链），点号那份与下划线那份只差一个字符，
    真机上就是一个场景目录里躺着两个几乎同名、内容相同的文件，谁也说不清该看哪个。
    现在三条链落同一个名字，点号那份**没有任何读者**了 —— 旧文件由
    `legacy_preview_path` 在每条链处理到那份栅格时顺手删掉。

    **同 stem 的 `.tif` / `.tiff` 同时存在时**（只 stat 固定候选，不列目录）改用
    `<stem>_<ext>_<源路径哈希>_preview.jpg`：两份源若共用一个落点，A 生成完、B
    覆盖、A 的响应在发送前读到的是 B 的像素 —— 源身份戳挡不住这个「生成到发送
    之间被覆盖」的窗口，只能从落点上把两份源分开。**身份段必须含源路径哈希**：
    只拼扩展名的 `_tif` 本身可以是另一份源的 stem（`same_tif.tif` → 原名正好是
    `same_tif_preview.jpg`），同名三份并存时仍会撞。没有冲突时保持原名，既有
    缓存与 URL 逐一不变。
    """
    src = Path(source_abs)
    stem = src.stem
    ext = src.suffix.lower()
    if ext in _RASTER_EXTS:
        for other in _RASTER_EXTS:
            if other == ext:
                continue
            # 大小写都试：Linux 上 `SAME.TIFF` 与 `same.tiff` 是两个文件。
            if any(src.with_name(stem + cand).is_file()
                   for cand in (other, other.upper())):
                return f"{stem}_{ext.lstrip('.')}_{source_key(src)}_preview.jpg"
    return stem + "_preview.jpg"


def preview_jpg_path(source_abs: Path, root: Path) -> Path:
    """Cache location for a scene's preview JPG.

    Default = `<源同目录>/<源 stem>_preview.jpg`；若配了 SR_PREVIEWS_ROOT（必须仍在
    scenes root 内）则放 `<previews_root>/<rel 目录>/<源 stem>_preview.jpg`，nginx
    单根 alias 下 URL 不变。**名字与档位无关**：换档位是原地覆盖同一份、靠戳里的 div
    判废重生成（见 `preview_jpg.rule_stamp`）。
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
        return (pre.resolve() / rel_dir) / preview_jpg_name(source_abs)
    return source_abs.with_name(preview_jpg_name(source_abs))


def preview_jpg_for(source_abs: Path, root: Path | None) -> Path:
    """预览 JPG 的落点：库内 `preview_jpg_path`，库外（手工路径）落源同目录。

    三个分支**文件名形态相同**，差别只在目录：

    * 库内（在 `SR_SCENES_ROOT` 之下）→ `preview_jpg_path`（可被
      `SR_PREVIEWS_ROOT` 搬去镜像树）。
    * 盘阵外、但配在 `SR_PREVIEW_ROOTS` 之下的只读预览源 → `generic_preview_path`
      （隔离的缓存根，不往别人的目录里写东西）。
    * 其余手工路径（盘阵前缀内、场景根外）→ `drop_preview_path`，落源同目录，
      与既有行为一致。

    后两类都没有静态 URL，由 `GET /api/scenes/{id}/preview` 直接回字节。
    """
    if root is not None and _is_within(source_abs, root):
        return preview_jpg_path(source_abs, root)
    # 没配 SR_PREVIEW_ROOTS 时直接走原来的落点，不多做一次白名单 stat。
    if preview_roots() and is_preview_only(source_abs):
        return generic_preview_path(source_abs)
    return drop_preview_path(source_abs)


def generic_preview_root() -> Path:
    """盘阵外预览源的隔离缓存根（`SR_PREVIEW_CACHE_ROOT`）。

    缺省落在 `SR_TEMP_PREVIEWS_ROOT`（或系统临时目录）下的 `generic/`：
    每日清理只删按日期命名的桶，`generic/` 不在其列，所以它跟着源文件长期
    有效，靠 `ensure_preview_jpg` 的 mtime + 规则签名判废。

    **必须由服务端指定**：盘阵外的源目录可能只读，也可能属于别的用途，
    预览 JPG 不该写进去。
    """
    raw = os.environ.get("SR_PREVIEW_CACHE_ROOT")
    if raw:
        return Path(raw)
    base = os.environ.get("SR_TEMP_PREVIEWS_ROOT")
    root = Path(base) if base else Path(tempfile.gettempdir()) / "sr-tmp-previews"
    return root / "generic"


def generic_preview_path(source_abs: Path) -> Path:
    """盘阵外预览源的落点：`<缓存根>/<sha256(绝对路径)[:16]>_<stem>_preview.jpg`。

    路径哈希进文件名是**来源隔离**：两个目录里同名的 `a.tif` 不会共用一份缓存，
    也不会因为谁先打开就被另一份顶掉。文件名仍以 `_preview.jpg` 结尾，与
    `legacy_preview_path` / `preview_div_of` 那套判据一致。
    """
    src = Path(source_abs)
    key = hashlib.sha256(src.as_posix().encode("utf-8")).hexdigest()[:16]
    return generic_preview_root() / f"{key}_{src.stem}_preview.jpg"


def drop_preview_path(source_abs: Path) -> Path:
    """拖入链的落点：**恒** `<源同目录>/<源 stem>_preview.jpg`。

    与 `preview_jpg_for` 现在**同名**（2026-09-22 起），差别只剩目录：

    1. **不吃 `SR_PREVIEWS_ROOT`** —— 拖入链要的是「生成一次长期可用」，落进生产场景目录
       才成立；搬去缓存根就又变成缓存了。
    2. **恒落源同目录**，不区分库内库外 —— 拖入的生产场景本来就在盘阵里，而库外路径
       （手工粘贴的任意绝对路径）也没有第二个合理的落点。
    3. **不进场景库列表**：`_preview.jpg` 不在 `scene_search.is_scene_file` 的白名单里
       （要求 stem == 目录名 或 PAN），所以它不会被当成新的一类图收进来。

    库外路径由 `ensure_allowed` 的前缀白名单把关，白名单之外的在调用方就被挡了。
    """
    return Path(source_abs).with_name(preview_jpg_name(source_abs))


def legacy_preview_path(jpg_path: Path) -> Path:
    """改名前的点号落点（`<stem>.preview.jpg`）：**由新落点反推**。

    同一目录、同一源 stem，只差文件名里一个字符 —— 两条老规则（`with_suffix` 与
    `SR_PREVIEWS_ROOT` 的镜像树）都把那份文件放在新落点同一个目录里，所以反推不需要
    root。传进来的不是本函数的落点（stem 不以 `_preview` 结尾）时**原样返回**，好让
    调用方能一眼跳过。
    """
    p = Path(jpg_path)
    tail = "_preview"
    if not p.stem.endswith(tail):
        return p
    return p.with_name(p.stem[: -len(tail)] + ".preview" + p.suffix)
