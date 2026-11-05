"""Read-only discovery of verified SR output and its existing preview.

No scans, TIFF decoding, preview baking or SR execution occur here.
"""
from pathlib import Path

from backend.pathguard import ensure_allowed
from backend.services.goal_worker import sr_contract
from backend.services.preview_jpg import source_revision


def discover(task, root):
    from backend.api.app import _manual_row, _run_dataroot
    from backend.api.platform import _norm_dir
    base = {"task_id": task["task_id"], "state": "preparing", "note": "成果准备中"}
    if task.get("status") != "COMPLETED":
        return {**base, "state": "unavailable", "note": "作业尚未成功完成"}
    params = task.get("params") or {}
    dataroot = _run_dataroot(task)
    if not dataroot or _norm_dir(dataroot) != _norm_dir(params.get("lq_path") or ""):
        return {**base, "state": "unavailable", "note": "沙箱成果不属于当前生产影像"}
    contract = sr_contract(task, {"state": "COMPLETED", "exit_code": "0:0"})
    if not contract.get("ok") or contract.get("skip") or not contract.get("output"):
        return {**base, "state": "unavailable", "note": "未确认有效成果：" + "；".join(contract.get("reasons") or [])}
    source = ensure_allowed(Path(contract["output"]), kind="file")
    if source.parent.resolve() != Path(dataroot).resolve():
        return {**base, "state": "unavailable", "note": "成果目录与作业不符"}
    row = _manual_row(source, source.parent, root)
    revision = source_revision(source)
    if not revision or row.get("srcRev") != revision:
        return base
    from backend.api.paths import scene_id
    aliases = [row["id"]] + ([scene_id(row["rel"])] if row.get("rel") else [])
    kind = "input" if params.get("repair") else "product"
    return {**base, "state": "ready" if row.get("hasPreview") and row.get("previewDiv") else "preparing",
            "note": "新预览就绪" if row.get("hasPreview") else "成果准备中",
            "version": row["id"] + "|" + revision, "row": row, "aliases": aliases,
            "group": source.parent.as_posix(), "kind": kind,
            "source": source.as_posix(), "finished_at": task.get("finished_at") or task.get("updated_at")}


def preview_bytes(task, root, version):
    from backend.api.app import _preview_cache_state
    from backend.api.paths import preview_jpg_for
    result = discover(task, root)
    if result.get("state") != "ready" or result.get("version") != version:
        raise ValueError("成果版本或预览已变化，请刷新后重试")
    source = Path(result["source"])
    preview = preview_jpg_for(source, root)
    if preview.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("预览过大，请使用常规查看入口")
    from backend.api.app import _jpeg_snapshot, _PreviewSnapshotError
    try:
        response = _jpeg_snapshot(preview, div=result["row"]["previewDiv"], source_rev=result["row"]["srcRev"])
    except _PreviewSnapshotError as exc:
        raise ValueError(str(exc)) from exc
    data = response.body
    valid, div = _preview_cache_state(preview, source)
    if (not valid or div != result["row"]["previewDiv"]
            or source_revision(source) != result["row"]["srcRev"]):
        raise ValueError("读取期间源版本或预览发生变化")
    return data
