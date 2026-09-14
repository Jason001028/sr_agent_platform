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
    SR_SR_SCRIPT        SR script the job runs (default code_0817_prod.py).
                        Point it at the Slurm deployment variant to run that
                        instead, leaving the production script untouched.
    SR_SANDBOX_ROOT     dir under which each job gets a private copy of its
                        lq_path (see sandbox_scene_paths). Unset → run in place,
                        which writes to lq_path: SR's contract renames the input
                        to `*_NOSR.tif` before writing the result (util.writeTiff).
                        Forced off when SR_EXECUTOR=local (see sandbox_scene_paths).
    SR_EXECUTOR         "slurm" (default, sbatch) or "local" (backend/services/
                        local_exec.py runs the same generated script with bash
                        on this host, $SR_PYTHON, CUDA_VISIBLE_DEVICES=SR_LOCAL_GPU).
                        The generated script is valid either way — `#SBATCH`
                        lines are comments to bash.

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

from . import local_exec
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

#: An SR_SANDBOX_ROOT we are willing to bake into a generated shell script. The
#: sandbox step starts with `rm -rf "<root>/<key>"`, so a value carrying quotes,
#: spaces, `..` or a shell metacharacter would be an injection / traversal hole.
_SAFE_SANDBOX_ROOT = re.compile(r"^/[A-Za-z0-9._/-]+$")

#: Hex chars of the task fingerprint that name a sandbox. Long enough that two
#: tasks never share one, short enough to stay readable in `ls`.
SANDBOX_KEY_LEN = 12

#: A SR_SLURM_NODELIST we are willing to bake into the batch script. This is a
#: Slurm host list (`node81-132`, or the bracketed form `node81-[132-134]`),
#: written verbatim after `#SBATCH --nodelist=`; anything else (spaces, quotes,
#: newlines) would either break the directive or inject a second one. Empty
#: string means "no line". 2026-09-14: the real value is a **single** host — the
#: one 4×3090 machine every job must stay on (docs/status/slurm-acceptance.md §B0).
_SAFE_NODELIST = re.compile(r"^[A-Za-z0-9_,\[\]-]+$")

#: Prefix on the SRLOG's last line for a legal policy skip (cloud cover over
#: <CloudLimit>); the deploy variant writes it so a skip stops looking like a
#: silent failure. See docs/sr_code/sr-slurm-deploy-variant.md §3.2 (E9).
RUN_SKIPPED_PREFIX = "Run skipped:"
RUN_FINISHED_MARKER = "Run finished."


def _posix_basename(path) -> str:
    """Last component of a *disk-array* path, on any host.

    Deliberately not Path(...).name: these are POSIX paths being constructed on
    a Windows dev machine, and the drive/separator rules of the local OS have no
    business touching them.
    """
    return str(path).rstrip("/").rsplit("/", 1)[-1]


def sandbox_scene_paths(lq_path, fingerprint, sandbox_root=None) -> dict | None:
    """Where a job's private copy of `lq_path` lives — or None when off.

    Returns ``{"root", "parent", "scene"}``. `scene` is a copy of the lq_path
    *directory itself*, basename preserved: the SC step derives its input name
    from it (`<basename>.tif`, see util.get_l1_pan_tif_rcsc), so renaming the
    copy would silently change which file the job reads.

    Keyed by the task fingerprint, which is what makes the layering work: the
    backend bakes this path into config.xml *before* sbatch, the job copies into
    it as its first step, and `exit_code_file_for()` later re-derives the very
    same verdict path from that config — so polling finds the result without any
    job-side handshake. A replay of the same submit resolves the same copy.
    """
    rt = sr_runtime()
    # Local execution runs SR *in* the locked directory on purpose: the
    # requirement is "products land next to the input" (plan §1.1), and a
    # sandbox copy would put them under <root>/<key>/ instead. So SR_EXECUTOR=
    # local switches the sandbox off no matter what SR_SANDBOX_ROOT says — one
    # choke point, so the submit path and the queue view (api/platform
    # ._run_dataroot) cannot disagree about where the product went.
    if sandbox_root is None and rt.executor == "local":
        return None
    root = rt.sandbox_root if sandbox_root is None else sandbox_root
    if not root or not str(root).strip():
        return None
    root = str(root).strip().rstrip("/")
    # `..` is spelled with allowed characters but would move the generated
    # `rm -rf "<root>/<key>"` out from under the root it is supposed to clean.
    if not _SAFE_SANDBOX_ROOT.match(root) or ".." in root.split("/"):
        raise ValueError(
            f"SR_SANDBOX_ROOT {root!r} rejected: must be an absolute POSIX path "
            "with no shell metacharacters or `..` segments (the job script is "
            "generated with a rm -rf under it)")
    key = str(fingerprint or "")[:SANDBOX_KEY_LEN]
    if not re.fullmatch(r"[A-Za-z0-9]+", key):
        raise ValueError(f"sandbox key {key!r} rejected: expected a hex digest")
    parent = f"{root}/{key}"
    return {"root": root, "parent": parent,
            "scene": f"{parent}/{_posix_basename(lq_path)}"}


def build_config_xml(params: dict, dataroot=None) -> str:
    """Serialize run_sr params into an <SFSR_Config> XML string.

    DatarootLQ is required. MaskPath is emitted only when given (absent → the
    SR script does full-image SR). GridAlign defaults on; emitted `false` only
    when disabled.

    `dataroot` overrides params["lq_path"] as <DatarootLQ> — the sandbox uses
    this to point the job at its private copy while the task keeps recording the
    path the user actually asked for.
    """
    root = ET.Element("SFSR_Config")

    def add(tag, value):
        if value is not None:
            e = ET.SubElement(root, tag)
            e.text = str(value)

    add("DatarootLQ", dataroot or params.get("lq_path"))
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
                       verify_script=None, sr_script=None,
                       sandbox_src=None, sandbox_parent=None,
                       nodelist=None, job_id=None) -> str:
    """Slurm batch script: audit the allocation, run the SR script, verify it.

    Shape (P2):

      1. #SBATCH directives — gres / time / cpus / job-name / output, plus
         --export=NONE so the *submitting* environment cannot leak into the job.
         ⚠️ --export=NONE is an on-machine must-verify item: it also drops
         LD_LIBRARY_PATH that a real CentOS7 SR env may rely on (the SR env's
         torch + GDAL are built against system libs — node81-135 实测
         torch 1.10.2+cu113 / GDAL 2.4.0，2026-09-14). If the job fails to
         import torch/GDAL, the fallback is a ONE-LINE change here:
         `--export=NONE` → `--export=ALL`. See
         docs/sr_code/sr-slurm-deploy-variant.md §5.1.
      2. An audit preamble echoing SLURM_JOB_ID / SLURM_JOB_GPUS /
         CUDA_VISIBLE_DEVICES / hostname / interpreter / which SR script /
         config, plus the GPU UUIDs nvidia-smi reports for the devices Slurm
         actually handed us. This is the evidence trail for "did --gres really
         give a card" *and* for "did the job run the production script or the
         deployment variant" — both are read off the same .out file.
      3. `cd` into the bundle with an explicit failure exit, then the SR script
         (SR_SR_SCRIPT, default the production one), then the contract verifier,
         whose exit code is passed straight out (0 = contract satisfied, 90 =
         not).

    Optional sandbox step (both args given): the job first copies `sandbox_src`
    into `sandbox_parent`, and the config's DatarootLQ already points at the
    copy. SR is not read-only against its DatarootLQ — util.writeTiff renames the
    input to `*_NOSR.tif` before writing the result — so without this step a
    submit writes to wherever the user pointed it.

    The copy is made unconditionally, never reused. A marker file would save a
    few minutes when the same task is re-run after a failure, but it would also
    mean a re-run silently executes against the *old* copy of a source that has
    since been fixed — the failure that wastes an afternoon instead of five
    minutes.

    `job_id` is for the local executor (backend/services/local_exec.py): the
    verifier names its verdict file after the job id, and with no Slurm there is
    no $SLURM_JOB_ID to fall back on, so the id has to be baked into the text at
    generation time. Under Slurm it stays None and the verifier reads the
    scheduler's id as before.

    Deliberate omissions: no `set -e` (it interacts subtly with `||` and
    pipelines in the middle of a job script), and no CUDA_VISIBLE_DEVICES
    assignment of any kind — GPU selection belongs to Slurm's --gres, and any
    value written here would clobber the allocation (production E2/E4). (The
    local executor sets the variable in the child's *environment* instead, which
    this script never touches — the audit line below only reads it.)
    """
    rt = sr_runtime()
    python = python or rt.python
    bundle_dir = bundle_dir or rt.bundle_dir
    partition = partition if partition is not None else rt.partition
    verify_script = verify_script or os.environ.get("SR_VERIFY_SCRIPT",
                                                    "verify_sr_run.py")
    # Which SR script the job runs (same shape as SR_VERIFY_SCRIPT above).  Both
    # names are resolved HERE, not in the job: the batch script carries
    # --export=NONE, so nothing set in this process survives into it — the name
    # has to be baked into the generated text.  Changing SR_SR_SCRIPT therefore
    # takes a `systemctl restart sr-api`, like every other SR_* env.
    #
    # Why a switch: the deployment variant (SR_code/variants/…) can sit next to
    # the production script instead of being renamed over it, so the production
    # file stays byte-for-byte the source of truth gen_slurm_variant.py derives
    # from, and rollback is one env line.
    sr_script = sr_script or os.environ.get("SR_SR_SCRIPT", "code_0817_prod.py")
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
    # --nodelist: restrict the job to a named set of nodes. Needed on the
    # CentOS7 cluster because the `gpu` partition spans two families and the
    # node104-* one cannot be resolved from the submitting host (a job that
    # lands there cannot be monitored from node81-135). Unset = no line =
    # the scheduler decides, which is what every cluster had before.
    nodelist = str(rt.nodelist if nodelist is None else nodelist).strip()
    if nodelist:
        if not _SAFE_NODELIST.match(nodelist):
            raise ValueError(
                f"SR_SLURM_NODELIST {nodelist!r} rejected: expected a Slurm "
                "host list (letters, digits, `-`, `,`, `[`, `]`); the value is "
                "written verbatim into the generated batch script")
        lines.append(f"#SBATCH --nodelist={nodelist}")
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
        f'echo "SR_SCRIPT={sr_script}"',
        # Which launcher produced this log — "slurm" (sbatch, the default) or
        # "local" (local_exec sets SR_EXECUTOR=local in the child env). Under
        # sbatch the var is absent because of --export=NONE, hence the default.
        'echo "SR_EXECUTOR=${SR_EXECUTOR:-slurm}"',
        f'echo "config={config_xml_path}"',
        # Running without a sandbox means SR writes *into* the scene directory
        # and renames the input to `*_NOSR.tif` on the way (util.writeTiff) —
        # a 4.9 GB file that already exists there gets overwritten. Say it in
        # the log every single time (plan §4.2): the log is what gets read when
        # a run is being explained afterwards.
        *([] if (sandbox_src and sandbox_parent) else [
            'echo "WARNING: no sandbox — SR runs in place: the input is renamed'
            ' to *_NOSR.tif and an existing _NOSR.tif in that directory will be'
            ' overwritten."',
        ]),
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
    ]
    if sandbox_src and sandbox_parent:
        scene = f"{sandbox_parent}/{_posix_basename(sandbox_src)}"
        lines += [
            "",
            "# ---- sandbox: work on a private copy, the source dir stays read-only ----",
            "# SR renames its input to *_NOSR.tif (util.writeTiff), so running",
            "# against the source directory would mutate production data.",
            f'echo "sandbox_src={sandbox_src}"',
            f'echo "sandbox_scene={scene}"',
            f'SBX_SRC="{sandbox_src}"',
            f'SBX_PARENT="{sandbox_parent}"',
            'SBX_SCENE="$SBX_PARENT/$(basename "$SBX_SRC")"',
            'rm -rf "$SBX_PARENT"',
            'mkdir -p "$SBX_PARENT"'
            ' || { echo "sandbox mkdir $SBX_PARENT failed" >&2; exit 1; }',
            'cp -a "$SBX_SRC" "$SBX_PARENT/"'
            ' || { echo "sandbox copy failed: $SBX_SRC" >&2; exit 1; }',
            '# DatarootLQ in the config is SBX_SCENE — refuse to run against a',
            '# half-copied tree, which would look like a scene that SR cannot read.',
            'if [ ! -d "$SBX_SCENE" ]; then',
            '    echo "sandbox copy incomplete: $SBX_SCENE" >&2; exit 1',
            "fi",
        ]
    verify_line = (f'{python} {verify_script} --config {config_xml_path} '
                   '--sr-exit-code "$_sr_rc"')
    if job_id is not None:
        # No scheduler to name the verdict file after (local executor), so the
        # id is written in: verify_sr_run.py prefers --job-id over $SLURM_JOB_ID.
        verify_line += f" --job-id {int(job_id)}"
    lines += [
        "",
        f"cd {bundle_dir} || {{ echo \"cd {bundle_dir} failed\" >&2; exit 1; }}",
        "",
        f"{python} {sr_script} -f {config_xml_path}",
        "_sr_rc=$?",
        "",
        verify_line,
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
    """Scheduler status for a task, pointing it at that task's verdict file.

    A job's *terminal* state lives in `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`
    rather than in sacct (accounting is disabled on the array server). The path
    is derived from the task's own config.xml, so every caller that has the
    task row — submit replay and the queue view — resolves the same file.

    Which scheduler answers depends on SR_EXECUTOR: Slurm (squeue → verdict
    file) or the local executor (in-memory process table → the same verdict
    file). Both return the same dict shape, and in both cases the verdict file
    is what decides COMPLETED vs FAILED.

    `run_cmd=None` means "module default _run"; passing it through explicitly
    would override the default with None and crash. It is a Slurm-only seam.
    """
    exit_file = exit_code_file_for((task or {}).get("config_xml"), job_id)
    if sr_runtime().executor == "local":
        return local_exec.status(job_id, exit_code_file=exit_file)
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
    """Assemble config + batch script and hand the job to the executor.

    Which executor is SR_EXECUTOR: "slurm" sbatch-submits on the cluster,
    "local" (backend/services/local_exec.py) starts the very same script as a
    child process on this host. Everything else — the config XML, the
    fingerprint, the store rows, the verdict-file terminal state — is identical
    on both paths, which is the point of reusing the batch script verbatim.

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
    if sr_runtime().executor == "local":
        # No scheduler involved: the API host runs the job itself. local_exec
        # is always available (it only needs bash); the real precondition is
        # that SR_PYTHON/SR_BUNDLE_DIR exist *on this host*, which the job's own
        # log will report if they do not.
        if not local_exec.available():
            raise RuntimeError("local executor unavailable on this host")
    elif not slurm.slurm_available():
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

    local = sr_runtime().executor == "local"
    lq_path = str(params["lq_path"])
    # None under the local executor — the product must land next to the input
    # (see sandbox_scene_paths).
    sandbox = sandbox_scene_paths(lq_path, fp)
    work_dir = sr_runtime().slurm_work_dir
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    # The fingerprint in the file names, not just the suffix: two tasks that
    # share a suffix but differ in lq_path would otherwise overwrite each
    # other's config, and `exit_code_file_for` reads DatarootLQ back out of that
    # file to find each job's verdict — the second submit would silently point
    # the first task's polling at the wrong scene.
    stem = f"run_sr_{params.get('suffix') or 'sr'}_{fp[:SANDBOX_KEY_LEN]}"
    cfg_path = work / f"{stem}.xml"
    cfg_path.write_text(build_config_xml(params, dataroot=(sandbox or {}).get("scene")),
                        encoding="utf-8")

    # The local executor's id must exist before the script text is written: the
    # verifier is told `--job-id <id>` so it names its verdict file after this
    # job, and exit_code_file_for() re-derives the same name from the same id.
    # (No side effect yet — reserving only bumps the counter on disk.)
    job_id = local_exec.reserve_job_id() if local else None

    script_path = work / f"{stem}.sh"
    script_path.write_text(
        build_batch_script(cfg_path, work,
                           sandbox_src=(lq_path if sandbox else None),
                           sandbox_parent=(sandbox or {}).get("parent"),
                           job_id=job_id),
        encoding="utf-8")

    # checkpoint the intent BEFORE the external side effect: a crash between
    # here and recording job_id leaves a job_id-less task row, and replay
    # answers "outcome unknown" instead of re-submitting (no duplicate).
    store.put_sr_task(fp, params, status="new", job_id=None,
                      config_xml=str(cfg_path), batch_script=str(script_path),
                      log_dir=str(work))

    if local:
        # job_id reserved above → reuse it, do not draw a second one.
        job_id = local_exec.submit(script_path, job_id=job_id)
    else:
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
