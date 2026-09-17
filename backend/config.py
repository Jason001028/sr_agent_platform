"""Runtime configuration for the agent platform (env-driven, no framework deps).

Every value is read from an environment variable so the same code speaks the
OpenAI-compatible wire protocol against any endpoint — cloud API, Ollama,
vLLM — with zero code changes (LLM backend intentionally unpinned, see
docs/knowledge/agent-orchestration-research.md §1).

    SR_LLM_BASE_URL    base URL, default https://api.openai.com/v1
    SR_LLM_API_KEY     API key, default "" (local endpoints accept empty)
    SR_LLM_MODEL       model id, default gpt-4o-mini
    SR_LLM_MAX_TOKENS  per-turn completion cap, default 1024
    SR_LLM_TEMPERATURE sampling temperature, default 0.2
    SR_LLM_TIMEOUT     HTTP timeout seconds, default 60
    SR_LLM_MOCK        =1 → _default_chat 返回假 chat（固定脚本，先调 search_scenes
                       再回最终回复，不连任何端点）—— 离机验收/前端 e2e 的 mock LLM。

Two independent groups live here:

* ``Config`` / ``load_config()`` — the **agent loop** (LLM endpoint). Read once
  at startup, passed around as a snapshot.
* ``SrRuntime`` / ``sr_runtime()`` — the **SR pipeline + Slurm layer** (bundle
  path, job interpreter, work dir, partition, job limits). See §"SrRuntime".

This module must never import from ``backend.services`` (or any other backend
package): the services import *it*, so a back-import would close a cycle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# --------------------------------------------------------------------------
# SR pipeline defaults — the values the code used before they were centralised.
# Kept as module constants so run_sr.py can re-export them under its historical
# DEFAULT_* names (test_run_sr.py and callers import those).
# --------------------------------------------------------------------------

#: mmsr_bundle/codes on the CentOS7 array server — holds code_0817_prod.py,
#: models/, utils/, options/ and tools/ImgHistMatch.so. The spelling matches
#: the load_library() call at code_0817_prod.py:27 (that call is the authority:
#: a bundle dir that disagrees with it cannot run).
SR_DEFAULT_BUNDLE_DIR = "/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes"

#: Local scratch by default — fine on a single node, WRONG on a multi-node
#: partition: a batch script/config.xml written here does not exist on the node
#: that runs the job. Must point at shared storage whenever `sinfo -N` shows
#: more than one node (docs/status/slurm-integration.md §2.6).
SR_DEFAULT_WORK_DIR = "/tmp/sr_agent_work"

#: Options yml passed as <OPT>; carries scale/tile size and tif_type.
SR_DEFAULT_OPTIONS_YML = "/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml"

#: Interpreter that runs code_0817_prod.py *inside the job* — the SR production
#: env, a different world from the API's own /opt/sr-venv (py3.9).
#: 2026-09-14 node81-135 实测：torch 1.10.2+cu113 / GDAL 2.4.0（版本号用
#: `gdal.VersionInfo()` 取——py3.6 时代的 osgeo 绑定没有 `gdal.__version__`）。
#: ⚠️ 目录名叫 `torch1.9.1py36`，但里面装的**不是** 1.9.1 —— 别拿目录名当版本号。
#: See docs/status/real-machine-bringup.md §1.
SR_DEFAULT_PYTHON = "python"

#: Slurm job limits/limits applied by run_sr.build_batch_script.
#: No default node list: unset means "let the scheduler decide" (the behaviour
#: every cluster had before 2026-09-14). SR_SLURM_NODELIST exists because the
#: `gpu` partition spans two node families and one of them (node104-*) cannot be
#: resolved from the submitting host — see docs/status/slurm-acceptance.md §B0.
SR_DEFAULT_SLURM_TIME = "02:00:00"
SR_DEFAULT_SLURM_CPUS = 4
SR_DEFAULT_SLURM_GRES = 1

#: How the SR job is launched: "slurm" (sbatch on the cluster) or "local"
#: (a plain child process on the API host, backend/services/local_exec.py).
#: Local is the minimal-prototype mode — see
#: docs/planning/sr-minimal-prototype-plan.md §1.2. Default stays "slurm" so an
#: un-updated deployment keeps behaving exactly as before.
SR_DEFAULT_EXECUTOR = "slurm"

#: The one scene directory /api/queue accepts while the prototype is pinned to
#: a single test directory (SR_LOCKED_DIR). Unset → the historical behaviour
#: (any absolute path the caller sends). Local mode + a set value means every
#: submit must name this directory and nothing else.
SR_DEFAULT_LOCKED_DIR = None

#: GPU handed to the local child via CUDA_VISIBLE_DEVICES (SR_LOCAL_GPU). Only
#: meaningful with SR_EXECUTOR=local — under Slurm the card comes from --gres.
SR_DEFAULT_LOCAL_GPU = "0"

#: LAST-RESORT <Suffix>, not a knob. The default a submit actually gets is read
#: from the SR team's own config inside SR_BUNDLE_DIR (services/run_sr.py::
#: default_suffix); this literal is only what's left when that file is missing,
#: unparseable or carries an unusable value. It used to be overridable through
#: SR_SUFFIX_DEFAULT — that env var was retired 2026-09-16, the file is the
#: authority. Non-empty on purpose: an empty suffix makes the output name equal
#: the input name, which SR turns into a rename of the input
#: (sr-pipeline-interface.md §2.4-1).
SR_DEFAULT_SUFFIX = "sr"

#: Queue-state calibration period for GET /api/queue + SSE job_update frames.
SR_DEFAULT_QUEUE_POLL_SEC = 2.0

#: SQLite path when SR_AGENT_DB is unset (services.store.DEFAULT_DB, duplicated
#: here so this module stays free of service imports).
SR_DEFAULT_DB = "sr_agent.db"


@dataclass(frozen=True)
class Config:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_max_tokens: int
    llm_temperature: float
    llm_timeout: float
    llm_mock: bool = False


def load_config() -> Config:
    """Build Config from environment variables (unknown vars keep defaults)."""

    def env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        return int(raw) if raw is not None else default

    def env_float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        return float(raw) if raw is not None else default

    return Config(
        llm_base_url=os.environ.get("SR_LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_api_key=os.environ.get("SR_LLM_API_KEY", ""),
        llm_model=os.environ.get("SR_LLM_MODEL", "gpt-4o-mini"),
        llm_max_tokens=env_int("SR_LLM_MAX_TOKENS", 1024),
        llm_temperature=env_float("SR_LLM_TEMPERATURE", 0.2),
        llm_timeout=env_float("SR_LLM_TIMEOUT", 60),
        llm_mock=os.environ.get("SR_LLM_MOCK") == "1",
    )


# --------------------------------------------------------------------------
# SR pipeline / Slurm runtime
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SrRuntime:
    """Read-only snapshot of the SR-path environment.

    Deliberately NOT folded into ``Config``: that one is the agent loop's LLM
    endpoint and is loaded once; these values belong to the SR/Slurm layer and
    are read on every call (see ``sr_runtime``), because tests and the systemd
    unit set them via the environment around the call.
    """

    bundle_dir: str
    python: str
    slurm_work_dir: str
    partition: str            # "" → no --partition line in the batch script
    gres: int
    time: str                 # Slurm --time, "HH:MM:SS"
    cpus: int
    mem: str                  # "" → no --mem line
    nodelist: str             # "" → no --nodelist line (scheduler picks)
    fake: bool
    queue_poll_sec: float
    agent_db: str
    # 注：**没有** scenes_root。场景根的唯一读取处是 backend/api/paths.py::
    # scenes_root()（每次读环境、且要求目录存在）—— 在这里再镜像一份就等于
    # 制造第二处真源，SR/Slurm 层也没人消费它（2026-09-17 删除）。
    sandbox_root: str | None  # None → SR runs in place, writing to lq_path
    executor: str             # "slurm" | "local" — who launches the job
    locked_dir: str | None    # None → /api/queue accepts any absolute lq_path
    local_gpu: str            # CUDA_VISIBLE_DEVICES for the local child


def sr_runtime() -> SrRuntime:
    """Snapshot the SR env **at call time** (never cached).

    Reading on each call is the contract: the systemd unit exports these once
    for a long-lived process, while tests set them in setUp/tearDown, so a
    cached snapshot would leak values across cases.
    """

    def env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        try:
            return int(raw) if raw is not None else default
        except ValueError:
            return default

    def env_float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        try:
            return float(raw) if raw is not None else default
        except ValueError:
            return default

    return SrRuntime(
        bundle_dir=os.environ.get("SR_BUNDLE_DIR") or SR_DEFAULT_BUNDLE_DIR,
        python=os.environ.get("SR_PYTHON") or SR_DEFAULT_PYTHON,
        slurm_work_dir=os.environ.get("SR_SLURM_WORK_DIR") or SR_DEFAULT_WORK_DIR,
        partition=os.environ.get("SR_SLURM_PARTITION") or "",
        gres=env_int("SR_SLURM_GRES", SR_DEFAULT_SLURM_GRES),
        time=os.environ.get("SR_SLURM_TIME") or SR_DEFAULT_SLURM_TIME,
        cpus=env_int("SR_SLURM_CPUS", SR_DEFAULT_SLURM_CPUS),
        mem=os.environ.get("SR_SLURM_MEM") or "",
        nodelist=os.environ.get("SR_SLURM_NODELIST") or "",
        fake=os.environ.get("SR_SLURM_FAKE") == "1",
        queue_poll_sec=env_float("SR_QUEUE_POLL_SEC", SR_DEFAULT_QUEUE_POLL_SEC),
        agent_db=os.environ.get("SR_AGENT_DB") or SR_DEFAULT_DB,
        sandbox_root=os.environ.get("SR_SANDBOX_ROOT") or None,
        executor=(os.environ.get("SR_EXECUTOR") or SR_DEFAULT_EXECUTOR).strip().lower(),
        locked_dir=os.environ.get("SR_LOCKED_DIR") or None,
        local_gpu=os.environ.get("SR_LOCAL_GPU") or SR_DEFAULT_LOCAL_GPU,
    )
