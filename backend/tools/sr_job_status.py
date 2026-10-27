"""sr_job_status — poll an SR job's state (thin wrapper over the job service).

Companion to run_sr: the agent submits a job, then polls it here until terminal
(COMPLETED/FAILED) before reporting the result.
"""

from __future__ import annotations

from backend.services import run_sr, store as store_mod
from .contract import err, ok, tool


@tool(
    name="sr_job_status",
    description=(
        "只读查询已提交 SR 作业的执行状态，不提交、取消或修复作业。"
        "按 job_id 查本地任务以定位判定文件，再由 SR_EXECUTOR 选择统一查询服务；返回 job_id、active、state、exit_code。"
        "本机 PENDING 为排队、RUNNING 为运行；真实作业终态依据作业判定文件：COMPLETED 表示契约通过，FAILED 表示契约失败，进程退出码 0 本身不足以证明成功。"
        "本机已取消且没有有效判定文件时返回 CANCELLED，Slurm 分支可返回调度器活动状态。"
        "UNKNOWN 表示现有活动记录及判定信息不足以确定状态，不等于本地无任务记录，更不能证明作业没有执行；active 可为 null，exit_code 未知时为 null。"
        "查询服务抛错时返回 ok=false，与成功查询到 UNKNOWN 不同。活动作业需等待后继续查询，UNKNOWN 需查询/外部对账，不得直接重新提交。"
        "作业终态不等于 Goal 的业务验收完成，仍需独立核验产物与目标条件。"
    ),
    params_schema={
        "type": "object",
        "properties": {
            "job_id": {
                "type": "integer",
                "description": "run_sr 提交/复用回执或共享队列给出的整数 job_id；不是任务指纹、result_id 或 Goal ID。缺少本地任务记录时仍尝试查询执行器，但可能无法定位判定文件。",
            },
        },
        "required": ["job_id"],
    },
)
def run_sr_job_status(**params) -> dict:
    try:
        job_id = int(params["job_id"])
    except (KeyError, TypeError, ValueError) as e:
        return err(f"bad params: {e}")
    try:
        task = store_mod.default_store().get_sr_task_by_job(job_id)
        # The service owns Executor dispatch even when no task receipt exists.
        data = run_sr.query_job_status(job_id, task=task)
    except Exception as e:  # noqa: BLE001 — contract boundary
        return err(f"{type(e).__name__}: {e}")
    return ok(data)
