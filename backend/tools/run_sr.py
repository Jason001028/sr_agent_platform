"""run_sr — submit a super-resolution job (thin wrapper).

Business logic in backend/services/run_sr.py: high-level params → config.xml
+ run script → executor (idempotent via the sr_tasks table). Returns a job_id;
the agent polls it with the sr_job_status tool. Async by design (production
runs the job as a child process on the API host).

Placeholder / non-absolute paths are rejected here as err() — search_scenes
fake results carry `<fake>/...` paths that must never reach the executor. The
err is fed back to the model (contract boundary), so it can change the params
and retry; it never raises.
"""

from __future__ import annotations

from backend.pathguard import (
    PathDeniedError, normalize_submit_path, to_posix_array_path)
from backend.services import run_sr as svc
from backend.services import store as store_mod
from backend.services.scene_search import derived_mask_path
from .contract import err, ok, tool
from backend.services.goal_lease import current_tool

_DESC = (
    "异步提交 PAN 局部修复作业：以场景目录的 <目录名>_NOSR.tif 为低分输入，掩码内做模型 SR，"
    "掩码外用 NOSR 三次插值；不是任意影像的整图超分工具。"
    "适配器要求同景 PAN.tif、NOSR、掩码和 <目录名>_meta.xml，各栅格为单波段，PAN 与 NOSR 样本类型一致，PAN 为未压缩 TIFF，"
    "NOSR 分辨率低于 PAN；校验候选尺寸和字节数与 PAN 一致后，将旧 PAN 备份为 PAN_old_2.tif 并发布新 PAN.tif，已有备份会拒绝执行。"
    "lq_path 和显式 mask_path 经盘阵白名单及映射归一，拒绝 Fake 占位路径；掩码省略时推导路径，不退化成整图处理，缺文件可能在取得 job_id 后的作业内失败。"
    "提交会写任务记录、Tool Intent、XML 和批脚本，由 SR_EXECUTOR 选择本机进程或 Slurm；作业另写暂存文件、日志、回执和 PAN 文件。"
    "返回 job_id、status、idempotent、config_xml、batch_script、log_dir；SUBMITTED 是提交回执，RESUMED_ACTIVE/RESUMED_COMPLETED 表示复用。"
    "相同有效参数及输入修订信息按指纹复用活动或已完成任务，已知失败可能重新提交；状态不确定或 external_unknown 时先查询/对账，不盲目重试。"
    "用 sr_job_status 查询结果；工具 ok=true 不等于修复完成或 Goal 业务验收通过。"
)


def _bad_path(path: str) -> str | None:
    """Why a path cannot be handed to run_sr, or None if it is acceptable.

    Rejects search_scenes fake rows (`<fake>/...`), relative paths, and anything
    outside `SR_ALLOWED_ROOTS` — only real-array absolute paths make sense, and
    the job writes its output *into* lq_path. Uses the same `pathguard` rules as
    the REST entry point, so both entries accept exactly the same set of paths.
    This is validation returning an err (the loop self-heals), not an exception.
    """
    if not path:
        return None
    if "<fake>" in path:
        return ("is a fake/placeholder path from search_scenes fake results — "
                "pick a real scene path on the array")
    try:
        to_posix_array_path(path)
    except PathDeniedError as e:
        return f"must be an absolute array path ({e})"
    try:
        normalize_submit_path(path)
    except PathDeniedError as e:
        return f"must be inside an allowed array prefix ({e})"
    return None


@tool(
    name="run_sr",
    description=_DESC,
    params_schema={
        "type": "object",
        "properties": {
            "lq_path": {
                "type": "string",
                "description": "场景目录 DatarootLQ，含待修复 PAN.tif 与 <目录名>_NOSR.tif；不是影像文件路径。使用已确认的真实盘阵绝对目录或 SR_DRIVE_MAP 可映射的盘符目录，须在 SR_ALLOWED_ROOTS 内；search_scenes.path 是文件路径，不能原样作为目录。",
            },
            "mask_path": {
                "type": "string",
                "description": "现有 ROI 掩码 TIFF 的盘阵绝对路径（同样受映射与白名单约束），非零像素参与修复，尺寸须匹配 PAN 或 NOSR。省略或空串时推导 <lq_path>/<输入影像 stem>_mask.tif，无法识别输入时以目录名为 stem；不自动整图处理，缺失或空掩码会使作业内校验失败。",
            },
            "sr_scale": {
                "type": "integer", "minimum": 1, "default": 2,
                "description": "写入 XML SRScale 的正整数，省略为 2；实际推理还受 options_yml 控制，修复候选必须匹配现有 PAN 尺寸，不能据此承诺将当前 PAN 再放大此倍数。",
            },
            "suffix": {
                "type": "string", "default": "",
                "description": "内部推理产物及作业文件使用的后缀，1..16 个英文字母、数字、_ 或 -；去除首尾空白。省略或空串先读 SR_BUNDLE_DIR 配置的有效 Suffix，不可用时回退 sr；最终发布文件仍是 PAN.tif。",
            },
            "gpu": {
                "type": "integer", "minimum": 0, "default": 0,
                "description": "写入 XML GPUIDS 的非负整数，省略为 0；不保证选择同编号物理 GPU，实际选卡由执行器及生产脚本决定，本机执行器另读 SR_LOCAL_GPU。",
            },
            "cloud_limit": {
                "type": "integer", "minimum": 0, "maximum": 100, "default": 80,
                "description": "写入 CloudLimit 的云量百分比上限，0..100，省略为 80。生产推理可在云量超限时跳过，但 PAN 修复要求有效修复产物，跳过会被适配器判为失败，不能当作修复成功。",
            },
            "delete_ori": {
                "type": "boolean", "default": False,
                "description": "省略为 false；true 被服务拒绝。此开关不控制 PAN 修复发布，false 时成功修复仍会备份旧 PAN 并替换 PAN.tif。",
            },
            "grid_align": {
                "type": "boolean", "default": True,
                "description": "是否启用生产推理的 MTA-Grid 偏移对齐；省略为 true，false 时写入 XML GridAlign=false，不改变最终 PAN 尺寸校验。",
            },
            "options_yml": {
                "type": "string",
                "description": "SR 生产解释器可读取的网络配置 YAML 文件路径，写入 XML OPT；省略或空串使用服务的 DEFAULT_OPTIONS_YML。此路径不会按 lq_path 的盘符规则归一，文件可用性由作业验证。",
            },
        },
        "required": ["lq_path"],
    },
)
def run_run_sr(**params) -> dict:
    try:
        lq_path = str(params["lq_path"]).strip()
        sr_scale = int(params.get("sr_scale", 2))
        gpu = int(params.get("gpu", 0))
        cloud_limit = int(params.get("cloud_limit", 80))
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")

    if not lq_path:
        return err("lq_path is required")
    bad = _bad_path(lq_path)
    if bad:
        return err(f"lq_path {bad}")
    # 归一化（不是原样透传）：`W:\...` 与 `/DiskArray/...` 必须落到同一个字符串，
    # 否则 REST 与工具两个入口对同一次提交算出两个指纹、幂等失效。
    lq_path = normalize_submit_path(lq_path)
    mask_path = params.get("mask_path")
    if mask_path:
        mask_path = str(mask_path).strip()
        bad = _bad_path(mask_path)
        if bad:
            return err(f"mask_path {bad}")
        mask_path = normalize_submit_path(mask_path)
    else:
        # 不传就按同一份规则推导（`<输入名>_mask.tif` 落在影像旁边），与 REST
        # 入口 _norm_sr_params 完全一致。以前这里留 None → 配置 XML 里没有
        # <MaskPath>，而同一次提交走 REST 却带着推导出来的路径：两边指纹不同，
        # 幂等层认不出这是同一个作业。
        mask_path = derived_mask_path(lq_path)
    if sr_scale < 1:
        return err("sr_scale must be >= 1")
    if gpu < 0:
        return err("gpu must be >= 0")
    if not (0 <= cloud_limit <= 100):
        return err("cloud_limit must be in 0..100")
    # Same normalization the REST entry point applies (api/platform.py::
    # _norm_sr_params), deliberately shared and not copied: the two entries must
    # agree on the effective suffix or an identical logical submit produces two
    # different task_fingerprints and therefore a duplicate job instead of a
    # reuse. Returns err rather than raising — this is a contract boundary, the
    # model is meant to read the rule and retry.
    try:
        suffix = svc.normalize_suffix(params.get("suffix"))
    except ValueError as e:
        return err(str(e))

    params = svc.repair_request({
        "lq_path": lq_path, "mask_path": mask_path,
        "sr_scale": sr_scale, "suffix": suffix, "gpu": gpu,
        "cloud_limit": cloud_limit,
        "delete_ori": bool(params.get("delete_ori", False)),
        "grid_align": bool(params.get("grid_align", True)),
        "options_yml": params.get("options_yml"),
        "repair": True,
    })
    try:
        execution = current_tool.get()
        data = svc.submit_run_sr(params, store=execution["store"] if execution else store_mod.default_store())
    except svc.SubmissionUnknown as e:
        return {**err(str(e)), "error_class":"external_unknown"}
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    return ok(data)
