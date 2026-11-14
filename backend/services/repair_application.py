"""Unified Repair Application Service.

One business entry for the Repair tool panel, the Agent tools and the REST API.
It only orchestrates: target validation, a confirmable plan, server-side user
confirmation, and the creation/reuse of the **existing** Batch Goal.

What lives here
---------------
``prepare_repair``   validate a trustworthy workspace/selection and freeze an
                     immutable manifest, then persist a confirmable plan.
``confirm_repair``   record a real user's confirmation (never a model claim).
``start_repair``     re-validate the confirmed plan and create or reuse the
                     same Batch Goal through ``Store.create_batch_goal``.
``get_repair_status``/``get_repair_result``
                     project the authoritative Goal/Item/SR/quality state.

What deliberately does not live here
------------------------------------
Detection, mask rasterization, the SR submission loop, monitoring, capacity,
Lease/CAS fencing, checkpoints and UNKNOWN reconciliation all stay in
``batch_worker`` / ``run_sr`` / ``Store``.  This module never submits SR and
never keeps a second task-state database: the ``repair_plans`` row stores the
plan/confirmation only; execution truth is the Goal + Item rows.

Manual single-scene repair
--------------------------
A scene the operator already drew a mask for is represented as a **one-item
Batch Goal** whose item starts in ``MASK_READY``.  The existing mask is copied
into the batch artifact root and contract-checked, a truthful
``existing-human-mask`` detection record is attached, and the normal Producer
submits it through the same ``run_sr.submit_run_sr`` call.  No second executor
and no detection rewrite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

from backend.pathguard import PathDeniedError, ensure_allowed
from backend.services import (batch_contracts, batch_manifest, batch_mask,
                              batch_snapshot, preview_jpg, scene_search)
from backend.services.store import RepairPlanConflict

PLAN_CONTRACT_VERSION = "repair_plan.v1"
PLAN_TTL_SEC = 3600.0
CONFIRM_TTL_SEC = 900.0
_DEFAULT_TITLE = "自动修复"

_DETECTION_LABELS = {
    "absdiff": "existing_diff_pipeline",
    "ssim": "existing_diff_pipeline",
    "smart": "smart_compare_experimental",
    "existing": "none_existing_mask",
}


class RepairError(ValueError):
    """A repair request cannot be planned or started."""

    code = "repair_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class RepairBlocked(RepairError):
    """Execution is intentionally closed for this mode/environment."""

    code = "repair_execution_blocked"

    def __init__(self, message: str, *, reason: str = "execution_blocked"):
        super().__init__(message)
        self.reason = reason


def _now() -> float:
    return time.time()


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)
    return value if value > 0 else float(default)


# ---------------------------------------------------------------------------
# execution availability (no bypass of the existing production gate)
# ---------------------------------------------------------------------------

def probe_execution(mode: str) -> dict:
    """Report whether the existing batch gate would allow this mode.

    This calls ``batch_worker.ensure_execution_allowed`` itself — the same
    function the Producer uses on every submit — so a plan cannot advertise an
    execution path the worker would later refuse.
    """
    try:
        from backend.services import batch_worker
        batch_worker.ensure_execution_allowed(mode)
    except Exception as exc:  # noqa: BLE001 — availability is reported, not raised
        return {"allowed": False, "mode": mode,
                "reason": f"{type(exc).__name__}: {exc}"}
    return {"allowed": True, "mode": mode, "reason": None}


# ---------------------------------------------------------------------------
# workspace -> trustworthy scene sources
# ---------------------------------------------------------------------------

def _scene_input_for_id(scene_id: str) -> Path:
    from backend.api import paths
    root = paths.scenes_root()
    abs_path = paths.scene_id_to_abs(scene_id, root)
    directory = abs_path.parent
    if not scene_search.is_scene_dir(directory):
        raise RepairError("不是可修复的场景目录（缺 <目录名>_meta.xml 或输入影像）")
    inp = scene_search.input_scene_path(directory)
    if inp is None:
        raise RepairError("场景目录里没有可提交的输入影像")
    ensure_allowed(inp, kind="file")
    return inp


def _workspace_identity(workspace) -> tuple[list[str], str | None]:
    """Stable identity of the whole workspace (resolvable or not).

    ``scene_ids`` are opaque IDs, so display renames and unresolvable cards do
    not change the identity; adding/removing a card does.  The digest is used
    instead of a count comparison.
    """
    if not workspace:
        return [], None
    ids = sorted({
        item.get("scene_id")
        for item in workspace.get("items") or []
        if isinstance(item, dict) and isinstance(item.get("scene_id"), str)
    })
    # Local-only images and display names are not identities: a local file
    # added to the list must not invalidate a confirmed scene plan.
    digest = batch_manifest.canonical_hash({"scene_ids": ids})
    return ids, digest


def _workspace_sources(workspace, current, selected_images, scope):
    """Resolve the current message's Viewer workspace into scene input files.

    Only opaque scene ids from the message snapshot are used.  There is no
    ``search_scenes`` call and no directory listing: each id is re-resolved
    through the existing whitelist + existence checks.
    """
    if scope not in ("current", "selection", "workspace"):
        raise RepairError("scope 只支持 current、selection、workspace")
    if scope == "workspace" and workspace and workspace.get("truncated"):
        # A truncated snapshot is not "the workspace"; planning a silent prefix
        # would repair a different set than the user sees.
        raise RepairError(
            "工作区候选集被截断（超过 200 项或字节预算），不能静默只规划前一部分；"
            "请缩小题数/多选目标后重新准备")
    ids: list[tuple[str, str]] = []
    if scope == "current":
        scene_id = (current or {}).get("scene_id")
        if not isinstance(scene_id, str) or not scene_id:
            raise RepairError(
                "当前影像尚未绑定可用于SR的服务器资源；"
                "可先准备资源评估计划，再补齐后端源文件与输入契约")
        ids.append((scene_id, str((current or {}).get("name") or "当前影像")))
    elif scope == "selection":
        for image in selected_images or []:
            if isinstance(image, dict) and isinstance(image.get("scene_id"), str):
                ids.append((image["scene_id"], str(image.get("name") or "选中影像")))
        if not ids:
            raise RepairError(
                "这条消息没有已绑定服务器源文件的多选影像；"
                "请重新发送包含所选资源身份的快照，先准备资源评估计划")
    else:
        for item in (workspace or {}).get("items") or []:
            if isinstance(item, dict) and isinstance(item.get("scene_id"), str):
                ids.append((item["scene_id"], str(item.get("name") or "工作区影像")))

    if not ids:
        local_only = int((workspace or {}).get("local_only") or 0)
        if local_only:
            raise RepairError(
                f"工作区有 {local_only} 张本地影像，但旧快照没有资源身份；"
                "请更新前端并重新发送消息，或指定当前影像准备资源评估计划")
        raise RepairError("当前工作区没有可准备修复计划的影像资源")

    sources: list[dict] = []
    unresolved: list[dict] = []
    scene_ids: list[str] = []
    scene_refs: dict[str, str] = {}
    seen_files: set[str] = set()
    for scene_id, name in ids:
        try:
            inp = _scene_input_for_id(scene_id)
        except (RepairError, PathDeniedError, KeyError, OSError) as exc:
            unresolved.append({"name": name, "reason": str(exc)})
            continue
        key = inp.as_posix()
        if key in seen_files:
            continue
        seen_files.add(key)
        scene_ids.append(scene_id)
        # The manifest keys an Item by scene directory; keep the mapping so the
        # *executable* target set can be derived after exclusions.
        scene_refs[scene_id] = inp.parent.as_posix()
        sources.append({
            "path": key,
            "name": name,
            "evidence": "Agent 工作区选择；问题与区域待检测",
        })
    if not sources:
        reasons = "；".join(f"{row['name']}：{row['reason']}" for row in unresolved[:5])
        raise RepairError(
            "工作区里没有满足现有SR输入契约的服务器影像" + (f"（{reasons}）" if reasons else ""))
    return sources, unresolved, scene_ids, scene_refs


# ---------------------------------------------------------------------------
# plan construction
# ---------------------------------------------------------------------------

def _sources_from_argument(sources) -> list[dict]:
    if not isinstance(sources, list) or not sources:
        raise RepairError("sources 必须是非空数组")
    out: list[dict] = []
    seen: set[str] = set()
    for entry in sources:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise RepairError("sources 每项必须是 {path, name, evidence}")
        path = entry["path"].strip()
        if not path or len(path) > 4096:
            raise RepairError("sources.path 为空或过长")
        if path in seen:
            continue
        seen.add(path)
        name = str(entry.get("name") or Path(path).name)[:512]
        evidence = str(entry.get("evidence") or "Viewer 手动选择；待检测")[:4096]
        out.append({"path": path, "name": name, "evidence": evidence})
    return out


def _item_identities(manifest: dict,
                     planned: list[dict] | None = None) -> list[dict]:
    """Small source identities; never a full-file SHA256."""
    result = []
    for item in (planned if planned is not None else manifest.get("items") or []):
        if item.get("status") == "SKIPPED":
            continue
        target = item.get("target_image") or {}
        reference = item.get("reference_image") or {}
        scene_ref = str(item.get("scene_ref") or "")
        rows = []
        for role, image in (("pan", target), ("nosr", reference)):
            path = str(image.get("path") or "")
            revision = preview_jpg.source_revision(path) if path else None
            rows.append({
                "role": role,
                "path": path,
                "revision": revision,
                "frozen_revision": image.get("revision"),
                "width": image.get("width"),
                "height": image.get("height"),
            })
        backup_names = []
        try:
            for name in _backup_names(scene_ref, target.get("path")):
                backup_names.append(name)
        except (OSError, TypeError, ValueError):
            pass
        result.append({
            "item_id": item.get("item_id"),
            "scene_ref": scene_ref,
            "images": rows,
            "backups": backup_names,
        })
    return result


def _backup_names(scene_ref, target_path=None):
    stems = {"PAN", Path(target_path).stem if target_path else "PAN"}
    scene = Path(scene_ref)
    return sorted(
        backup.name for backup in
        [scene / f"{stem}{tail}" for stem in stems
         for tail in ("_old_latest.tif", "_old_2.tif")]
        if backup.exists())


def _targets(manifest: dict, unresolved: list[dict] | None = None,
             planned: list[dict] | None = None) -> dict:
    items = planned if planned is not None else (manifest.get("items") or [])
    rows = [{
        "item_id": item.get("item_id"),
        "ordinal": item.get("ordinal"),
        "name": item.get("name"),
        "scene_ref": item.get("scene_ref"),
        "source_lines": item.get("source_lines") or [],
        "status": item.get("status"),
    } for item in items]
    excluded = sum(1 for row in rows if row["status"] == "SKIPPED")
    return {
        "total": len(rows),
        "excluded": excluded,
        "applicable": len(rows) - excluded,
        "items": rows,
        "unresolved": list(unresolved or []),
    }


def _mask_plan(mask_policy: str, mask_path, manifest: dict,
               planned: list[dict] | None = None) -> dict | None:
    if mask_policy != "existing":
        return None
    items = planned if planned is not None else (manifest.get("items") or [])
    applicable = [item for item in items if item.get("status") != "SKIPPED"]
    if len(applicable) != 1:
        raise RepairError("人工掩码修复一次只能准备一个场景")
    item = applicable[0]
    target = item.get("target_image") or {}
    scene_ref = item.get("scene_ref")
    if not mask_path:
        mask_path = scene_search.derived_mask_path(item.get("scene_ref"))
    mask_path = str(mask_path)
    path = Path(mask_path)
    try:
        ensure_allowed(path, kind="file")
    except PathDeniedError as exc:
        raise RepairError(f"人工掩码不在允许的盘阵前缀内：{exc}") from exc
    if not path.is_file():
        raise RepairError(
            f"场景目录缺少已保存的掩码 {mask_path}；请先在 Viewer 绘制并保存掩码，"
            "或改用自动检测掩码")
    if not scene_ref:
        raise RepairError("人工掩码缺少目标场景，拒绝准备")
    try:
        mask_dir = os.path.normcase(str(path.parent.resolve()))
        target_dir = os.path.normcase(str(Path(scene_ref).resolve()))
    except OSError as exc:
        raise RepairError(f"人工掩码目标场景不可读：{exc}") from exc
    if mask_dir != target_dir:
        raise RepairError(
            "人工掩码必须位于目标场景目录内，不能跨景复用；"
            "请在当前场景重新绘制并保存掩码")
    try:
        info = preview_jpg.probe_tiff(path)
    except Exception as exc:  # noqa: BLE001 — any probe failure is actionable
        raise RepairError(f"人工掩码不可读：{type(exc).__name__}: {exc}") from exc
    if info["W"] != target.get("width") or info["H"] != target.get("height"):
        raise RepairError(
            f"人工掩码尺寸 {info['W']}×{info['H']} 与本体 "
            f"{target.get('width')}×{target.get('height')} 不一致")
    return {
        "policy": "existing",
        "scene_ref": Path(scene_ref).as_posix(),
        "path": path.as_posix(),
        "revision": preview_jpg.source_revision(path),
        "width": info["W"],
        "height": info["H"],
    }


def _plan_hash(binding: dict) -> str:
    return "plan_" + batch_manifest.canonical_hash(binding)[:24]


def _observed_resources(workspace, current, selected_images, scope):
    """Metadata references only: these never grant server file access."""
    if scope == 'current':
        rows = [current] if current else []
    elif scope == 'selection':
        rows = selected_images or []
    else:
        rows = [*((workspace or {}).get('items') or []),
                *((workspace or {}).get('resources') or [])]
        if not rows and (workspace or {}).get('local_only') == 1 and current:
            rows = [current]
        elif int((workspace or {}).get('local_only') or 0) > len((workspace or {}).get('resources') or []):
            if (workspace or {}).get('resources_truncated'):
                raise RepairError('本地资源身份超过快照预算被截断，场景候选不受影响；'
                                  '请缩小工作区或分批选择后重新发送，不能只规划部分资源')
            raise RepairError('工作区本地资源身份不完整，请更新前端并重新发送快照，不能只规划部分资源')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row,dict):
            continue
        ref = {k:row[k] for k in ('id','name','image_key','source_revision','scene_id','asset_id','width','height')
               if k in row and (row[k] is None or isinstance(row[k],(str,int,float)))}
        key = ref.get('scene_id') or ref.get('image_key') or ref.get('asset_id')
        if key is None:
            key = ref.get('id')
        if key is None or str(key) in seen:
            continue
        ref['source_kind'] = ('server_reference' if ref.get('scene_id') else
                              'uploaded_or_preview_asset' if ref.get('asset_id') else 'browser_local')
        seen.add(str(key))
        result.append(ref)
    return result


def _resource_assessment(store, resources, *, session_id, scope, mode,
                         strategy, detection_method, mask_policy, title, created_by,
                         resolution_reason=None):
    """Persist a useful incomplete plan for any user-selected resource source.

    Execution still requires authoritative PAN/NOSR and Mask bindings. No path
    resolution, upload-cache access, manifest import or filesystem scan occurs.
    """
    requirements = [
        {'code':'backend_source_binding','message':'将源影像绑定为后端可读取的资源；浏览器文件或预览资产引用不等于SR输入授权。'},
        {'code':'sr_input_contract','message':'为每个目标补齐并核验PAN/NOSR配对、生产元数据与掩码策略；可来自允许的普通目录或共享目录，不要求进入场景库。'}]
    if resolution_reason:
        from backend.services.repair_workspace import safe
        requirements.insert(0,{'code':'source_resolution','message':safe(resolution_reason)})
    plan_id = _plan_hash({'kind':'resource_assessment','session_id':session_id,'scope':scope,
        'resources':resources,'mode':mode,'strategy':strategy,'detection':detection_method,'mask':mask_policy})
    now = _now()
    plan = {'kind':'repair_plan.v1','contract_version':PLAN_CONTRACT_VERSION,
        'plan_id':plan_id,'version':1,'session_id':session_id,'scope':scope,'mode':mode,
        'status':'BLOCKED','confirmation_required':True,'created_by':created_by,
        'title':str(title or '影像资源修复评估')[:200],
        'targets':{'total':len(resources),'applicable':0,'excluded':0,'items':[
            {'item_id':'resource_'+batch_manifest.canonical_hash(row)[:20],'ordinal':n,
             'name':row.get('name') or '影像资源','status':'INPUT_REQUIRED','resource':row}
            for n,row in enumerate(resources,1)]},
        'strategy_policy':strategy,'detection_method':detection_method,'mask_policy':mask_policy,
        'strategy':{'detection':_DETECTION_LABELS.get(detection_method,'existing_diff_pipeline'),
                    'mask':mask_policy,'execution':'existing_batch_runtime'},
        'capabilities':{'workspace_observation':True,'repair_assessment':True,'sr_execution':False},
        'execution':{'allowed':False,'mode':mode,'reason':'影像资源已纳入计划；待补齐后端源文件与SR输入契约。'},
        'blocking':requirements,'diagnostics':[], 'identities':[],
        'manifest_ref':'','manifest_sha256':'','confirmation':None,
        'created_at':now,'expires_at':now+_env_float('SR_REPAIR_PLAN_TTL_SEC',PLAN_TTL_SEC)}
    return public_plan(store.upsert_repair_plan(plan))


def prepare_repair(
    store,
    *,
    session_id: str | None = None,
    workspace=None,
    current=None,
    selected_images=None,
    scope: str = "workspace",
    sources=None,
    manifest_ref: str | None = None,
    exclusions=None,
    mode: str = "demo",
    strategy: str = "automatic",
    detection_method: str = "absdiff",
    smart_options=None,
    mask_policy: str = "auto",
    mask_path: str | None = None,
    title: str | None = None,
    created_by: str = "agent",
) -> dict:
    """Validate targets and persist a confirmable repair plan.  No SR side effect."""
    mode = str(mode or "demo").strip().lower()
    if mode not in ("demo", "production"):
        raise RepairError("mode 只支持 demo、production")
    strategy = str(strategy or "automatic").strip().lower()
    if strategy not in ("automatic", "review"):
        raise RepairError("strategy 只支持 automatic、review")
    detection_method = str(detection_method or "absdiff").strip().lower()
    if detection_method not in ("absdiff", "ssim", "smart"):
        raise RepairError("detection_method 只支持 absdiff、ssim、smart")
    mask_policy = str(mask_policy or "auto").strip().lower()
    if mask_policy not in ("auto", "existing"):
        raise RepairError("mask_policy 只支持 auto、existing")
    # ``existing`` is a plan-level strategy, not an importer detection method:
    # the frozen importer only accepts absdiff/ssim/smart.  The mask policy is
    # written into the plan separately, and the manual path never runs a
    # detector.
    plan_detection = detection_method
    import_detection = detection_method
    if mask_policy == "existing":
        plan_detection = "existing"
        import_detection = "absdiff"  # no detector runs on the manual path
        strategy = "review"
    elif detection_method == "smart":
        strategy = "review"  # the frozen importer enforces this too

    workspace_version = (workspace or {}).get("version")
    workspace_total = (workspace or {}).get("total")
    workspace_truncated = bool((workspace or {}).get("truncated"))
    workspace_identity_ids, workspace_identity_digest = _workspace_identity(
        workspace)
    unresolved: list[dict] = []
    workspace_scene_ids: list[str] = []
    workspace_scene_refs: dict[str, str] = {}
    try:
        normalized_exclusions = batch_manifest.normalize_exclusions(exclusions)
    except batch_manifest.ManifestError as exc:
        raise RepairError(str(exc)) from exc
    if manifest_ref and sources is not None:
        raise RepairError("manifest_ref 与 sources 只能提供一个")
    if not manifest_ref and sources is None:
        if scope not in ('current','selection','workspace'):
            raise RepairError('scope 只支持 current、selection、workspace')
        if scope == 'workspace' and (workspace or {}).get('truncated'):
            raise RepairError('工作区快照被截断，请缩小选择范围后重新准备')
        observed = _observed_resources(workspace,current,selected_images,scope)
        if observed and any(not row.get('scene_id') for row in observed):
            return _resource_assessment(store,observed,session_id=session_id,scope=scope,mode=mode,
                strategy=strategy,detection_method=detection_method,mask_policy=mask_policy,
                title=title,created_by=created_by)
    if manifest_ref:
        # Reuse an already frozen QC import (the legacy batch-import flow).
        # No re-import and no directory scan: the manifest is verified by the
        # same content digest used at execution time.
        try:
            manifest = batch_manifest.load_manifest(str(manifest_ref))
        except batch_manifest.ManifestError as exc:
            raise RepairError(str(exc)) from exc
        frozen_mode = str(manifest.get("mode") or "demo")
        if str(mode or frozen_mode).strip().lower() != frozen_mode:
            raise RepairError("mode does not match the frozen manifest")
        mode = frozen_mode
        frozen_config = manifest.get("config") or {}
        frozen_strategy = str(frozen_config.get("strategy") or strategy)
        if frozen_strategy not in ("automatic", "review"):
            frozen_strategy = "automatic"
        strategy = frozen_strategy
        frozen_detection = str(
            (frozen_config.get("detection") or {}).get("method") or detection_method)
        if frozen_detection not in ("absdiff", "ssim", "smart"):
            frozen_detection = "absdiff"
        detection_method = frozen_detection
        plan_detection = ("existing" if mask_policy == "existing"
                          else detection_method)
        imported = {
            "manifest_ref": str(manifest_ref),
            "rejected": False,
            "diagnostics": manifest.get("diagnostics") or [],
        }
    else:
        if sources is not None:
            sources = _sources_from_argument(sources)
            # Explicit Viewer sources are already scene input files; the
            # service still validates every one through the frozen importer.
        else:
            try:
                (sources, unresolved, workspace_scene_ids,
                 workspace_scene_refs) = _workspace_sources(
                    workspace, current, selected_images, scope)
            except RepairError as exc:
                if observed:
                    return _resource_assessment(store,observed,session_id=session_id,scope=scope,mode=mode,
                        strategy=strategy,detection_method=detection_method,mask_policy=mask_policy,
                        title=title,created_by=created_by,resolution_reason=str(exc))
                raise
        if not sources:
            raise RepairError("没有可准备的修复目标")
        try:
            imported = batch_manifest.import_manifest(
                text="", mode=mode, sources=sources, strategy=strategy,
                detection_method=import_detection,
                smart_options=smart_options)
        except batch_manifest.ManifestError as exc:
            raise RepairError(str(exc)) from exc
        try:
            manifest = batch_manifest.load_manifest(imported["manifest_ref"])
        except batch_manifest.ManifestError as exc:
            raise RepairError(str(exc)) from exc

    planned = None
    planning_error = None
    try:
        planned = batch_manifest.plan_goal_items(manifest, normalized_exclusions)
    except batch_manifest.ManifestConflict as exc:
        planning_error = exc
    targets = _targets(manifest, unresolved, planned)
    # Executable targets after exclusions, as opaque scene IDs.  This is what
    # start_repair must still find in the workspace; unresolved/excluded cards
    # are part of the full identity set, not of the executable set.
    applicable_refs = {
        item.get("scene_ref") for item in (planned or [])
        if item.get("status") != "SKIPPED"
    } if planned is not None else set()
    executable_scene_ids = [
        scene_id for scene_id in workspace_scene_ids
        if workspace_scene_refs.get(scene_id) in applicable_refs
    ]
    diagnostics = list(imported.get("diagnostics") or [])
    excluded_lines = {row["line"] for row in normalized_exclusions}
    blocking = [
        row for row in diagnostics
        if row.get("severity") == "error"
        and row.get("line") not in excluded_lines
    ]
    mask = None
    status = "READY"
    if planning_error is not None:
        status = "BLOCKED"
        blocking = list(planning_error.diagnostics) + blocking
    elif blocking:
        status = "BLOCKED"
    if status == "READY" and not targets["applicable"]:
        status = "BLOCKED"
        blocking.append({"severity": "error", "code": "no_items",
                         "message": "清单没有可执行的目标项"})
    try:
        mask = _mask_plan(mask_policy, mask_path, manifest, planned)
    except RepairError as exc:
        status = "BLOCKED"
        blocking.append({"severity": "error", "code": "mask_unavailable",
                         "message": str(exc)})

    scope_name = "single" if len(targets["items"]) == 1 else "batch"
    binding = {
        "contract_version": PLAN_CONTRACT_VERSION,
        "session_id": session_id,
        "manifest_ref": imported["manifest_ref"],
        "manifest_sha256": manifest.get("manifest_sha256"),
        "mode": mode,
        "strategy": strategy,
        "detection_method": plan_detection,
        "mask_policy": mask_policy,
        "exclusions": normalized_exclusions,
        "mask": mask,
        "scope": scope_name,
        "workspace_scene_ids": sorted(executable_scene_ids),
        "workspace_identity_digest": workspace_identity_digest,
    }
    plan_id = _plan_hash(binding)
    now = _now()
    execution = probe_execution(mode)
    plan = {
        "kind": "repair_plan.v1",
        "contract_version": PLAN_CONTRACT_VERSION,
        "plan_id": plan_id,
        "version": 1,
        "scope": scope_name,
        "status": status,
        "confirmation_required": True,
        "targets": targets,
        "strategy_policy": strategy,
        "strategy": {
            "detection": _DETECTION_LABELS.get(plan_detection,
                                               "existing_diff_pipeline"),
            "detection_method": plan_detection,
            "mask": "existing" if mask_policy == "existing" else "auto_or_existing",
            "execution": "existing_batch_runtime",
        },
        "mode": mode,
        "detection_method": plan_detection,
        "execution": execution,
        "manifest_ref": imported["manifest_ref"],
        "manifest_sha256": manifest.get("manifest_sha256"),
        "exclusions": normalized_exclusions,
        "title": str(title or _DEFAULT_TITLE)[:200],
        "session_id": session_id,
        "created_by": created_by,
        "workspace": {
            "version": workspace_version,
            "total": workspace_total,
            "local_only": int((workspace or {}).get("local_only") or 0),
            "truncated": workspace_truncated,
            # Executable targets vs the whole (possibly mixed) workspace.
            "scene_ids": sorted(executable_scene_ids),
            "identity_ids": workspace_identity_ids,
            "identity_digest": workspace_identity_digest,
            "scope": scope,
        },
        "mask": mask,
        "diagnostics": diagnostics[:200],
        "blocking": blocking[:50],
        "identities": _item_identities(manifest, planned),
        "created_at": now,
        "expires_at": now + _env_float("SR_REPAIR_PLAN_TTL_SEC", PLAN_TTL_SEC),
        "confirmation": None,
    }
    stored = store.upsert_repair_plan(plan)
    return public_plan(stored)


def public_plan(plan: dict) -> dict:
    """The model/UI facing plan; internal identities stay server-side.

    Target item ids are capped so a 500-scene plan does not blow the Agent
    result budget; ``targets.total/applicable`` remain authoritative and the
    full identity list stays in the persisted plan for start validation.
    """
    if not plan:
        return plan
    hidden = {"identities", "plan_json"}
    result = {k: v for k, v in plan.items() if k not in hidden}
    result = json.loads(json.dumps(result, ensure_ascii=False, default=str))
    targets = result.get("targets")
    if isinstance(targets, dict):
        items = targets.get("items")
        if isinstance(items, list) and len(items) > 50:
            targets["items"] = items[:50]
            targets["items_truncated"] = True
    return result


def get_repair_plan(store, plan_id: str) -> dict | None:
    return store.get_repair_plan(plan_id)


def confirm_repair(store, plan_id: str, *, session_id: str | None) -> dict:
    """Record a real user's confirmation.  The Agent cannot call this.

    The confirmation actor is derived server-side from the session; there is no
    ``confirmed_by`` argument for a caller to forge.
    """
    plan = store.confirm_repair_plan(
        plan_id, session_id=session_id,
        confirmed_by=(f"user:{session_id}" if session_id else None))
    return public_plan(plan)


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

def _check_workspace(plan: dict, workspace, workspace_version) -> None:
    """Reject a confirmed plan whose targets or workspace identity changed.

    Mixed workspaces are normal: local-only images and unresolvable cards do
    not enter the executable target set, but they **do** enter the full
    workspace identity digest.  The target set gets a superset check; the
    workspace scope additionally gets a stable identity digest instead of a
    count-only comparison.
    """
    stored = plan.get("workspace") or {}
    expected = set(stored.get("scene_ids") or [])
    if not expected:
        return
    if workspace is None:
        return
    if workspace.get("truncated"):
        raise RepairPlanConflict(
            "启动时的工作区快照被截断，无法核对完整身份；请缩小范围后重新准备",
            reason="stale_workspace")
    current_ids = {
        item.get("scene_id") for item in (workspace.get("items") or [])
        if isinstance(item, dict) and isinstance(item.get("scene_id"), str)
    }
    if not expected.issubset(current_ids):
        raise RepairPlanConflict(
            "计划中的目标影像已不在当前工作区，旧计划确认失效；请重新准备计划并确认",
            reason="stale_workspace")
    scope = stored.get("scope") or "workspace"
    if scope == "workspace":
        stored_digest = stored.get("identity_digest")
        if stored_digest:
            _, current_digest = _workspace_identity(workspace)
            if current_digest != stored_digest:
                raise RepairPlanConflict(
                    "工作区场景集合已变化，旧计划确认失效；请重新准备计划并确认",
                    reason="stale_workspace")
        else:
            # Compatibility with plans prepared before the identity digest.
            stored_total = stored.get("total")
            current_total = workspace.get("total")
            if (isinstance(stored_total, int) and isinstance(current_total, int)
                    and stored_total != current_total):
                raise RepairPlanConflict(
                    "工作区清单数量已变化，旧计划确认失效；请重新准备计划并确认",
                    reason="stale_workspace")
    stored_version = stored.get("version")
    if (workspace_version is not None and stored_version is not None
            and int(workspace_version) != int(stored_version)
            and scope == "workspace"):
        raise RepairPlanConflict(
            "工作区版本已变化，旧计划确认失效；请重新准备计划并确认",
            reason="stale_workspace")


def _check_identities(plan: dict) -> None:
    """Cheap source consistency check: revisions + backups, no full SHA256."""
    by_item = {row["item_id"]: row for row in plan.get("identities") or []}
    if not by_item:
        raise RepairPlanConflict("计划缺少目标源身份，请重新准备", reason="stale_source")
    for item_id, row in by_item.items():
        scene_ref = row.get("scene_ref")
        if not scene_ref:
            raise RepairPlanConflict("计划缺少场景目录", reason="stale_source")
        try:
            ensure_allowed(scene_ref, kind="dir")
        except PathDeniedError as exc:
            raise RepairPlanConflict(
                f"场景目录已不可访问：{exc}", reason="stale_source") from exc
        for image in row.get("images") or []:
            path = Path(str(image.get("path") or ""))
            try:
                ensure_allowed(path, kind="file")
            except PathDeniedError as exc:
                raise RepairPlanConflict(
                    f"目标源已不可访问：{exc}", reason="stale_source") from exc
            if not path.is_file():
                raise RepairPlanConflict(
                    f"目标源文件不存在：{path.name}", reason="stale_source")
            revision = preview_jpg.source_revision(path)
            frozen = image.get("frozen_revision")
            if frozen and revision != frozen:
                raise RepairPlanConflict(
                    "目标源文件在计划确认后发生变化，旧确认失效；请重新准备计划",
                    reason="stale_source")
        if row.get("backups"):
            raise RepairPlanConflict(
                "场景里已经存在修复备份，拒绝重复修复："
                + "、".join(row["backups"]), reason="stale_source")


def _plan_binding_hash(plan: dict) -> str:
    """Deterministic identity of a plan revision (no full-file digest)."""
    detection = plan.get("detection_method")
    if not detection:
        strategy = plan.get("strategy")
        detection = (strategy.get("detection_method")
                     if isinstance(strategy, dict) else strategy)
    return batch_manifest.canonical_hash({
        "plan_id": plan.get("plan_id"),
        "version": int(plan.get("version") or 0),
        "manifest_ref": plan.get("manifest_ref"),
        "manifest_sha256": plan.get("manifest_sha256"),
        "mode": plan.get("mode"),
        "detection_method": detection,
        "strategy_policy": (plan.get("strategy_policy")
                            or (plan.get("strategy")
                                if isinstance(plan.get("strategy"), str)
                                else None)),
        "exclusions": plan.get("exclusions") or [],
        "title": plan.get("title"),
        "mask_policy": plan.get("mask_policy"),
        "mask_revision": (plan.get("mask") or {}).get("revision"),
    })


def _check_manifest(plan: dict) -> dict:
    try:
        manifest = batch_manifest.load_manifest(plan["manifest_ref"])
    except batch_manifest.ManifestError as exc:
        raise RepairPlanConflict(
            f"冻结清单不可用：{exc}", reason="stale_manifest") from exc
    if manifest.get("manifest_sha256") != plan.get("manifest_sha256"):
        raise RepairPlanConflict("冻结清单摘要已变化", reason="stale_manifest")
    if (manifest.get("mode") or "demo") != plan.get("mode"):
        raise RepairPlanConflict("清单执行模式与计划不一致", reason="stale_manifest")
    try:
        planned = batch_manifest.plan_goal_items(
            manifest, plan.get("exclusions") or [])
    except batch_manifest.ManifestConflict as exc:
        raise RepairPlanConflict(str(exc), reason="stale_manifest") from exc
    expected_ids = [row.get("item_id") for row in
                    (plan.get("targets") or {}).get("items") or []]
    actual_ids = [row.get("item_id") for row in planned]
    if actual_ids != expected_ids:
        raise RepairPlanConflict(
            "冻结清单的目标项已变化，旧计划确认失效；请重新准备计划",
            reason="stale_manifest")
    return manifest


def _batch_checkpoint(manifest: dict, plan: dict) -> dict:
    review = (manifest.get("config") or {}).get("strategy") == "review"
    cp = {
        "workflow": batch_manifest.WORKFLOW,
        "manifest_ref": plan["manifest_ref"],
        "manifest_sha256": plan.get("manifest_sha256"),
        "mode": plan.get("mode") or "demo",
        "exclusions": plan.get("exclusions") or [],
        "batch_control": {"pause_requested": False, "cancel_requested": False},
        "control_revision": 0,
        "producer_status": "DISPATCHING",
        "report_ref": None,
        "repair_plan_id": plan["plan_id"],
        "repair_plan_version": int(plan.get("version") or 0),
        "repair_plan_binding": _plan_binding_hash(plan),
        "repair_scope": plan.get("scope"),
        "review_strategy": review,
    }
    return cp


def _item_checkpoint(item: dict, *, review_required: bool) -> dict:
    return {
        "phase": item["status"],
        "attempt": 0,
        "source_lines": item.get("source_lines") or [],
        "name": item.get("name"),
        "scene_ref": item.get("scene_ref"),
        "target_image": item.get("target_image"),
        "reference_image": item.get("reference_image"),
        "config": item.get("config") or {},
        "exclusion_reason": item.get("exclusion_reason") or "",
        "review_required": bool(review_required),
    }


def _prepare_manual_mask(plan: dict, item: dict) -> tuple[dict, dict]:
    """Copy + verify an operator-saved mask into the batch artifact root.

    Returns ``(mask_metadata, detection_metadata)``.  The mask is verified with
    the existing streaming verifier; no second mask implementation is added.
    """
    target = item.get("target_image") or {}
    mask_info = plan.get("mask") or {}
    source = Path(str(mask_info.get("path") or ""))
    try:
        ensure_allowed(source, kind="file")
    except PathDeniedError as exc:
        raise RepairError(f"人工掩码不在允许的盘阵前缀内：{exc}") from exc
    if not source.is_file():
        raise RepairError(f"人工掩码不存在：{source}")
    scene_ref = item.get("scene_ref")
    if not scene_ref or mask_info.get("scene_ref") != Path(scene_ref).as_posix():
        raise RepairPlanConflict(
            "人工掩码不属于当前目标场景，旧确认失效；请重新准备计划",
            reason="stale_source")
    try:
        if os.path.normcase(str(source.parent.resolve())) != os.path.normcase(
                str(Path(scene_ref).resolve())):
            raise RepairPlanConflict(
                "人工掩码必须位于目标场景目录内，不能跨景复用",
                reason="stale_source")
    except OSError as exc:
        raise RepairError(f"人工掩码目标场景不可读：{exc}") from exc
    current_revision = preview_jpg.source_revision(source)
    if mask_info.get("revision") and current_revision != mask_info["revision"]:
        raise RepairPlanConflict(
            "人工掩码在计划确认后发生变化，旧确认失效；请重新准备计划",
            reason="stale_source")
    width, height = int(target.get("width") or 0), int(target.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RepairError("冻结目标缺少 PAN 尺寸")
    item_id = str(item.get("item_id"))
    bundle = (batch_manifest.artifact_root() / "work" / plan["plan_id"]
              / item_id / "manual-1")
    bundle.mkdir(parents=True, exist_ok=True)
    dest = bundle / "mask.tif"
    tmp = bundle / f".mask-{os.getpid()}-{time.time_ns()}.tif"
    try:
        shutil.copyfile(source, tmp)
        checked = batch_mask.verify_mask(tmp, width=width, height=height)
        os.replace(tmp, dest)
    except batch_mask.MaskError as exc:
        text = str(exc)
        if "没有前景像素" in text:
            raise RepairError(
                "人工掩码为空（没有前景像素），拒绝启动修复；请重新绘制掩码") from exc
        raise RepairError(f"人工掩码不符合掩码契约：{text}") from exc
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    mask = {
        "contract_version": batch_contracts.MASK_CONTRACT_VERSION,
        "coordinate_space": batch_contracts.COORDINATE_SPACE,
        "path": dest.as_posix(),
        "sha256": checked["sha256"],
        "revision": preview_jpg.source_revision(dest),
        "width": width,
        "height": height,
        "dtype": "uint8",
        "bits": 8,
        "spp": 1,
        "background_value": 0,
        "foreground_value": 255,
        "foreground_pixels": checked["foreground_pixels"],
        "item_id": item_id,
        "source_image_key": target.get("image_key"),
        "source_revision": target.get("revision"),
        "provenance": "real",
        "builder_name": "existing-human-mask",
        "builder_version": "1.0.0",
        "detector_name": "existing-human-mask",
        "detector_version": "1.0.0",
        "detection_contract_version": batch_contracts.DETECTION_CONTRACT_VERSION,
        "compression": checked.get("compression"),
        "strip_count": checked.get("strip_count"),
        "foreground_source": source.as_posix(),
    }
    detection = {
        "contract_version": batch_contracts.DETECTION_CONTRACT_VERSION,
        "coordinate_space": batch_contracts.COORDINATE_SPACE,
        "image_size": {"width": width, "height": height},
        "item_id": item_id,
        "image_id": target.get("image_key"),
        "image_key": target.get("image_key"),
        "provenance": "real",
        "detector_name": "existing-human-mask",
        "detector_version": "1.0.0",
        "candidates": [],
        "candidate_count": 0,
        "candidate_accounting": {"total": 0, "processed": 0, "unprocessed": 0},
        "requires_geometry_review": False,
        "diagnostics": [{"code": "existing_manual_mask",
                         "source": source.as_posix()}],
    }
    return mask, detection


def _goal_content_hash(plan: dict, manifest: dict, title: str) -> str:
    mask_sha = (plan.get("mask") or {}).get("sha256")
    return batch_manifest.canonical_hash({
        "plan_id": plan["plan_id"],
        "plan_version": int(plan.get("version") or 0),
        "plan_binding": _plan_binding_hash(plan),
        "manifest_ref": plan["manifest_ref"],
        "manifest_sha256": manifest.get("manifest_sha256"),
        "mode": plan.get("mode") or "demo",
        "exclusions": plan.get("exclusions") or [],
        "title": title,
        "manual_mask_revision": (plan.get("mask") or {}).get("revision"),
        "manual_mask_sha256": mask_sha,
    })


def _create_goal(store, plan: dict, manifest: dict, *, title: str,
                 request_key: str) -> dict:
    planned = batch_manifest.plan_goal_items(
        manifest, plan.get("exclusions") or [])
    checkpoint = _batch_checkpoint(manifest, plan)
    manual = (plan.get("mask") or {}).get("policy") == "existing"
    review_required = (manifest.get("config") or {}).get("strategy") == "review"
    items = []
    for item in planned:
        cp = _item_checkpoint(item, review_required=review_required)
        status = item["status"]
        if status == "SKIPPED":
            # Excluded items keep their identity and skip reason; never turn
            # them into an executable (or manual-mask) Item.
            items.append({
                "item_id": item["item_id"],
                "ordinal": item["ordinal"],
                "name": item["name"],
                "status": "SKIPPED",
                "checkpoint": cp,
                "resource_key": (item.get("target_image") or {}).get("image_key"),
                "next_wake_at": None,
            })
            continue
        if manual:
            mask, detection = _prepare_manual_mask(plan, item)
            cp.update({
                "phase": "MASK_READY",
                "mask": mask,
                "detection": detection,
                "mask_policy": "existing",
                "review_required": False,
            })
            plan["mask"] = {**(plan.get("mask") or {}), "sha256": mask["sha256"]}
            status = "MASK_READY"
        items.append({
            "item_id": item["item_id"],
            "ordinal": item["ordinal"],
            "name": item["name"],
            "status": status,
            "checkpoint": cp,
            "resource_key": (item.get("target_image") or {}).get("image_key"),
            "next_wake_at": time.time() if status in {"PENDING", "MASK_READY"} else None,
        })
    content_sha256 = _goal_content_hash(plan, manifest, title)
    return store.create_batch_goal(
        title=title, request_key=request_key, content_sha256=content_sha256,
        checkpoint=checkpoint, items=items, session_id=plan.get("session_id"),
        max_model_calls=0)


def _goal_matches_plan(goal: dict, plan: dict) -> bool:
    cp = goal.get("checkpoint") or {}
    same_version = (int(cp.get("repair_plan_version") or 0)
                    == int(plan.get("version") or 0))
    return (
        cp.get("repair_plan_id") == plan.get("plan_id")
        and same_version
        and cp.get("repair_plan_binding") == _plan_binding_hash(plan)
        and cp.get("manifest_ref") == plan.get("manifest_ref")
        and (cp.get("mode") or "demo") == (plan.get("mode") or "demo")
    )


def start_repair(store, plan_id: str, *, session_id: str | None,
                 workspace=None, workspace_version=None,
                 plan_version: int | None = None) -> dict:
    """Start a plan that a real user has already confirmed server-side."""
    plan = store.get_repair_plan(plan_id)
    if not plan:
        raise KeyError("repair plan not found")
    if plan_version is not None and int(plan_version) != int(plan.get("version") or 1):
        raise RepairPlanConflict(
            "计划版本不匹配，请重新读取计划后确认", reason="stale_plan")
    if plan.get("session_id") is not None and plan["session_id"] != session_id:
        raise RepairPlanConflict(
            "这个计划属于另一个会话，不能在这里启动", reason="session_mismatch")
    request_key = str(plan.get("request_key") or ("repair:" + plan_id))
    if plan.get("status") == "STARTED":
        goal_id = plan.get("goal_id")
        goal = store.get_goal(goal_id) if goal_id else None
        if goal is None:
            goal = store.get_goal_by_request_key(request_key)
        if goal:
            if not _goal_matches_plan(goal, plan):
                raise RepairPlanConflict(
                    "计划关联的 Goal 与当前计划版本不匹配，拒绝复用",
                    reason="goal_conflict")
            updated = store.bind_repair_plan_goal(plan_id, goal["goal_id"])
            return {"plan": public_plan(updated), "goal": _goal_view(goal),
                    "reused": True}
        raise RepairPlanConflict(
            "计划标记为已启动但没有可恢复的 Goal，请重新准备",
            reason="goal_conflict")
    if plan.get("status") != "CONFIRMED":
        reason = ("expired" if plan.get("status") == "EXPIRED"
                  else "confirmation_required")
        raise RepairPlanConflict(
            "计划还没有通过用户确认，不能启动", reason=reason)
    now = time.time()
    if float(plan.get("expires_at") or 0) <= now:
        store.update_repair_plan_status(plan_id, "EXPIRED", "plan_expired")
        raise RepairPlanConflict("计划已过期，请重新准备并确认", reason="expired")
    confirmation_expires = plan.get("confirmation_expires_at")
    if confirmation_expires is not None and float(confirmation_expires) <= now:
        store.update_repair_plan_status(plan_id, "EXPIRED", "confirmation_expired")
        raise RepairPlanConflict(
            "用户确认已超过有效期，请重新确认后启动", reason="confirmation_expired")
    title = str(plan.get("title") or _DEFAULT_TITLE)
    # A previous call may have committed the Goal but lost the response before
    # recording ``goal_id`` on the plan.  Recover that exact row **before**
    # source consistency checks: a successful repair changes the PAN/backup, and
    # that must not turn a recoverable start into a stale plan.
    existing = store.get_goal_by_request_key(request_key)
    if existing is not None and existing.get("kind") == "batch":
        if not _goal_matches_plan(existing, plan):
            raise RepairPlanConflict(
                "幂等键对应的既有 Goal 与当前计划版本不匹配，拒绝复用",
                reason="goal_conflict")
        updated = store.bind_repair_plan_goal(plan_id, existing["goal_id"])
        return {"plan": public_plan(updated), "goal": _goal_view(existing),
                "reused": True}
    try:
        _check_workspace(plan, workspace, workspace_version)
        manifest = _check_manifest(plan)
        _check_identities(plan)
    except RepairPlanConflict as exc:
        # A changed target/list/source invalidates this confirmation; preparing
        # the same binding again resets it to READY for a fresh confirmation.
        store.update_repair_plan_status(plan_id, "SUPERSEDED", exc.reason)
        raise
    execution = probe_execution(plan.get("mode") or "demo")
    if not execution["allowed"]:
        raise RepairBlocked(execution["reason"] or "当前执行环境不允许启动修复",
                            reason="execution_blocked")
    try:
        goal = _create_goal(store, plan, manifest, title=title,
                            request_key=request_key)
    except RepairPlanConflict as exc:
        store.update_repair_plan_status(plan_id, "SUPERSEDED", exc.reason)
        raise
    if not _goal_matches_plan(goal, plan):
        raise RepairPlanConflict(
            "创建出的 Goal 与当前计划版本不匹配，拒绝关联",
            reason="goal_conflict")
    updated = store.bind_repair_plan_goal(plan_id, goal["goal_id"])
    return {"plan": public_plan(updated), "goal": _goal_view(goal),
            "reused": False}


# ---------------------------------------------------------------------------
# read projections (Goal/Item/SR/quality remain the only source of truth)
# ---------------------------------------------------------------------------

def verified_output_path(item: dict) -> str | None:
    """The SR output this Item actually verified, or ``None``.

    Only a passed ``artifacts`` layer names an output file.  Demo / Fake SR
    marks that layer ``not_applicable`` (no output exists), and a failed check
    must never be presented as a repair result.  Returning the *target image*
    path as a fallback would claim the original PAN is the result, which is
    exactly the identity confusion this projection exists to prevent.
    """
    # A failed/blocked Item may still carry a partially built verification; it
    # must never be offered as a result.  These are exactly the states in which
    # a passed artifacts layer describes the Job's live output.
    if item.get("status") not in {
            "VERIFYING", "AWAITING_REVIEW", "REVIEW_REJECTED", "SUCCEEDED"}:
        return None
    verification = item.get("verification")
    if not isinstance(verification, dict):
        return None
    layers = verification.get("layers")
    if not isinstance(layers, dict):
        return None
    artifacts = layers.get("artifacts")
    if not isinstance(artifacts, dict) or artifacts.get("status") != "passed":
        return None
    path = artifacts.get("path")
    return path if isinstance(path, str) and path else None


def _goal_view(goal: dict) -> dict:
    view = {key: goal.get(key) for key in
            ("goal_id", "session_id", "title", "status", "phase", "kind",
             "version", "created_at", "updated_at", "finished_at")}
    # Explicit convergence: the checkpoint marker wins over the mirrored
    # column, and a missing value is **not** convergence.  Callers must never
    # read ``None`` as "done".
    done = bool(batch_snapshot.monitoring_done(goal))
    view["monitoring_done"] = done
    view["monitor_done"] = done
    return view


def _report_info(goal_id: str) -> dict:
    path = batch_manifest.artifact_root() / "reports" / f"{goal_id}.json"
    try:
        stat = path.stat()
    except OSError:
        return {"available": False, "updated_at": None}
    return {"available": True, "updated_at": stat.st_mtime}


def get_repair_status(store, plan_id: str) -> dict:
    plan = store.get_repair_plan(plan_id)
    if not plan:
        raise KeyError("repair plan not found")
    result = {
        "plan": public_plan(plan),
        "goal": None,
        "counts": None,
        "items": [],
        "active_jobs": [],
        "quality": None,
        "report": {"available": False, "updated_at": None},
    }
    goal_id = plan.get("goal_id")
    if not goal_id:
        return result
    goal = store.get_goal(goal_id)
    if not goal:
        return result
    result["goal"] = _goal_view(goal)
    if goal.get("kind") != "batch":
        return result
    snapshot = store.batch_snapshot(goal_id)
    if snapshot:
        result["counts"] = snapshot.get("counts")
        result["goal"] = {**_goal_view(goal),
                          "control_revision": snapshot["goal"].get("control_revision"),
                          "monitoring_done": snapshot["goal"].get("monitoring_done"),
                          "monitor_done": bool(snapshot["goal"].get("monitoring_done"))}
        result["active_jobs"] = snapshot.get("active_jobs") or []
        result["quality"] = snapshot.get("quality")
        result["progress"] = snapshot.get("progress")
    items = store.list_goal_items(goal_id, limit=500)
    review_ids = []
    result["items"] = []
    for item in items:
        cp = item.get("checkpoint") or {}
        target = cp.get("target_image") or {}
        mask = cp.get("mask") or {}
        review_id = cp.get("quality_review_id")
        if review_id:
            review_ids.append(review_id)
        result["items"].append({
            "item_id": item.get("item_id"),
            "ordinal": item.get("ordinal"),
            "name": item.get("name"),
            "status": item.get("status"),
            "attempt": int(cp.get("attempt") or 0),
            "error": item.get("error"),
            "review": cp.get("review"),
            "review_required": bool(cp.get("review_required")),
            "review_decision": (cp.get("review") or {}).get("decision"),
            "quality_review_id": review_id,
            "verification_ok": (item.get("verification") or {}).get("ok"),
            "job_id": item.get("external_job_id") or cp.get("job_id"),
            "sr_status": cp.get("sr_status"),
            "image_path": target.get("path"),
            "output_path": verified_output_path(item),
            "overlay_ready": bool(mask.get("overlay")),
            "phase": cp.get("phase"),
            "candidate_count": len((cp.get("detection") or {}).get("candidates") or []),
        })
    # Review counts are limited to the reviews currently bound to Items; a
    # re-run's superseded review must not keep inflating the acceptance view.
    result["quality_reviews"] = store.batch_quality_review_counts(
        goal_id, review_ids=review_ids)
    result["quality_products"] = store.batch_quality_product_counts(
        goal_id, review_ids=review_ids)
    result["quality_regions"] = store.batch_quality_region_counts(
        goal_id, review_ids=review_ids)
    result["acceptance"] = _acceptance(
        plan, result["goal"], result["quality_reviews"])
    result["report"] = _report_info(goal_id)
    return result


def _acceptance(plan: dict, goal: dict | None, reviews: dict | None) -> dict:
    """Separate computation, file-contract verification and human acceptance.

    ``computed`` only turns true after the Goal is terminal **and** its
    Monitor has explicitly converged.  ``quality_passed`` additionally requires
    production mode, a completed Goal and no pending/rejected review; it is
    never true for a demo/Fake SR run.
    """
    mode = (plan or {}).get("mode") or "demo"
    status = (goal or {}).get("status") or ""
    monitoring_done = bool((goal or {}).get("monitoring_done"))
    terminal = status in {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED"}
    computed = bool(goal) and terminal and monitoring_done
    counts = reviews or {"approved": 0, "rejected": 0, "pending": 0}
    required = int(counts.get("approved") or 0) + int(
        counts.get("rejected") or 0) + int(counts.get("pending") or 0)
    quality_passed = bool(
        mode == "production" and computed and status == "COMPLETED"
        and int(counts.get("pending") or 0) == 0
        and int(counts.get("rejected") or 0) == 0
        and (required == 0 or int(counts.get("approved") or 0) == required))
    return {
        "mode": mode,
        "computed": computed,
        "quality_required": required,
        "quality_approved": int(counts.get("approved") or 0),
        "quality_rejected": int(counts.get("rejected") or 0),
        "quality_pending": int(counts.get("pending") or 0),
        "quality_passed": quality_passed,
    }


def get_repair_result(store, plan_id: str) -> dict:
    status = get_repair_status(store, plan_id)
    plan = status["plan"]
    goal = status["goal"]
    if not goal:
        return {"plan_id": plan_id, "state": "not_started",
                "goal": None, "report": None,
                "production_verified": False,
                "quality_passed": False,
                "note": "计划尚未启动，没有修复产物"}
    report = None
    if status["report"]["available"]:
        path = batch_manifest.artifact_root() / "reports" / f"{goal['goal_id']}.json"
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
    mode = plan.get("mode") or "demo"
    verified = int((status.get("counts") or {}).get("verified") or 0)
    failed = int((status.get("counts") or {}).get("failed") or 0)
    acceptance = status.get("acceptance") or {}
    return {
        "plan_id": plan_id,
        "state": "running" if not acceptance.get("computed") else "settled",
        "goal": goal,
        "counts": status.get("counts"),
        "items": status.get("items") or [],
        "report": report,
        "verified_items": verified,
        "failed_items": failed,
        "acceptance": acceptance,
        # A demo contract run is never production quality acceptance.
        "success_scope": "demo_contract" if mode == "demo" else "production_contract",
        # File-contract verification and human quality acceptance are distinct.
        "production_verified": False,
        "quality_passed": bool(acceptance.get("quality_passed")),
        "note": ("演示/Fake SR 结果只证明契约链路，不代表真实 SR 质量验收"
                 if mode == "demo" else
                 "SR 计算完成不等于修复质量通过；以逐区域人工审核结果为准"),
    }
