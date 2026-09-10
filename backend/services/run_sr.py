"""run_sr orchestration: assemble an SFSR config.xml and submit it to Slurm.

Wraps SR_code/code_0817_prod.py (production, CentOS7 + Slurm) as an
asynchronous job: the tool returns a job_id and the agent polls it via
sr_job_status. The config XML and the batch script are written under a work
dir, then sbatch-submitted.

Host-specific paths come from the environment (see backend/config.sr_runtime,
which owns the defaults), so the same code runs on the dev machine (where
sbatch is absent → clean error) and the array server:

    SR_BUNDLE_DIR       dir containing code_0817_prod.py (mmsr_bundle/codes)
    SR_PYTHON           python interpreter on the Slurm node (default python)
    SR_SLURM_WORK_DIR   shared work dir for config.xml + batch scripts
    SR_SLURM_PARTITION  optional Slurm partition
    SR_SLURM_TIME       sbatch --time (default 02:00:00)
    SR_SLURM_CPUS       sbatch --cpus-per-task (default 4)
    SR_VERIFY_SCRIPT    contract verifier run after the SR script
                        (default verify_sr_run.py, resolved after the cd)

Idempotency (§5.3 "必写层，现在就该设计"): submit is keyed on a stable
fingerprint of the params in the store's sr_tasks table. The intent is
recorded *before* sbatch and the job_id *after*, so a crashed-loop replay
resolves the stored job_id through squeue (active) / the job's exit-code file
(terminal) instead of re-submitting: an active or contract-satisfying job is
reused, a failed / forgotten one is re-submitted.

Terminal-state source (slurm-integration.md §一 "C 方案"): the array server runs
with AccountingStorageType=none, so sacct is permanently unusable. The job
itself records its verdict at `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt` via
SR_code/variants/verify_sr_run.py, and `exit_code_file_for()` below recomputes
that same path here — the naming contract is locked by test_sr_verify.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from backend.config import SR_DEFAULT_BUNDLE_DIR, SR_DEFAULT_OPTIONS_YML
from backend.config import SR_DEFAULT_WORK_DIR, sr_runtime

from . import slurm
from . import store as store_mod

# Re-exported under their historical names (callers import these); the values
# live in backend/config.py so the systemd unit, the batch script and the tests
# all read one set of defaults.
DEFAULT_BUNDLE_DIR = SR_DEFAULT_BUNDLE_DIR
DEFAULT_WORK_DIR = SR_DEFAULT_WORK_DIR
DEFAULT_OPTIONS_YML = SR_DEFAULT_OPTIONS_YML

#: Exit-code file name written by SR_code/variants/verify_sr_run.py. Kept in
#: sync by test_sr_verify.py::TestNamingContract — do not change one alone.
EXIT_FILE_FMT = "_SREXIT_{job_id}.txt"

_JOB_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: Prefix on the SRLOG's last line for a legal policy skip (cloud cover over
#: <CloudLimit>); the deploy variant writes it so a skip stops looking like a
#: silent failure. See docs/sr_code/sr-slurm-deploy-variant.md §3.2 (E9).
RUN_SKIPPED_PREFIX = "Run skipped:"
RUN_FINISHED_MARKER = "Run finished."


def build_config_xml(params: dict) -> str:
    """Serialize run_sr params into an <SFSR_Config> XML string.

    DatarootLQ is required. MaskPath is emitted only when given (absent → the
    SR script does full-image SR). GridAlign defaults on; emitted `false` only
    when disabled.
    """
    root = ET.Element("SFSR_Config")

    def add(tag, value):
        if value is not None:
            e = ET.SubElement(root, tag)
            e.text = str(value)

    add("DatarootLQ", params.get("lq_path"))
    add("GPUIDS", params.get("gpu", 0))
    add("CloudLimit", params.get("cloud_limit", 80))
    add("DeleteOriTifNeeded", "True" if params.get("delete_ori") else "False")
    add("SRScale", params.get("sr_scale", 2))
    add("Suffix", params.get("suffix") or "")
    add("OPT", params.get("options_yml") or DEFAULT_OPTIONS_YML)
    add("MaskPath", params.get("mask_path"))
    if params.get("grid_align") is False:
        add("GridAlign", "false")

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def build_batch_script(config_xml_path, out_dir, python=None, bundle_dir=None,
                       partition=None, gres=1, job_name=None,
                       verify_script=None) -> str:
    """Slurm batch script: audit the allocation, run the SR script, verify it.

    Shape (P2):

      1. #SBATCH directives — gres / time / cpus / job-name / output, plus
         --export=NONE so the *submitting* environment cannot leak into the job.
         ⚠️ --export=NONE is an on-machine must-verify item: it also drops
         LD_LIBRARY_PATH that a real CentOS7 SR env may rely on (torch 1.9.1 +
         cu111 + GDAL are built against system libs). If the job fails to
         import torch/GDAL, the fallback is a ONE-LINE change here:
         `--export=NONE` → `--export=ALL`. See
         docs/sr_code/sr-slurm-deploy-variant.md §5.1.
      2. An audit preamble echoing SLURM_JOB_ID / SLURM_JOB_GPUS /
         CUDA_VISIBLE_DEVICES / hostname / interpreter / config, plus the GPU
         UUIDs nvidia-smi reports for the devices Slurm actually handed us.
         This is the evidence trail for "did --gres really give a card".
      3. `cd` into the bundle with an explicit failure exit, then the SR script,
         then the contract verifier, whose exit code is passed straight out
         (0 = contract satisfied, 90 = not).

    Deliberate omissions: no `set -e` (it interacts subtly with `||` and
    pipelines in the middle of a job script), and no CUDA_VISIBLE_DEVICES
    assignment of any kind — GPU selection belongs to Slurm's --gres, and any
    value written here would clobber the allocation (production E2/E4).
    """
    rt = sr_runtime()
    python = python or rt.python
    bundle_dir = bundle_dir or rt.bundle_dir
    partition = partition if partition is not None else rt.partition
    verify_script = verify_script or os.environ.get("SR_VERIFY_SCRIPT",
                                                    "verify_sr_run.py")
    job_name = _sanitize_job_name(job_name or Path(str(config_xml_path)).stem)

    lines = ["#!/bin/bash",
             f"#SBATCH --gres=gpu:{gres}",
             f"#SBATCH --job-name={job_name}",
             f"#SBATCH --output={out_dir}/%j.out",
             f"#SBATCH --error={out_dir}/%j.err",
             f"#SBATCH --time={rt.time}",
             f"#SBATCH --cpus-per-task={rt.cpus}",
             "#SBATCH --export=NONE"]
    if partition:
        lines.append(f"#SBATCH --partition={partition}")
    if rt.mem:
        lines.append(f"#SBATCH --mem={rt.mem}")
    lines += [
        "#",
        "# Generated by backend/services/run_sr.build_batch_script — do not edit",
        "# in place. GPU selection is Slurm's job (--gres): this script never",
        "# assigns CUDA_VISIBLE_DEVICES, it only reports what Slurm injected.",
        "# Contract verifier exit codes: 0 = satisfied, 90 = not satisfied.",
        "export PYTHONUNBUFFERED=1",
        "",
        "# ---- audit preamble: what did Slurm actually give this job? ----",
        'echo "=== sr job audit ==="',
        'echo "SLURM_JOB_ID=${SLURM_JOB_ID}"',
        'echo "SLURM_JOB_GPUS=${SLURM_JOB_GPUS}"',
        'echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"',
        'echo "hostname=$(hostname)"',
        f'echo "SR_PYTHON={python}"',
        f'echo "config={config_xml_path}"',
        'echo "=== gpu index,uuid (filtered by CUDA_VISIBLE_DEVICES) ==="',
        "if command -v nvidia-smi >/dev/null 2>&1; then",
        "    nvidia-smi --query-gpu=index,uuid --format=csv,noheader 2>/dev/null"
        " | while IFS= read -r _gpu; do",
        '        _idx="${_gpu%%,*}"',
        '        _uuid="${_gpu#*, }"',
        '        case ",${CUDA_VISIBLE_DEVICES}," in',
        '            *",${_idx},"*) echo "gpu ${_gpu}" ;;',
        '            *",${_uuid},"*) echo "gpu ${_gpu}" ;;',
        "        esac",
        "    done",
        "else",
        '    echo "nvidia-smi not found"',
        "fi",
        "",
        f"cd {bundle_dir} || {{ echo \"cd {bundle_dir} failed\" >&2; exit 1; }}",
        "",
        f"{python} code_0817_prod.py -f {config_xml_path}",
        "_sr_rc=$?",
        "",
        f'{python} {verify_script} --config {config_xml_path} --sr-exit-code "$_sr_rc"',
        "exit $?",
    ]
    return "\n".join(lines) + "\n"


def _sanitize_job_name(name: str) -> str:
    """Slurm-safe --job-name: no whitespace/slashes, length-capped."""
    cleaned = _JOB_NAME_SAFE.sub("_", str(name or "").strip()).strip("_")
    return (cleaned or "run_sr")[:64]


def read_dataroot_lq(config_xml_path) -> str | None:
    """<DatarootLQ> from a platform-written config.xml, or None.

    Used to locate the per-job verdict file (`exit_code_file_for`). Never
    raises — a config that cannot be read simply has no verdict file, which the
    caller reports as UNKNOWN rather than as an error.
    """
    try:
        root = ET.parse(str(config_xml_path)).getroot()
    except Exception:
        return None
    node = root.find("DatarootLQ")
    if node is None or not (node.text or "").strip():
        return None
    return node.text.strip()


def exit_code_file_for(config_xml_path, job_id) -> str | None:
    """Path of the verdict file the job writes, or None when not derivable.

    Same convention as SR_code/variants/verify_sr_run.py (EXIT_FILE_FMT): the
    file sits in the scene's Debug/ dir, next to the SRLOG, and is keyed by job
    id so two submits of the same scene cannot read each other's verdict.
    """
    lq_path = read_dataroot_lq(config_xml_path)
    if not lq_path or job_id in (None, ""):
        return None
    try:
        job_id = int(job_id)
    except (TypeError, ValueError):
        return None
    return str(Path(lq_path) / "Debug" / EXIT_FILE_FMT.format(job_id=job_id))


def task_fingerprint(params: dict) -> str:
    """Stable hash of the submit params; identical params collide, which is
    exactly what the idempotency layer keys on."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def query_job_status(job_id, task: dict | None = None, run_cmd=None) -> dict:
    """slurm.job_status for a task, pointing it at that task's verdict file.

    A job's *terminal* state lives in `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`
    rather than in sacct (accounting is disabled on the array server). The path
    is derived from the task's own config.xml, so every caller that has the
    task row — submit replay and the queue view — resolves the same file.

    `run_cmd=None` means "module default _run"; passing it through explicitly
    would override the default with None and crash.
    """
    exit_file = exit_code_file_for((task or {}).get("config_xml"), job_id)
    if run_cmd is not None:
        return slurm.job_status(job_id, run_cmd=run_cmd, exit_code_file=exit_file)
    return slurm.job_status(job_id, exit_code_file=exit_file)


def _resolve_existing(task: dict, run_cmd=None) -> dict:
    """Decide reuse-vs-rerun for a task that already has a stored job_id.

    Raises RuntimeError when the task records an *interrupted* submit (intent
    written, no job_id): the job may or may not have reached Slurm, so the safe
    answer is "outcome unknown" rather than a blind re-submit (deepseek-harness
    repair.ts semantics — never assume the side effect didn't happen).

    Returns reuse dicts for active / contract-satisfying jobs (do NOT
    resubmit); a `reuse: False` dict for terminal-failure / unknown jobs (safe
    to rerun). Only a verdict file saying "0 exit, contract satisfied" counts
    as COMPLETED — a job that exited 0 without producing SRLOG + tif is the
    silent-failure case this whole layer exists to stop caching (see the module
    docstring and slurm-integration.md §2.3).
    """
    job_id = task["job_id"]
    if job_id is None:
        raise RuntimeError(
            "run_sr: a previous submit for these params was interrupted before "
            "a job id was recorded; the job may or may not have reached Slurm. "
            "Do not blindly re-submit — verify the scheduler (squeue), then retry")
    st = query_job_status(job_id, task=task, run_cmd=run_cmd)
    base = {"job_id": job_id, "config_xml": task["config_xml"],
            "batch_script": task["batch_script"], "log_dir": task["log_dir"]}
    if st["active"]:
        return {"reuse": True, "status": "RESUMED_ACTIVE",
                "state": st["state"], "idempotent": True, **base}
    if st["state"] == "COMPLETED":
        return {"reuse": True, "status": "RESUMED_COMPLETED",
                "state": st["state"], "exit_code": st["exit_code"],
                "idempotent": True, **base}
    # terminal failure (FAILED/CANCELLED/TIMEOUT/...) or no scheduler record
    # (UNKNOWN) → the prior job is not worth reusing; fall through to rerun.
    return {"reuse": False, "previous_state": st["state"],
            "previous_exit_code": st["exit_code"], "previous_job_id": job_id}


def submit_run_sr(params: dict, run_cmd=None, store=None) -> dict:
    """Assemble config + batch script and sbatch-submit the job.

    Idempotent via the store's sr_tasks table (default store unless one is
    passed): before submitting, the task table is consulted and a stored
    job_id is resolved through squeue/sacct — an active or COMPLETED job is
    returned as-is (RESUMED_ACTIVE / RESUMED_COMPLETED, no new submit), so a
    crashed-loop replay cannot double-submit the same Slurm job.

    Raises RuntimeError (incl. "slurm not available") on any failure. Returns
    {"job_id", "status", "config_xml", "batch_script", "log_dir"}, where status
    is SUBMITTED for a fresh submission (possibly with `previous_state` when
    it re-submitted after a failed job) or a RESUMED_* reuse marker.
    """
    if not slurm.slurm_available():
        raise RuntimeError(
            "slurm not available on this host (sbatch not found) — run_sr "
            "targets the CentOS7 array server; set SR_BUNDLE_DIR/SR_PYTHON there")

    store = store or store_mod.default_store()
    fp = task_fingerprint(params)

    # --- idempotency check BEFORE any side effect -------------------------
    previous = None
    task = store.get_sr_task(fp)
    if task is not None:
        resolved = _resolve_existing(task, run_cmd=run_cmd)
        if resolved.get("reuse"):
            return resolved
        previous = resolved  # rerun: carry the prior failure info

    lq_path = str(params["lq_path"])
    work_dir = sr_runtime().slurm_work_dir
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    stem = f"run_sr_{params.get('suffix') or 'sr'}"
    cfg_path = work / f"{stem}.xml"
    cfg_path.write_text(build_config_xml(params), encoding="utf-8")

    script_path = work / f"{stem}.sh"
    script_path.write_text(build_batch_script(cfg_path, work),
                           encoding="utf-8")

    # checkpoint the intent BEFORE the external side effect: a crash between
    # here and recording job_id leaves a job_id-less task row, and replay
    # answers "outcome unknown" instead of re-submitting (no duplicate).
    store.put_sr_task(fp, params, status="new", job_id=None,
                      config_xml=str(cfg_path), batch_script=str(script_path),
                      log_dir=str(work))

    # run_cmd=None → module default _run (see _resolve_existing).
    job_id = (slurm.sbatch_submit(script_path, run_cmd=run_cmd)
              if run_cmd is not None else slurm.sbatch_submit(script_path))
    store.update_sr_task_job(fp, job_id=job_id, status="submitted",
                             config_xml=str(cfg_path),
                             batch_script=str(script_path), log_dir=str(work))

    out = {"job_id": job_id, "status": "SUBMITTED",
           "config_xml": str(cfg_path), "batch_script": str(script_path),
           "log_dir": str(work), "idempotent": False}
    if previous:
        out.update({"previous_state": previous["previous_state"],
                    "previous_exit_code": previous["previous_exit_code"],
                    "previous_job_id": previous["previous_job_id"]})
    return out
