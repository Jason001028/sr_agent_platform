"""Curated scene references and tags; source files stay in their existing location."""
from fastapi import HTTPException, Request
from jsonschema import Draft202012Validator

from backend.api import paths
from backend.services import scene_search

TAG_GROUPS = ("purpose", "content", "quality", "custom")
_TAGS = {"type": "object", "additionalProperties": False, "properties": {
    group: {"type": "array", "maxItems": 40, "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 40,
                      "pattern": r"\S"}} for group in TAG_GROUPS}}
_BATCH = Draft202012Validator({"type": "object", "required": ["entries"],
    "additionalProperties": False, "properties": {"entries": {
        "type": "array", "minItems": 1, "maxItems": 200, "items": {
            "type": "object", "required": ["scene_id", "tags"], "additionalProperties": False,
            "properties": {"scene_id": {"type": "string", "minLength": 1, "maxLength": 4096},
                           "tags": _TAGS}}}}})


def install(app, root, make_row, dimensions):
    @app.post("/api/scene-library")
    async def add_entries(request: Request):
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(422, "请求不是有效 JSON")
        if next(_BATCH.iter_errors(body), None) is not None:
            raise HTTPException(422, "条目或标签无效：每批最多 200 张，每组最多 40 个标签，每个标签 1–40 字")
        entries = []
        for item in body["entries"]:
            try:
                src = paths.scene_id_to_abs(item["scene_id"], root).resolve()
                if not src.is_file() or src.suffix.lower() not in {".tif", ".tiff", ".jpg", ".jpeg"}:
                    raise ValueError()
            except (ValueError, OSError, paths.PathDeniedError):
                raise HTTPException(422, "批次中有影像不可访问或不在路径白名单内，本批尚未保存，请检查影像关联")
            entries.append({"source_path": str(src), "tags": {
                group: list(dict.fromkeys(v.strip() for v in values))
                for group, values in item["tags"].items()}})
        app.state.store.add_library_entries(entries)
        return {"ok": True, "count": len({e["source_path"] for e in entries})}

    @app.get("/api/scene-library")
    def list_entries():
        results = []
        unavailable = 0
        # 隐藏与检索共用同一份服务端集合：从场景库「清除/隐藏」过的场景，
        # 「已入库」视图也不该再列出来，否则同一景在两个视图里一隐一现。
        hidden = {e["scene_dir"] for e in app.state.store.list_hidden_scenes()}
        for entry in app.state.store.list_library_entries():
            try:
                # Recheck the current whitelist rather than trusting persisted paths.
                src = paths.scene_id_to_abs(paths.scene_id_abs(entry["source_path"]), root)
                if not src.is_file():
                    raise ValueError()
                if scene_search.dir_key(src.parent) in hidden:
                    continue
                row = make_row(src, src.parent, root)
                dims = dimensions(src)
                if dims:
                    row["W"], row["H"] = dims["W"], dims["H"]
                row["tags"] = entry["tags"]
                row["in_library"] = True
                results.append(row)
            except (ValueError, OSError, paths.PathDeniedError):
                unavailable += 1
        return {"results": results, "unavailable": unavailable, "hidden": len(hidden)}
