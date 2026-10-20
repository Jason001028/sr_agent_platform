"""聊天截图附件 —— 落盘、取用与「只在水合时才转 base64」。

背景：Agent 原本只读文字 + 上下文快照，看不见查看器里的画面。2026-10-20 起聊天
可以带截图（`docs/planning/api-contract.md` §3.2.1），每回合至多 2 张。

**为什么不在数据库里存 data URL** —— 一张 1280 长边的 JPEG 约 200–400 KB，base64 后
再涨 1/3；历史消息、`agent_runs.prompt`、轨迹 payload（`bounded_payload` 32 KB 上限）
都会被它撑爆，而且每开一次会话都要整包传一遍。这里改成：

1. 落盘 `SR_CHAT_IMAGE_DIR/<session_id>/<uuid>.jpg`；
2. 数据库里只存**平台相对 URL**（`/api/chat/sessions/<sid>/images/<uuid>.jpg`）；
3. 只有在真正调 LLM 前，`hydrate_images` 才现读盘换成 data URL，并**剥掉 `name`**
   —— 兼容端点只认 `type` / `image_url` 两个键。

于是 `GET messages`、SQLite、轨迹里的 prompt 都只有小体积的路径，前端 `<img>` 直接
走 GET 端点。

**输入校验一律服务端说了算**：不信客户端给的 mime 与尺寸，用 Pillow 按实际字节识别，
统一 `exif_transpose` → RGB → 长边 ≤2048 → JPEG q85。超限或认不出的格式抛
`ChatImageError`，由接口层在开流前转 400（不占会话锁）。

约定
----
* 只依赖标准库 + Pillow + `backend.services.store.default_db_path`；不 import `api/`。
* 会话 id 与文件名各一条正则；`image_path` 再校验解析后的父目录，两道锁防目录穿越。
* 写盘用同目录临时文件 + `os.replace`：中途失败不会留下半截 JPEG。
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.services.store import default_db_path

#: 每回合最多几张（产品约束：2）。
MAX_IMAGES = 2
#: 解码后的原始字节上限。前端截出来的图远小于它，卡在这里的是「误传了别的东西」。
MAX_SOURCE_BYTES = 12 * 1024 * 1024
#: 像素数上限：防解压炸弹（一张 30000×30000 的 PNG 解出来就是几十 GB）。
MAX_PIXELS = 40_000_000
#: 落盘时的长边与质量。
MAX_SIDE = 2048
JPEG_QUALITY = 85

#: 会话 id：与 store 生成的一致（uuid hex），放宽到常见的 URL 安全字符。
_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: 落盘文件名。只有本模块生成的 uuid4 hex 形态能被取回。
_FILE_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")
#: `data:image/png;base64,` 一类前缀（前端两种都发得出来，都接受）。
_DATA_URL_RE = re.compile(r"^data:image/[A-Za-z0-9.+-]+;base64,", re.IGNORECASE)
#: 落库的相对 URL。取回时以**解析后的 path** 匹配，所以带 host 的绝对 URL 也认。
_URL_RE = re.compile(
    r"^/api/chat/sessions/([A-Za-z0-9_-]{1,64})/images/([0-9a-f]{32}\.jpg)$")


class ChatImageError(ValueError):
    """用户可读的拒收原因（接口层转 HTTP 400）。"""


def image_root() -> Path:
    """截图根目录：`SR_CHAT_IMAGE_DIR`，否则与 `SR_AGENT_DB` 同目录的 `chat_images/`。"""
    env = os.environ.get("SR_CHAT_IMAGE_DIR")
    if env:
        return Path(env)
    return Path(default_db_path()).parent / "chat_images"


def session_dir(session_id: str) -> Path:
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        raise ChatImageError("会话 id 非法")
    return image_root() / session_id


def save_images(session_id: str, images) -> list[dict]:
    """校验并落盘一回合的截图，返回 `[{url, name}]`（顺序同入参）。

    全部成功才算成功：中途任一张失败，本次已写的文件全部删掉再抛错 —— 否则
    「3 张里第 2 张坏了」会在盘上留一张谁都引用不到的钱。
    """
    if images is None:
        return []
    if not isinstance(images, (list, tuple)):
        raise ChatImageError("images 须为数组")
    if not images:
        return []
    if len(images) > MAX_IMAGES:
        raise ChatImageError(f"最多 {MAX_IMAGES} 张截图")

    target = session_dir(session_id)
    saved: list[dict] = []
    written: list[Path] = []
    try:
        for item in images:
            record, path = _save_one(target, item)
            saved.append(record)
            written.append(path)
    except Exception:
        for path in written:
            _unlink_quiet(path)
        raise
    return saved


def image_path(session_id: str, filename: str) -> Path | None:
    """相对 URL 里的 (会话, 文件名) → 磁盘路径；不合法一律 None，不抛错。"""
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        return None
    if not isinstance(filename, str) or not _FILE_RE.match(filename):
        return None
    base = image_root() / session_id
    candidate = base / filename
    try:
        # 两道正则之后仍然再核对一次父目录：路径拼接一旦被改坏，这里就是最后一道锁。
        if candidate.resolve().parent != base.resolve():
            return None
    except OSError:
        return None
    return candidate


def delete_session_images(session_id: str) -> None:
    """删掉一个会话的截图目录。会话 id 非法就当没有（best-effort，不抛错）。"""
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        return
    shutil.rmtree(image_root() / session_id, ignore_errors=True)


def hydrate_images(messages: list[dict], *,
                   cache: dict[str, str] | None = None) -> list[dict]:
    """把相对 URL 换成 data URL，供 OpenAI 兼容端点调用（**返回浅拷贝**）。

    `cache` 是「一次回合内」的 URL → data URL 记忆：同一张图会被每个 LLM 调用
    重读一遍，最多 2 张小文件，可忽略；但给了 cache 就复用。
    文件丢失 → 该 block 降级成一条文字，整轮不至于 500。
    """
    out: list[dict] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        blocks: list[dict] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image_url":
                blocks.append(block)
                continue
            url = _block_url(block)
            data = _data_url(url, cache)
            if data is None:
                blocks.append({"type": "text",
                               "text": "[截图不可用：文件已丢失]"})
            else:
                # 只留 OpenAI schema 认的键：DB 里那个 name 不能漏给端点。
                blocks.append({"type": "image_url", "image_url": {"url": data}})
        out.append({**msg, "content": blocks})
    return out


def user_content(text: str, saved: list[dict]) -> str | list[dict]:
    """组装落库用的 user content：无图仍是纯字符串（老形状），有图是 block 列表。"""
    if not saved:
        return text
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for item in saved:
        blocks.append({"type": "image_url",
                       "image_url": {"url": item["url"]},
                       "name": item["name"]})
    return blocks


def split_content(content) -> tuple[str, list[dict]]:
    """把落库的 content 拆成 (文本, [{url, name}])，供 GET messages 投影。

    纯字符串（老消息）原样当文本；data URL 不进投影（它不该出现在库里）。
    """
    if not isinstance(content, list):
        return ("" if content is None else str(content)), []
    texts: list[str] = []
    images: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
        elif kind == "image_url":
            url = _block_url(block)
            if url and not url.startswith("data:"):
                images.append({"url": url, "name": block.get("name")})
    return "\n".join(t for t in texts if t), images


# --------------------------------------------------------------------------
# 内部
# --------------------------------------------------------------------------

def _save_one(target: Path, item) -> tuple[dict, Path]:
    if not isinstance(item, dict):
        raise ChatImageError("图片项须为对象")
    raw = item.get("data")
    if not isinstance(raw, str) or not raw.strip():
        raise ChatImageError("图片缺少 data")

    b64 = _DATA_URL_RE.sub("", raw.strip(), count=1)
    try:
        blob = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ChatImageError("图片 base64 非法") from exc
    if not blob:
        raise ChatImageError("图片内容为空")
    if len(blob) > MAX_SOURCE_BYTES:
        raise ChatImageError(
            f"单张图片不得超过 {MAX_SOURCE_BYTES // (1024 * 1024)} MB")

    payload = _to_jpeg(blob)

    target.mkdir(parents=True, exist_ok=True)
    filename = uuid.uuid4().hex + ".jpg"
    final = target / filename
    tmp = target / (filename + ".tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, final)
    except OSError as exc:
        _unlink_quiet(tmp)
        raise ChatImageError("截图写入失败") from exc

    url = f"/api/chat/sessions/{target.name}/images/{filename}"
    return {"url": url, "name": _clean_name(item.get("name"))}, final


def _to_jpeg(blob: bytes) -> bytes:
    """任意可识别图片 → RGB JPEG（长边 ≤MAX_SIDE），顺手去掉 EXIF 方向。"""
    buf = io.BytesIO()
    try:
        with Image.open(io.BytesIO(blob)) as src:
            if src.width * src.height > MAX_PIXELS:
                raise ChatImageError("图片像素数过大")
            im = ImageOps.exif_transpose(src)
            if im.mode != "RGB":
                # 带 alpha 的（PNG 截图、掩码叠加）铺白底：JPEG 没有 alpha 通道，
                # 直接 convert 会把透明处变成黑色，视觉上等于给用户看另一张图。
                if im.mode in ("RGBA", "LA", "PA") or (
                        im.mode == "P" and "transparency" in im.info):
                    rgba = im.convert("RGBA")
                    canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                    canvas.paste(rgba, mask=rgba.split()[-1])
                    im = canvas
                else:
                    im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            im.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except ChatImageError:
        raise
    except Image.DecompressionBombError as exc:
        raise ChatImageError("图片像素数过大") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ChatImageError("无法识别的图片格式") from exc
    return buf.getvalue()


def _clean_name(value) -> str:
    """文件名只作显示：取 basename、去不可打印字符、限长；**不参与任何路径**。"""
    try:
        base = Path(str(value or "")).name.strip()
    except (TypeError, ValueError):
        base = ""
    base = "".join(ch for ch in base if ch.isprintable())
    return base[:120] or "截图"


def _block_url(block: dict) -> str:
    obj = block.get("image_url")
    if isinstance(obj, dict):
        url = obj.get("url")
    else:
        url = obj
    return url if isinstance(url, str) else ""


def _data_url(url: str, cache: dict[str, str] | None) -> str | None:
    """相对/绝对图片 URL → data URL；取不到文件返回 None。"""
    if not url:
        return None
    if url.startswith("data:"):
        return url
    if cache is not None and url in cache:
        return cache[url]
    # 先按 path 解析：库里存的是相对路径，但手工构造的绝对 URL 也一并认。
    path = urlparse(url).path or url
    match = _URL_RE.match(path)
    if not match:
        return None
    file = image_path(match.group(1), match.group(2))
    if file is None or not file.is_file():
        return None
    try:
        raw = file.read_bytes()
    except OSError:
        return None
    data = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    if cache is not None:
        cache[url] = data
    return data


def _unlink_quiet(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass
