"""Frozen batch manifests for the QC TXT repair workflow.

PR B scope only: parse the business TXT, resolve each line to a real
PAN/NOSR scene pair, and freeze the result as a content-addressed artifact.
No detector, mask writer, SR submission or Progress Panel lives here.

The parser intentionally keeps every source line addressable.  It never drops a
line because it could not understand it; instead the line becomes a diagnostic
that must be explicitly excluded before a Goal may be created.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from backend.pathguard import PathDeniedError, ensure_allowed
from backend.services import preview_jpg, scene_resolve, scene_search
from backend.services.spatial_identity import resource_identity
from backend.services.tour_identity import image_identity
from backend.services.store import BATCH_WORKFLOW, default_db_path

WORKFLOW = BATCH_WORKFLOW
MANIFEST_VERSION = 2
SOURCE_FORMAT = "qc_txt_v1"
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_LINES = 5000
MAX_ITEMS = 500

_STATUS_WORDS = {
    "修复通过": "fixed",
    "已修复": "fixed",
    "驳回": "rejected",
    "非模糊通过": "no_blur",
}

_KIND_RE = re.compile(r"问题类型[:：]\s*([^\s)）]+)")
_IMAGE_TYPE_RE = re.compile(r"影像类型[:：]\s*([^\s)）]+)")
_ROW_COL_RE = re.compile(
    r"行列号[:：]\s*([+-]?\d+(?:\.\d+)?)\s*[,，]\s*([+-]?\d+(?:\.\d+)?)")
_TRAILING_COMMA_RE = re.compile(r"[,，]+$")


class ManifestError(ValueError):
    """Malformed import request or unreadable frozen manifest."""


class ManifestConflict(ManifestError):
    """The manifest needs explicit exclusions before a Goal may be created."""

    def __init__(self, message: str, diagnostics: list[dict] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


def artifact_root() -> Path:
    raw = os.environ.get("SR_BATCH_ARTIFACT_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path(default_db_path()).resolve().parent / "batch_artifacts"


def canonical_hash(value) -> str:
    body = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def decode_bytes(data: bytes, encoding: str = "auto") -> tuple[str, str]:
    """Decode a QC TXT the same way the browser importer does.

    The browser tries UTF-8 with BOM, strict UTF-8, then GBK.  Keep this byte
    contract server-side so a file the page could display is not rejected by the
    batch importer.
    """
    requested = (encoding or "auto").strip().lower()
    if requested in ("utf8", "utf-8"):
        try:
            return data.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError as exc:
            raise ManifestError(f"不是合法 UTF-8：{exc}") from exc
    if requested == "gbk":
        try:
            return data.decode("gbk"), "gbk"
        except UnicodeDecodeError as exc:
            raise ManifestError(f"不是合法 GBK：{exc}") from exc
    if requested not in ("auto", ""):
        raise ManifestError("encoding 只支持 auto、utf-8、gbk")
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data[3:].decode("utf-8"), "utf-8"
        except UnicodeDecodeError as exc:
            raise ManifestError(f"BOM 后的内容不是合法 UTF-8：{exc}") from exc
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        try:
            return data.decode("gbk"), "gbk"
        except UnicodeDecodeError as exc:
            raise ManifestError("既不是合法 UTF-8 也不是合法 GBK") from exc


def _clean_name(raw: str) -> str:
    return _TRAILING_COMMA_RE.sub("", str(raw).strip()).strip()


def _parse_status(raw: str) -> dict | None:
    if "\t" not in raw:
        return None
    fields = raw.split("\t")
    if len(fields) != 2:
        return None
    name = _clean_name(fields[0])
    status = _STATUS_WORDS.get(fields[1].strip())
    if not name or not status:
        return None
    return {"name": name, "status": status}


def _parse_issue(raw: str) -> dict | None:
    if "\t" not in raw:
        return None
    fields = raw.split("\t")
    if len(fields) < 3:
        return None
    name = _clean_name(fields[0])
    if not name:
        return None
    desc = fields[1].strip()
    owner = fields[2].strip()
    kind = _KIND_RE.search(desc)
    image_type = _IMAGE_TYPE_RE.search(desc)
    location = _ROW_COL_RE.search(desc)
    return {
        "name": name,
        "desc": desc,
        "owner": owner,
        "kind": kind.group(1) if kind else "",
        "imgType": image_type.group(1) if image_type else "",
        # The QC document writes (row, column); keep those names and do not
        # reinterpret them as x/y.  B does not build masks from these values.
        "row": float(location.group(1)) if location else None,
        "col": float(location.group(2)) if location else None,
    }


def parse_qc_text(text: str) -> dict:
    """Parse the two-section QC TXT without discarding any line.

    Returns ``rows`` (all non-empty lines with their original line number),
    unique ``items`` (first occurrence per production name), and diagnostics.
    Duplicate lines are attached to the first item and reported as errors; the
    caller must exclude them explicitly before creating a Goal.
    """
    if not isinstance(text, str):
        raise ManifestError("清单内容必须是文本")
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("﻿"):
        normalized = normalized[1:]
    lines = normalized.split("\n")
    if len(lines) > MAX_SOURCE_LINES:
        raise ManifestError(f"清单超过 {MAX_SOURCE_LINES} 行")
    rows: list[dict] = []
    items: list[dict] = []
    by_name: dict[str, dict] = {}
    diagnostics: list[dict] = []
    for line_no, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        status = _parse_status(raw)
        if status:
            rows.append({"line": line_no, "kind": "status", "raw": raw, **status})
            diagnostics.append({
                "line": line_no,
                "severity": "warning",
                "code": "status_line_ignored",
                "message": "已有状态行，不生成批任务项",
                "name": status["name"],
            })
            continue
        issue = _parse_issue(raw)
        if issue is None:
            rows.append({"line": line_no, "kind": "invalid", "raw": raw})
            diagnostics.append({
                "line": line_no,
                "severity": "error",
                "code": "invalid_columns",
                "message": "不是「影像名<TAB>问题描述<TAB>责任人」记录",
                "raw": raw[:200],
            })
            continue
        rows.append({"line": line_no, "kind": "issue", "raw": raw, **issue})
        first = by_name.get(issue["name"])
        if first is not None:
            first["source_lines"].append(line_no)
            diagnostics.append({
                "line": line_no,
                "severity": "error",
                "code": "duplicate_name",
                "message": (
                    "与第 %d 行重复（已合并；需显式排除重复行后才能创建）"
                    % first["source_lines"][0]),
                "name": issue["name"],
            })
            continue
        item = {**issue, "source_lines": [line_no]}
        by_name[issue["name"]] = item
        items.append(item)
    return {"rows": rows, "items": items, "diagnostics": diagnostics}


def _image(path: Path, role: str | None = None) -> dict:
    info = preview_jpg.probe_tiff(path)
    identity = resource_identity(path)
    revision = preview_jpg.source_revision(path)
    return {
        "path": path.as_posix(),
        "image_key": identity["image_key"],
        "revision": revision,
        "width": info["W"],
        "height": info["H"],
        "bits": info["bits"],
        "sample_format": info["sample_format"],
        "spp": info["spp"],
        "compression": info["compression"],
        "tourIdentity": image_identity(
            path, path.parent.as_posix(),
            role or ("pan" if path.stem == "PAN" else "nosr")),
    }


def _resolve_item(row: dict, *, date: str | None, product: str | None) -> dict:
    if row.get("explicit_path"):
        hinted = Path(row["explicit_path"])
        directory = ensure_allowed(hinted.parent, kind="dir")
        # Viewer 只传得出场景目录加上一个输入候选名（通常是 PAN.tif）；本体
        # 随 RC/SC 变名，这里只认「同一场景的输入候选」，真正的那份下面定。
        if hinted.name not in {c.name for c in scene_search.input_candidates(directory)}:
            raise scene_resolve.SceneLookupError("显式影像必须为场景输入影像")
        scene = {"dir": directory,
                 "input": scene_search.input_scene_path(directory) or hinted}
    else:
        scene = scene_resolve.resolve_scene_dir(
            row["name"], date=date, product=product or row.get("imgType") or None)
    directory: Path = scene["dir"]
    try:
        ensure_allowed(directory, kind="dir")
    except PathDeniedError as exc:
        raise scene_resolve.SceneLookupError(str(exc)) from exc
    # 本体随场景步骤变：RC 是 PAN.tif，SC 是 <目录名>.tif（判据同 SR 与
    # /siblings）。批量检测的宽高比与修复发布都针对本体；混合目录里那份
    # PAN.tif 可能只是上游遗留，不能拿来当 SC 场景的比较对象。
    pan = scene_search.body_path(directory) or (directory / "PAN.tif")
    nosr = directory / (directory.name + "_NOSR.tif")
    meta = directory / (directory.name + "_meta.xml")
    if not meta.is_file():
        raise scene_resolve.SceneLookupError(f"缺 {meta.name}")
    if not os.access(directory, os.W_OK):
        raise scene_resolve.SceneLookupError("场景目录不可写")
    for path in (pan, nosr):
        if not path.is_file():
            raise scene_resolve.SceneLookupError(f"缺 {path.name}")
        ensure_allowed(path, kind="file")
    # 备份名随本体走：RC 是 PAN_old_latest，SC 是 <目录名>_old_latest。
    for name in dict.fromkeys(
            ["PAN_old_latest.tif", "PAN_old_2.tif",
             pan.stem + "_old_latest.tif", pan.stem + "_old_2.tif"]):
        if (directory / name).exists():
            raise scene_resolve.SceneLookupError(
                "已存在 PAN 备份，拒绝重复修复：" + name)
    pan_info = preview_jpg.probe_tiff(pan)
    nosr_info = preview_jpg.probe_tiff(nosr)
    if pan_info["spp"] != 1 or nosr_info["spp"] != 1:
        raise scene_resolve.SceneLookupError("PAN 与 NOSR 必须是单波段")
    if pan_info["compression"] != 1:
        raise scene_resolve.SceneLookupError("PAN 必须是无压缩 TIFF")
    if (pan_info["bits"], pan_info["sample_format"]) != (
            nosr_info["bits"], nosr_info["sample_format"]):
        raise scene_resolve.SceneLookupError("PAN 与 NOSR 样本类型不一致")
    if not (pan_info["W"] >= nosr_info["W"]
            and pan_info["H"] >= nosr_info["H"]
            and (pan_info["W"], pan_info["H"]) != (nosr_info["W"], nosr_info["H"])):
        raise scene_resolve.SceneLookupError("NOSR 分辨率必须低于 PAN")
    target = _image(pan, "pan")
    reference = _image(nosr, "nosr")
    scene_ref = directory.as_posix()
    item_id = "item_" + hashlib.sha256(scene_ref.encode("utf-8")).hexdigest()[:16]
    return {
        "item_id": item_id,
        "name": row["name"],
        "source_lines": list(row["source_lines"]),
        "desc": row["desc"],
        "owner": row["owner"],
        "kind": row["kind"],
        "imgType": row["imgType"],
        "row": row["row"],
        "col": row["col"],
        "scene_ref": scene_ref,
        "source_image": scene["input"].as_posix(),
        "target_image": {"file_ref": "pan", **target},
        "reference_image": {"file_ref": "nosr", **reference},
        "config": {"sr_scale": 2},
    }


_MANIFEST_BOOKKEEPING = {"manifest_id", "manifest_sha256", "created_at"}
_MANIFEST_REQUIRED = ("version", "workflow", "source", "mode", "config",
                      "items", "diagnostics")
_SOURCE_FIELDS = ("format", "sha256", "encoding", "document_ref")


def _manifest_payload(manifest: dict) -> dict:
    """The exact bytes covered by ``manifest_sha256``.

    Everything except bookkeeping fields is execution-relevant and therefore
    enters the digest. Config is not appended after hashing; a changed limit,
    path, exclusion, mode or item makes the recomputed digest differ.
    """
    if not isinstance(manifest, dict):
        raise ManifestError("manifest 必须是对象")
    missing = [key for key in _MANIFEST_REQUIRED if key not in manifest]
    if missing:
        raise ManifestError("manifest 缺少字段：" + "、".join(missing))
    source = manifest["source"]
    if not isinstance(source, dict):
        raise ManifestError("manifest source 必须是对象")
    missing = [key for key in _SOURCE_FIELDS if key not in source]
    if missing:
        raise ManifestError("manifest source 缺少字段：" + "、".join(missing))
    if source["format"] not in {SOURCE_FORMAT, "explicit_sources_v1"}:
        raise ManifestError("不支持的清单源格式")
    if source["document_ref"] != "original.bin":
        raise ManifestError("manifest 源文档引用非法")
    if not isinstance(source["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", source["sha256"]):
        raise ManifestError("manifest 源文档摘要非法")
    if manifest["version"] != MANIFEST_VERSION or manifest["workflow"] != WORKFLOW:
        raise ManifestError("不支持的 manifest 版本或 workflow")
    if not isinstance(manifest["mode"], str) or manifest["mode"] not in {
            "demo", "production"}:
        raise ManifestError("manifest mode 非法")
    if not isinstance(manifest["config"], dict):
        raise ManifestError("manifest config 必须是对象")
    if not isinstance(manifest["items"], list) or not isinstance(
            manifest["diagnostics"], list):
        raise ManifestError("manifest items/diagnostics 必须是数组")
    return {key: value for key, value in manifest.items()
            if key not in _MANIFEST_BOOKKEEPING}


def _verify_artifact(directory: Path, ref: str) -> dict:
    """Recompute the artifact digest and verify the frozen source bytes.

    A manifest that merely repeats the expected hash is not enough: the payload
    is re-hashed from its current content, and ``original.bin`` must match the
    source digest recorded inside that payload.
    """
    path = directory / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"manifest 不可读：{exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestError("manifest 必须是对象")
    if (manifest.get("manifest_id") != ref
            or manifest.get("manifest_sha256") != ref):
        raise ManifestError("manifest 摘要与引用不一致")
    payload = _manifest_payload(manifest)
    if canonical_hash(payload) != ref:
        raise ManifestError("manifest 内容摘要不一致（可能已损坏或被篡改）")
    document_ref = payload["source"]["document_ref"]
    try:
        raw = (directory / document_ref).read_bytes()
    except OSError as exc:
        raise ManifestError(f"原始清单工件不可读：{exc}") from exc
    if hashlib.sha256(raw).hexdigest() != payload["source"]["sha256"]:
        raise ManifestError("原始清单工件摘要不一致（可能已损坏）")
    return manifest


def _publish(manifest_id: str, raw: bytes, manifest: dict) -> Path:
    root = artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    final = root / manifest_id
    if final.is_dir():
        _verify_artifact(final, manifest_id)
        return final
    temporary = Path(tempfile.mkdtemp(prefix=f".{manifest_id}-", dir=str(root)))
    try:
        (temporary / "original.bin").write_bytes(raw)
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        _verify_artifact(temporary, manifest_id)
        try:
            os.replace(temporary, final)
        except OSError:
            if final.is_dir():
                shutil.rmtree(temporary, ignore_errors=True)
                _verify_artifact(final, manifest_id)
            else:
                raise
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final


def import_manifest(*, data: bytes | None = None, text: str | None = None,
                    encoding: str = "auto", date: str | None = None,
                    product: str | None = None, mode: str = "demo",
                    sources: list[dict] | None = None, strategy: str = "automatic") -> dict:
    """Freeze a QC TXT import; no Goal is created here."""
    if data is not None:
        raw = bytes(data)
        decoded, used_encoding = decode_bytes(raw, encoding)
    elif text is not None:
        decoded = str(text)
        if decoded.startswith("﻿"):
            decoded = decoded[1:]
        raw = decoded.encode("utf-8")
        used_encoding = "utf-8"
    else:
        raise ManifestError("需提供 content_base64 或 text")
    if len(raw) > MAX_SOURCE_BYTES:
        raise ManifestError(f"清单超过 {MAX_SOURCE_BYTES} 字节")
    mode = str(mode or "demo").strip().lower()
    if mode not in {"demo", "production"}:
        raise ManifestError("mode 只支持 demo、production")
    if not isinstance(strategy, str) or strategy not in {"automatic", "review"}:
        raise ManifestError("strategy 只支持 automatic、review")
    parsed = parse_qc_text(decoded)
    if sources is not None:
        if not isinstance(sources, list) or len(sources) > MAX_ITEMS:
            raise ManifestError("sources 必须是最多 500 项的数组")
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                raise ManifestError("sources 每项必须有显式 path")
            if not source["path"].strip() or len(source["path"]) > 4096:
                raise ManifestError("sources path 非法")
            line = len(decoded.splitlines()) + len(parsed["items"]) + 1
            name = str(source.get("name") or Path(source["path"]).name)
            parsed["items"].append({
                "name": name, "source_lines": [line], "explicit_path": source["path"],
                "desc": str(source.get("evidence") or "Viewer 手动加入；待检测"),
                "owner": "viewer", "kind": "待检测", "imgType": "", "row": None, "col": None,
            })
        if sources:
            raw = json.dumps({"text": decoded, "sources": sources}, ensure_ascii=False).encode("utf-8")
            used_encoding = "utf-8"
            if len(raw) > MAX_SOURCE_BYTES:
                raise ManifestError("显式清单超过字节上限")
    if len(parsed["items"]) > MAX_ITEMS:
        raise ManifestError(f"清单超过 {MAX_ITEMS} 个场景")
    diagnostics = list(parsed["diagnostics"])
    items: list[dict] = []
    by_scene: dict[str, dict] = {}
    for row in parsed["items"]:
        try:
            item = _resolve_item(
                row, date=date, product=product or row.get("imgType") or None)
        except (scene_resolve.SceneLookupError, PathDeniedError,
                preview_jpg.PreviewError, OSError) as exc:
            diagnostics.append({
                "line": row["source_lines"][0],
                "severity": "error",
                "code": "scene_unresolved",
                "message": str(exc),
                "name": row["name"],
            })
            continue
        first = by_scene.get(item["scene_ref"])
        if first is not None:
            source_line = row["source_lines"][0]
            first["source_lines"].append(source_line)
            diagnostics.append({
                "line": source_line,
                "severity": "error",
                "code": "duplicate_scene",
                "message": (
                    "与第 %d 行指向同一场景目录 %s（已合并；需显式排除重复行后才能创建）"
                    % (first["source_lines"][0], item["scene_ref"])),
                "name": row["name"],
                "scene_ref": item["scene_ref"],
            })
            continue
        item["ordinal"] = len(items) + 1
        by_scene[item["scene_ref"]] = item
        items.append(item)
    source_sha = hashlib.sha256(raw).hexdigest()
    if mode == "production":
        for item in items:
            item["config"]["detection"]={"mode":"diff","method":"absdiff","sensitivity":"standard",
                "max_edge":2048,"min_area_px":64,"margin":"standard","version":"shared-difference.v1"}
    content = {
        "version": MANIFEST_VERSION,
        "workflow": WORKFLOW,
        "source": {"format": "explicit_sources_v1" if sources else SOURCE_FORMAT, "sha256": source_sha,
                   "encoding": used_encoding, "document_ref": "original.bin"},
        "mode": mode,
        "config": {
            "strategy": strategy,
            "max_inflight_sr_jobs": 3 if mode == "demo" else 1,
            "max_prepared_items": 1,
        },
        "items": items,
        "diagnostics": diagnostics,
    }
    manifest_id = canonical_hash(content)
    manifest = {
        **content,
        "manifest_id": manifest_id,
        "manifest_sha256": manifest_id,
        "created_at": time.time(),
    }
    _publish(manifest_id, raw, manifest)
    return {
        "manifest_ref": manifest_id,
        "manifest_sha256": manifest_id,
        "manifest_path": (artifact_root() / manifest_id / "manifest.json").as_posix(),
        "encoding": used_encoding,
        "mode": mode,
        "counts": {
            "lines": len(parsed["rows"]),
            "items": len(items),
            "errors": sum(1 for d in diagnostics if d["severity"] == "error"),
            "warnings": sum(1 for d in diagnostics if d["severity"] == "warning"),
        },
        "rejected": any(d["severity"] == "error" for d in diagnostics),
        "diagnostics": diagnostics,
        "items": [
            {
                "item_id": item["item_id"],
                "ordinal": item["ordinal"],
                "name": item["name"],
                "source_lines": item["source_lines"],
                "scene_ref": item["scene_ref"],
            }
            for item in items
        ],
    }


def load_manifest(ref: str) -> dict:
    if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref):
        raise ManifestError("manifest_ref 非法")
    directory = artifact_root() / ref
    if not (directory / "manifest.json").is_file():
        raise ManifestError("manifest 不存在或已被清理")
    return _verify_artifact(directory, ref)


def normalize_exclusions(value) -> list[dict]:
    """Accept [1,2] or [{"line":1,"reason":"..."}]; dedupe by line."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ManifestError("exclusions 必须是数组")
    merged: dict[int, str] = {}
    for entry in value:
        if isinstance(entry, bool):
            raise ManifestError("exclusions 行号必须是正整数")
        if isinstance(entry, int):
            line, reason = entry, ""
        elif isinstance(entry, dict):
            line = entry.get("line")
            reason = str(entry.get("reason") or "")
        else:
            raise ManifestError("exclusions 每项必须是行号或 {line, reason}")
        if type(line) is not int or line <= 0:
            raise ManifestError("exclusions 行号必须是正整数")
        if len(reason) > 500:
            raise ManifestError("exclusions 原因最长 500 字")
        merged[line] = reason or merged.get(line, "")
    return [{"line": line, "reason": merged[line]}
            for line in sorted(merged)]


def plan_goal_items(manifest: dict, exclusions) -> list[dict]:
    """Apply exclusions and decide which frozen items become Goal steps.

    Every error diagnostic not covered by an exclusion blocks creation.  An item
    whose every source line is excluded becomes a SKIPPED step, retaining its
    identity and reason instead of disappearing.
    """
    normalized = normalize_exclusions(exclusions)
    excluded = {e["line"]: e["reason"] for e in normalized}
    blocking = [
        diagnostic for diagnostic in manifest.get("diagnostics", [])
        if diagnostic.get("severity") == "error"
        and diagnostic.get("line") not in excluded
    ]
    if blocking:
        raise ManifestConflict(
            "清单仍有未排除的错误行，不能创建批任务", blocking)
    result: list[dict] = []
    for item in manifest.get("items", []):
        kept = [line for line in item.get("source_lines", [])
                if line not in excluded]
        skipped = not kept
        reason = ""
        if skipped:
            reason = next(
                (excluded[line] for line in item.get("source_lines", [])
                 if excluded.get(line)), "用户排除")
        result.append({
            **item,
            "source_lines": kept,
            "status": "SKIPPED" if skipped else "PENDING",
            "exclusion_reason": reason,
        })
    if not result:
        raise ManifestConflict("清单没有可创建的批任务项")
    return result
