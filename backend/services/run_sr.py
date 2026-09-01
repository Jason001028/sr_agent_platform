"""run_sr orchestration: assemble an SFSR config.xml and submit it to Slurm.

Wraps SR_code/code_0817_prod.py (production, CentOS7 + Slurm) as an
asynchronous job: the tool returns a job_id and the agent polls it via
sr_job_status. The config XML and the batch script are written under a work
dir, then sbatch-submitted.

Host-specific paths come from env, so the same code runs on the dev machine
(where sbatch is absent → clean error) and the array server:

    SR_BUNDLE_DIR       dir containing code_0817_prod.py (mmsr_bundle/codes)
    SR_PYTHON           python interpreter on the Slurm node (default python)
    SR_SLURM_WORK_DIR   shared work dir for config.xml + batch scripts
    SR_SLURM_PARTITION  optional Slurm partition

Idempotency (§5.3 "必写层，现在就该设计"): submit is keyed on a stable
fingerprint of the params in the store's sr_tasks table. The intent is
recorded *before* sbatch and the job_id *after*, so a crashed-loop replay
resolves the stored job_id through squeue/sacct instead of re-submitting:
an active or COMPLETED job is reused, a failed / forgotten one is re-submitted.
"""
from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from . import slurm
from . import store as store_mod

DEFAULT_BUNDLE_DIR = "/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes"
DEFAULT_WORK_DIR = "/tmp/sr_agent_work"
DEFAULT_OPTIONS_YML = "/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml"


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
                       partition=None, gres=1) -> str:
    """Slurm batch script that cd's into the bundle and runs code_0817_prod.py."""
    python = python or os.environ.get("SR_PYTHON", "python")
    bundle_dir = bundle_dir or os.environ.get("SR_BUNDLE_DIR", DEFAULT_BUNDLE_DIR)
    lines = ["#!/bin/bash",
             f"#SBATCH --gres=gpu:{gres}",
             "#SBATCH --job-name=run_sr",
             f"#SBATCH --output={out_dir}/%j.out",
             f"#SBATCH --error={out_dir}/%j.err"]
    if partition:
        lines.append(f"#SBATCH --partition={partition}")
    lines.append(f"cd {bundle_dir}")
    lines.append(f"{python} code_0817_prod.py -f {config_xml_path}")
    return "\n".join(lines) + "\n"


def task_fingerprint(params: dict) -> str:
    """Stable hash of the submit params; identical params collide, which is
    exactly what the idempotency layer keys on."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_existing(task: dict, run_cmd=None) -> dict:
    """Decide reuse-vs-rerun for a task that already has a stored job_id.

    Raises RuntimeError when the task records an *interrupted* submit (intent
    written, no job_id): the job may or may not have reached Slurm, so the safe
    answer is "outcome unknown" rather than a blind re-submit (deepseek-harness
    repair.ts semantics — never assume the side effect didn't happen).

    Returns reuse dicts for active / COMPLETED jobs (do NOT resubmit); a
    `reuse: False` dict for terminal-failure / unknown jobs (safe to rerun).
    """
    job_id = task["job_id"]
    if job_id is None:
        raise RuntimeError(
            "run_sr: a previous submit for these params was interrupted before "
            "a job id was recorded; the job may or may not have reached Slurm. "
            "Do not blindly re-submit — verify the scheduler (squeue), then retry")
    # run_cmd=None means "module default _run" — pass it through only when set,
    # else it would override the default and crash with "NoneType not callable".
    st = (slurm.job_status(job_id, run_cmd=run_cmd) if run_cmd is not None
          else slurm.job_status(job_id))
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
    work_dir = os.environ.get("SR_SLURM_WORK_DIR", DEFAULT_WORK_DIR)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    stem = f"run_sr_{params.get('suffix') or 'sr'}"
    cfg_path = work / f"{stem}.xml"
    cfg_path.write_text(build_config_xml(params), encoding="utf-8")

    script_path = work / f"{stem}.sh"
    script_path.write_text(build_batch_script(
        cfg_path, work,
        partition=os.environ.get("SR_SLURM_PARTITION")), encoding="utf-8")

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
