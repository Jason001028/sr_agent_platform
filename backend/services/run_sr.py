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
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from . import slurm

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


def submit_run_sr(params: dict, run_cmd=None) -> dict:
    """Assemble config + batch script and sbatch-submit the job.

    Raises RuntimeError (incl. "slurm not available") on any failure. Returns
    {"job_id", "status": "SUBMITTED", "config_xml", "batch_script", "log_dir"}.
    """
    if not slurm.slurm_available():
        raise RuntimeError(
            "slurm not available on this host (sbatch not found) — run_sr "
            "targets the CentOS7 array server; set SR_BUNDLE_DIR/SR_PYTHON there")

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

    job_id = slurm.sbatch_submit(script_path, run_cmd=run_cmd)
    return {"job_id": job_id, "status": "SUBMITTED",
            "config_xml": str(cfg_path), "batch_script": str(script_path),
            "log_dir": str(work)}
