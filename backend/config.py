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
* ``SrRuntime`` / ``sr_runtime()`` — the **SR pipeline layer** (bundle path,
  job interpreter, work dir, GPU). See §"SrRuntime".

This module must never import from ``backend.services`` (or any other backend
package): the services import *it*, so a back-import would close a cycle.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# --------------------------------------------------------------------------
# SR pipeline defaults — kept as module constants so run_sr.py can re-export
# them under the DEFAULT_* names (test_run_sr.py and callers import those).
# --------------------------------------------------------------------------

#: mmsr_bundle/codes on the CentOS7 array server — holds code_0817_prod.py,
#: models/, utils/, options/ and tools/ImgHistMatch.so. The spelling matches
#: the load_library() call at code_0817_prod.py:27 (that call is the authority:
#: a bundle dir that disagrees with it cannot run).
SR_DEFAULT_BUNDLE_DIR = "/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes"

#: Work dir for the generated config.xml / run script / job log, and for the
#: executor's own bookkeeping (job-id counter, cross-process slot lock, run
#: records). Local scratch by default; on the real host it points at a disk on
#: the data array that the service account can write.
SR_DEFAULT_WORK_DIR = "/tmp/sr_agent_work"

#: Options yml passed as <OPT>; carries scale/tile size and tif_type.
SR_DEFAULT_OPTIONS_YML = "/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml"

#: Interpreter that runs code_0817_prod.py — the SR production env, a different
#: world from the API's own /opt/sr-venv (py3.9). The API launches it directly
#: as a child process, so this must be an absolute path (or a name resolvable on
#: the API host's PATH).
#: node81-135 实测：torch 1.10.2+cu113 / GDAL 2.4.0（版本号用
#: `gdal.VersionInfo()` 取——py3.6 时代的 osgeo 绑定没有 `gdal.__version__`）。
#: ⚠️ 目录名叫 `torch1.9.1py36`，但里面装的**不是** 1.9.1 —— 别拿目录名当版本号。
#: See docs/status/real-machine-bringup.md §1.
SR_DEFAULT_PYTHON = "python"

#: The launcher. The platform runs the SR script as a child process on the API
#: host (backend/services/local_exec.py); there is no other route. The variable
#: is still read so a leftover `SR_EXECUTOR=slurm` in a unit file or a drop-in
#: fails loudly instead of being silently ignored — see sr_runtime().
SR_DEFAULT_EXECUTOR = "local"

#: The one scene directory /api/queue accepts while the deployment is pinned to
#: a single test directory (SR_LOCKED_DIR). Unset → any absolute path the caller
#: sends. A set value means every submit must name this directory and nothing
#: else.
SR_DEFAULT_LOCKED_DIR = None

#: GPU handed to the SR child via CUDA_VISIBLE_DEVICES (SR_LOCAL_GPU). The API
#: launches the job itself, so this variable is the only thing that picks a
#: card; the SR script reads the variable instead of hardcoding an index.
SR_DEFAULT_LOCAL_GPU = "0"

#: LAST-RESORT <Suffix>, not a knob. The default a submit actually gets is read
#: from the SR team's own config inside SR_BUNDLE_DIR (services/run_sr.py::
#: default_suffix); this literal is only what's left when that file is missing,
#: unparseable or carries an unusable value. The file is the authority, not any
#: env var. Non-empty on purpose: an empty suffix makes the output name equal
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
# SR pipeline runtime
# --------------------------------------------------------------------------

#: Legacy spelling of SR_WORK_DIR. Read as a fallback so an existing unit file
#: or drop-in keeps pointing the platform at the same directory it used before;
#: nothing migrates or deletes files on its own.
SR_LEGACY_WORK_DIR_ENV = "SR_SLURM_WORK_DIR"


@dataclass(frozen=True)
class SrRuntime:
    """Read-only snapshot of the SR-path environment.

    Deliberately NOT folded into ``Config``: that one is the agent loop's LLM
    endpoint and is loaded once; these values belong to the SR layer and are
    read on every call (see ``sr_runtime``), because tests and the systemd unit
    set them via the environment around the call.
    """

    bundle_dir: str
    python: str
    work_dir: str
    queue_poll_sec: float
    agent_db: str
    # 注：**没有** scenes_root。场景根的唯一读取处是 backend/api/paths.py::
    # scenes_root()（每次读环境、且要求目录存在）—— 在这里再镜像一份就等于
    # 制造第二处真源，SR 层也没人消费它。
    executor: str             # always "local"; any other value is a config error
    locked_dir: str | None    # None → /api/queue accepts any absolute lq_path
    local_gpu: str            # CUDA_VISIBLE_DEVICES for the SR child


def sr_runtime() -> SrRuntime:
    """Snapshot the SR env **at call time** (never cached).

    Reading on each call is the contract: the systemd unit exports these once
    for a long-lived process, while tests set them in setUp/tearDown, so a
    cached snapshot would leak values across cases.

    Raises ``RuntimeError`` when the host is configured for a launcher this
    build does not have. A leftover ``SR_EXECUTOR=slurm`` (unit file, drop-in,
    exported shell) must not be quietly reinterpreted as "run it locally":
    the two routes differ in where the job runs and which GPU it gets, so
    guessing would silently change what a submit means. Fail at startup instead
    — ``create_app`` calls this once for exactly that reason.
    """
    raw_executor = (os.environ.get("SR_EXECUTOR") or SR_DEFAULT_EXECUTOR)
    executor = raw_executor.strip().lower()
    if executor not in ("local", ""):
        raise RuntimeError(
            f"SR_EXECUTOR={raw_executor!r} 不被支持：本版本只有本机执行一条路线"
            "（SR 脚本作为本机子进程运行）。请删除该变量的设置或改为 SR_EXECUTOR=local；"
            "本平台不再向任何调度器提交作业")

    def env_float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        try:
            return float(raw) if raw is not None else default
        except ValueError:
            return default

    # SR_WORK_DIR wins, SR_SLURM_WORK_DIR is the legacy alias, then the default.
    work_dir = (os.environ.get("SR_WORK_DIR")
                or os.environ.get(SR_LEGACY_WORK_DIR_ENV)
                or SR_DEFAULT_WORK_DIR)

    return SrRuntime(
        bundle_dir=os.environ.get("SR_BUNDLE_DIR") or SR_DEFAULT_BUNDLE_DIR,
        python=os.environ.get("SR_PYTHON") or SR_DEFAULT_PYTHON,
        work_dir=work_dir,
        queue_poll_sec=env_float("SR_QUEUE_POLL_SEC", SR_DEFAULT_QUEUE_POLL_SEC),
        agent_db=os.environ.get("SR_AGENT_DB") or SR_DEFAULT_DB,
        executor="local",
        locked_dir=os.environ.get("SR_LOCKED_DIR") or None,
        local_gpu=os.environ.get("SR_LOCAL_GPU") or SR_DEFAULT_LOCAL_GPU,
    )
