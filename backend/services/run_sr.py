"""run_sr: the task service — validate, prepare, submit, query, cancel.

This module is the single entry point for an SR job. REST
(`api/platform.py`), the Agent tool (`tools/run_sr.py`) and the status tool all
go through it, so all three agree on param normalization, mask derivation, the
suffix rule, the fingerprint, the input-prep plan and the submit logic.

    nosr_swap_plan()   which copy this run reads, where the product lands
    build_config_xml() the <SFSR_Config> the SR script reads
    build_run_script() the bash script that wraps prep → SR → verify
    task_fingerprint() the idempotency key
    query_job_status() one task's state, from its own verdict file
    submit_run_sr()    assemble the above and hand the job to the executor

Execution is local and only local: `backend/services/local_exec.py` starts
`build_run_script`'s output as a child process on this host with `SR_PYTHON`
(the SR production conda interpreter — a different world from the API's own
venv). There is no scheduler in the path.

    SR_BUNDLE_DIR       dir containing the SR script (mmsr_bundle/codes)
    SR_PYTHON           interpreter that runs it
    SR_WORK_DIR         dir for config.xml + run script + logs (SR_SLURM_WORK_DIR
                        is read as the legacy spelling)
    SR_SR_SCRIPT        SR script the job runs (default code_0817_prod.py)
    SR_VERIFY_SCRIPT    contract verifier run after it (default verify_sr_run.py)
    SR_PREP_SCRIPT      scene-prep script, only on a run that resolved a swap
                        (default sr_prep_scene.py)
    SR_LOCAL_GPU        CUDA_VISIBLE_DEVICES handed to the child

Idempotency: submit is keyed on a stable fingerprint of the params in the
store's sr_tasks table. The intent row is written *before* the launch and the
job_id *after*, so a crashed replay resolves the stored job_id through the
executor (active → reused, verdict file says contract satisfied → reused)
instead of starting a second process on the same scene.

Terminal state: the job writes its own verdict to
`<DatarootLQ>/Debug/_SREXIT_<job_id>.txt` (SR_code/variants/verify_sr_run.py),
and `exit_code_file_for()` recomputes that same path here — the naming contract
is pinned by test_sr_verify.py. The verdict is what decides COMPLETED vs
FAILED; the child's exit code does not (silent-failure paths exit 0).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path

from backend.config import SR_DEFAULT_BUNDLE_DIR, SR_DEFAULT_OPTIONS_YML
from backend.config import SR_DEFAULT_SUFFIX, SR_DEFAULT_WORK_DIR, sr_runtime

from . import local_exec
from . import scene_search
from . import store as store_mod

# Re-exported under their historical names (callers import these); the values
# live in backend/config.py so the systemd unit, the run script and the tests
# all read one set of defaults.
DEFAULT_BUNDLE_DIR = SR_DEFAULT_BUNDLE_DIR
DEFAULT_WORK_DIR = SR_DEFAULT_WORK_DIR
DEFAULT_OPTIONS_YML = SR_DEFAULT_OPTIONS_YML
DEFAULT_SUFFIX = SR_DEFAULT_SUFFIX

#: Exit-code file name written by SR_code/variants/verify_sr_run.py. Kept in
#: sync by test_sr_verify.py::TestNamingContract — do not change one alone.
EXIT_FILE_FMT = "_SREXIT_{job_id}.txt"

_JOB_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: Hex chars of the task fingerprint that go into a run's file names. Long
#: enough that two tasks never share one, short enough to stay readable in `ls`.
RUN_KEY_LEN = 12

#: Prefix on the SRLOG's last line for a legal policy skip (cloud cover over
#: <CloudLimit>); the platform variant writes it so a skip stops looking like a
#: silent failure. See docs/sr_code/sr-platform-variant.md §3.2 (E9).
RUN_SKIPPED_PREFIX = "Run skipped:"
RUN_FINISHED_MARKER = "Run finished."

#: The SR team's own sfsr config inside SR_BUNDLE_DIR, read for the default
#: <Suffix> (see default_suffix). TWO spellings because the file on disk and the
#: name the interface doc records disagree: SR_code/ holds
#: `sfsr_confgig_test_espan2_cuda1.xml` (the SR team's own typo), while
#: docs/sr_code/sr-pipeline-interface.md §5 records `sfsr_config_...` as the real
#: one (已核对). Which spelling a given deployment has is unverified, so
#: both are probed; flip the order here once someone checks the array machine.
BUNDLE_SUFFIX_CONFIG_NAMES = (
    "sfsr_confgig_test_espan2_cuda1.xml",
    "sfsr_config_test_espan2_cuda1.xml",
)

#: <Suffix> is spliced into the output file name (`<stem>_<suffix>.tif`), so a
#: separator or shell character is a writable-path hole, and an empty one makes
#: the output name equal the input name — which SR turns into a rename of the
#: input (sr-pipeline-interface.md §2.4-1). One alphabet, checked in exactly one
#: place (normalize_suffix) so both submit entries agree.
SUFFIX_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
SUFFIX_RULE = "只允许字母/数字/下划线/短横，长度 1..16"


class SubmitConflict(RuntimeError):
    """A previous submit for these params cannot be replayed or safely re-run.

    Raised instead of guessing. ``kind`` says which case it is:

    * ``"interrupted"`` — the intent row exists with no job_id: nothing was
      launched (the launch is what records the id), but the platform will not
      decide that on the operator's behalf in a replay.
    * ``"unknown"`` — a job_id exists and its outcome cannot be established:
      neither a process this host can see nor a verdict file. Re-running here
      is exactly how two SR processes end up writing one scene directory.
    """

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


def _posix_basename(path) -> str:
    """Last component of a *disk-array* path, on any host.

    Deliberately not Path(...).name: these are POSIX paths being constructed on
    a Windows dev machine, and the drive/separator rules of the local OS have no
    business touching them.
    """
    return str(path).rstrip("/").rsplit("/", 1)[-1]


def _shq(value) -> str:
    """One shell word for a path embedded in the generated run script.

    Two jobs at once: backslashes become forward slashes (the script is run by
    bash, and MSYS bash on a Windows dev machine handles `C:/x/y` but not
    `C:\\x\\y`), and the result is quoted so a space or a `$` in a configured
    path cannot split the command or expand into something else.
    """
    return shlex.quote(str(value).replace("\\", "/"))


#: delete_ori 在原型期禁用，两个提交入口（REST 的 api/platform.py 与 agent 工具
#: tools/run_sr.py）共用这一句话。原因：SR 侧（SR_code/util.py 的 writeTiff）在 True
#: 时删/覆盖的是**输出路径上的同名文件**、且不留备份 —— 小于 2GB 走 ``os.remove``，
#: 大于 2GB 由 ``driver.Create`` 直接覆盖。输出名 == 输入名时（``<Suffix>`` 为空）
#: 被删的就是原图；``Suffix`` 非空时被删的是上一次的同名输出。作业就在场景目录里
#: 就地跑，两种情形都不可恢复。空 ``Suffix`` 现在两个入口都到不了（normalize_suffix
#: 会补上默认值），但仍留着这条说明：它是 SR 侧的真实语义，也解释了为什么默认后缀
#: 必须非空。
DELETE_ORI_MSG = (
    "delete_ori 已禁用：SR 不删源图而是删/覆盖输出路径上的同名文件、不留备份"
    "（<Suffix> 为空时输出名 == 输入名，被删的就是原图），"
    "而作业就在场景目录里就地跑、输出路径就在那里，一旦执行不可恢复。"
    "如需删除原图，请在作业验收通过后手工处理")


def _reject_delete_ori(params: dict) -> None:
    """Reject a submit that asks for delete_ori — see DELETE_ORI_MSG."""
    if params.get("delete_ori"):
        raise ValueError(DELETE_ORI_MSG)


def nosr_swap_plan(lq_path, mask_path, suffix) -> dict:
    """这一跑读哪份、跑完落到哪个名字、两个临时件放哪（五条绝对路径）。

    平台的超分提交只把**场景目录**交给 SR，输入文件由 SR 脚本自己定位（RC 场景硬编码
    `PAN.tif`，见 util.get_l1_pan_tif_rcsc）。而真机上 `PAN.tif` 是**已经超过分**的那份
    本体：严格判据 `1.0 < size < 1.8 GiB` 只放行超分后的尺寸，未超分的那份一进去就被判成
    「已超分」直接退出。所以这一跑要先把**未超分那一份**拷到本体名上，跑完再把产物落回
    本体名 —— 本函数算的就是这套位置。

    两个消费方各取所需，同一份计划不各推一遍：

      * `source` / `backup` / `mask` / `mask_1x` —— 跑前脚本 sr_prep_scene.py；
      * `body` / `backup` / `mask_1x` —— 跑后契约校验的归位段（verify_sr_run.py）。

    本体与来源那一份都按 `scene_search` 的既有推导取（场景判定只有那一处）。来源取
    **输入 stem** 那几条候选里第一个存在的（`<输入 stem>_NOSR`，RC 场景即
    `PAN_NOSR.tif`）；**不传 `product_path`**：`<产物 stem>_NOSR` 是上一次同一 suffix 的
    超分结果，语义不同，不能当选源头。

    五条路径都在**真实场景目录**里：作业就地跑，产物落在输入旁边。来源那一份的存在性
    也按同一个目录判。两个临时件落在 `<场景>/Debug/`：`Debug/` 本来就是这个链路的临时
    区，且扩展名/主干都过不了 `is_scene_file` 的白名单，不会污染场景列表。

    `mask_path` 是操作员那份掩码（`<输入影像 stem>_mask.tif`），`mask_1x` 是它降到源头
    尺寸之后的临时件 —— 配置 XML 的 `<MaskPath>` 指向后者，前者的路径仍进 params 与
    `task_fingerprint`（指纹语义不变：同一次提交还是同一次提交）。

    缺本体、缺未超分那一份、缺掩码一律 `ValueError` 拦下，不拿别的文件顶替。
    """
    if not str(suffix or "").strip():
        # 空 suffix 时输出名 == 输入名，SR 的 writeTiff 会把输入改名为 _ori 后就地覆盖；
        # 两个入口的 normalize_suffix 都保证非空，这里是这一条链自己的兜底。
        raise ValueError("超分提交需要一个非空 suffix（输出名等于输入名时 SR 会就地覆盖输入）")
    scene = str(lq_path).replace("\\", "/").rstrip("/")
    inp = scene_search.input_scene_path(scene)
    if inp is None:
        cands = "、".join(c.name for c in scene_search.input_candidates(scene))
        raise ValueError(f"不是合法场景目录：{scene}"
                         f"（需含 <目录名>_meta.xml，且含 {cands} 之一）")
    candidates = scene_search.nosr_candidates(scene, inp)
    source = next((c for c in candidates if c.is_file()), None)
    if source is None:
        tried = "、".join(c.name for c in candidates)
        raise ValueError(f"场景里没有未超分那一份（试过 {tried}）："
                         "这一跑要读的就是它，缺了不拿别的顶替")
    mask = str(mask_path or "").replace("\\", "/")
    if not mask or not Path(mask).is_file():
        raise ValueError(f"掩码文件不存在：{mask or '<未给>'}")

    mask_name = _posix_basename(mask)
    return {
        "body": f"{scene}/{inp.name}",
        "source": f"{scene}/{source.name}",
        "backup": f"{scene}/Debug/{inp.name}.srbak",
        "mask": mask,
        "mask_1x": f"{scene}/Debug/{mask_name}.sr1x",
    }


def build_config_xml(params: dict, mask_path=None) -> str:
    """Serialize run_sr params into an <SFSR_Config> XML string.

    DatarootLQ is required, and it is the scene directory itself: the job runs
    in place, so the product lands next to the input. MaskPath is emitted only
    when given (absent → the SR script does full-image SR). GridAlign defaults
    on; emitted `false` only when disabled. DeleteOriTifNeeded is always emitted
    as False (see DELETE_ORI_MSG) so the generated config keeps its shape.

    `mask_path` overrides params["mask_path"] as <MaskPath> — the submit entries
    point the job at the mask resampled to the source's pixel size
    (nosr_swap_plan) while params keeps recording the operator's own mask path,
    which is what the fingerprint is taken over.
    """
    root = ET.Element("SFSR_Config")

    def add(tag, value):
        if value is not None:
            e = ET.SubElement(root, tag)
            e.text = str(value)

    add("DatarootLQ", params.get("lq_path"))
    add("GPUIDS", params.get("gpu", 0))
    add("CloudLimit", params.get("cloud_limit", 80))
    # Always False — delete_ori is disabled (DELETE_ORI_MSG). Emitted anyway so
    # the generated config.xml keeps the tag the SR side reads.
    add("DeleteOriTifNeeded", "False")
    add("SRScale", params.get("sr_scale", 2))
    add("Suffix", params.get("suffix") or "")
    add("OPT", params.get("options_yml") or DEFAULT_OPTIONS_YML)
    add("MaskPath", params.get("mask_path") if mask_path is None else mask_path)
    if params.get("grid_align") is False:
        add("GridAlign", "false")

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def run_stem(suffix, fingerprint, job_id) -> str:
    """File-name stem for one run: suffix + fingerprint + job id.

    All three, not one: the suffix alone collides across scenes, the fingerprint
    alone collides across retries of the same task, and `exit_code_file_for`
    reads `<DatarootLQ>` back out of the config file to find that run's verdict
    file — so a retry must never overwrite the config of a run still in flight.
    """
    return (f"run_sr_{suffix or DEFAULT_SUFFIX}_"
            f"{str(fingerprint or '')[:RUN_KEY_LEN]}_{int(job_id)}")


def build_run_script(config_xml_path, python=None, bundle_dir=None, job_id=None,
                     verify_script=None, sr_script=None, prep_script=None,
                     swap=None) -> str:
    """The bash script a run executes: cd, prep, SR, verify.

    Shape:

      1. An audit preamble echoing the job id, CUDA_VISIBLE_DEVICES, hostname,
         interpreter and which SR script / config this is, plus the GPU UUIDs
         nvidia-smi reports for the devices CUDA_VISIBLE_DEVICES selects. This
         is the evidence trail for "which card did it get" and "did it run the
         production script or the deployment variant" — both are read back off
         the same log file.
      2. `cd` into the bundle with an explicit failure exit, then the SR script
         (SR_SR_SCRIPT, default the production one), then the contract verifier,
         whose exit code is passed straight out (0 = contract satisfied, 90 =
         not).

    `swap` (a nosr_swap_plan dict) is the "this run reads the un-superresolved
    copy" wiring, and is absent on a submit that did not resolve one:

      * before the SR script, `prep_script` (SR_PREP_SCRIPT, default
        sr_prep_scene.py) lands that copy on the body name and resamples the
        mask to its pixel size. A prep failure does NOT abort the run — it is
        carried into `_sr_rc` and the SR step is skipped, so the verifier still
        runs and still writes its verdict (and restores the body from the
        backup). Exiting here instead would leave no verdict file at all, which
        the platform reads as UNKNOWN and can never clear.
      * the verifier is additionally asked to move the product onto the body
        name afterwards (`--land-on-body` / `--backup` / `--mask-1x`). That
        landing lives in verify_sr_run.py rather than here because the terminal
        state the platform reads is the verdict file, not this script's exit
        code: a landing that failed in bash could only print.

    `job_id` is required and is baked into the text: the verifier names its
    verdict file after the run number, and with no scheduler there is no
    environment variable to read it from. It is also what makes this run's log
    and verdict path distinct from a retry's.

    Deliberate omissions: no `set -e` (it interacts subtly with `||` and
    pipelines in the middle of a job script), and no CUDA_VISIBLE_DEVICES
    assignment — the card is chosen by the deployment (SR_LOCAL_GPU via
    backend/config.sr_runtime) and set in the child's *environment* by
    local_exec; the audit line below only reads it.

    Both script names are resolved HERE, not inside the job: they are baked into
    the generated text, so changing SR_SR_SCRIPT / SR_VERIFY_SCRIPT /
    SR_PREP_SCRIPT takes effect on the next submit.
    """
    rt = sr_runtime()
    python = python or rt.python
    bundle_dir = bundle_dir or rt.bundle_dir
    verify_script = verify_script or os.environ.get("SR_VERIFY_SCRIPT",
                                                    "verify_sr_run.py")
    prep_script = prep_script or os.environ.get("SR_PREP_SCRIPT",
                                                "sr_prep_scene.py")
    # Which SR script the run uses (same shape as SR_VERIFY_SCRIPT above): the
    # deployment variant (SR_code/variants/…) can sit next to the production
    # script instead of being renamed over it, so the production file stays
    # byte-for-byte the source of truth gen_platform_variant.py derives from,
    # and switching back is one env line.
    sr_script = sr_script or os.environ.get("SR_SR_SCRIPT", "code_0817_prod.py")
    if job_id is None:
        raise ValueError("build_run_script 需要 job_id：校验器用它命名判定文件，"
                         "而本机执行没有调度器环境变量可读")

    lines = [
        "#!/bin/bash",
        "#",
        "# Generated by backend/services/run_sr.build_run_script — do not edit in",
        "# place. Run on this host by backend/services/local_exec.py, which starts",
        "# it with bash, sets CUDA_VISIBLE_DEVICES from SR_LOCAL_GPU and captures",
        "# stdout/stderr into this run's log file.",
        "# Contract verifier exit codes: 0 = satisfied, 90 = not satisfied.",
        "export PYTHONUNBUFFERED=1",
        "",
        "# ---- audit preamble: what did this host actually give the run? ----",
        'echo "=== sr job audit ==="',
        f"echo {_shq('JOB_ID=' + str(int(job_id)))}",
        'echo "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"',
        'echo "hostname=$(hostname)"',
        f"echo {_shq('SR_PYTHON=' + str(python))}",
        f"echo {_shq('SR_SCRIPT=' + str(sr_script))}",
        f"echo {_shq('config=' + str(config_xml_path))}",
        # SR runs *in* the scene directory: the result tif lands next to the
        # input, and an existing file at that output path is renamed to
        # `*_NOSR.tif` before being overwritten (util.writeTiff). Say it in the
        # log every single time: the log is what gets read when a run is being
        # explained afterwards.
        'echo "IN PLACE: SR writes <input>_<suffix>.tif into the scene'
        " directory; the input tif is not renamed or deleted; an existing file"
        ' at that output path is renamed to <output>_NOSR.tif before being'
        ' overwritten."',
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
        f"cd {_shq(bundle_dir)} || "
        f"{{ echo {_shq('cd failed: ' + str(bundle_dir))} >&2; exit 1; }}",
        "",
    ]
    # No scheduler to name the verdict file after, so the run number is written
    # in: verify_sr_run.py takes it from --job-id.
    verify_line = (f"{_shq(python)} {_shq(verify_script)} "
                   f"--config {_shq(config_xml_path)} "
                   f'--sr-exit-code "$_sr_rc"')
    if swap:
        verify_line += (f' --land-on-body {_shq(swap["body"])}'
                        f' --backup {_shq(swap["backup"])}'
                        f' --mask-1x {_shq(swap["mask_1x"])}')
    verify_line += f" --job-id {int(job_id)}"
    if swap:
        lines += [
            "# ---- prep: this run reads the un-superresolved copy ----",
            "# A prep failure is carried into _sr_rc instead of exiting: the",
            "# verifier still has to run and write its verdict (it restores the",
            "# body from the backup), otherwise the platform reads UNKNOWN.",
            "_prep_rc=0",
            f"{_shq(python)} {_shq(prep_script)} --body {_shq(swap['body'])}"
            f" --nosr {_shq(swap['source'])} --backup {_shq(swap['backup'])}"
            f" --mask {_shq(swap['mask'])} --mask-1x {_shq(swap['mask_1x'])}"
            " || _prep_rc=$?",
            'if [ "$_prep_rc" -ne 0 ]; then',
            '    echo "sr_prep_scene failed (exit $_prep_rc);'
            ' skipping the SR run, the verifier restores the body" >&2',
            '    _sr_rc=$_prep_rc',
            "else",
            f"    {_shq(python)} {_shq(sr_script)} -f {_shq(config_xml_path)}",
            "    _sr_rc=$?",
            "fi",
            "",
            verify_line,
            "_v_rc=$?",
            "exit $_v_rc",
        ]
    else:
        lines += [
            f"{_shq(python)} {_shq(sr_script)} -f {_shq(config_xml_path)}",
            "_sr_rc=$?",
            "",
            verify_line,
            "_v_rc=$?",
            "exit $_v_rc",
        ]
    return "\n".join(lines) + "\n"


def read_dataroot_lq(config_xml_path) -> str | None:
    """<DatarootLQ> from a platform-written config.xml, or None.

    Used to locate the per-run verdict file (`exit_code_file_for`). Never
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


def read_bundle_suffix(bundle_dir=None, config_name=None) -> str | None:
    """`<Suffix>` out of the SR team's own sfsr config, or None.

    That file sits next to the SR code (`SR_BUNDLE_DIR`, i.e. beside
    `code_0817_prod.py`) and is the SR team's own hand-maintained sample — the
    platform reads it so its default output suffix matches what the production
    runs use (`<Suffix>260318</Suffix>` in the copy at SR_code/). ONLY <Suffix>
    is taken from it: its <DatarootLQ>/<MaskPath> are scene-specific values of
    someone's test run, not platform inputs.

    Two candidate names, probed in order, because the on-disk spelling and the
    spelling the interface doc records disagree (see BUNDLE_SUFFIX_CONFIG_NAMES).
    A candidate that cannot be parsed is skipped; one that parses but carries no
    non-empty <Suffix> does not shadow the next. Never raises — same contract as
    read_dataroot_lq: "no opinion" is always a legitimate answer, and the caller
    falls back rather than failing a submit.
    """
    base = str(bundle_dir if bundle_dir is not None else sr_runtime().bundle_dir)
    names = (config_name,) if config_name else BUNDLE_SUFFIX_CONFIG_NAMES
    for name in names:
        try:
            root = ET.parse(os.path.join(base, name)).getroot()
        except Exception:                  # missing / unreadable / malformed
            continue
        node = root.find("Suffix")
        value = (node.text or "").strip() if node is not None else ""
        if value:
            return value
    return None


def default_suffix() -> str:
    """The <Suffix> a submit that carries none gets.

    The file's value wins; anything unusable — file absent, unparseable, or a
    value that fails SUFFIX_RE — falls back to DEFAULT_SUFFIX. A bad value is
    DISCARDED rather than sanitised: it gets spliced into an output file name,
    so a separator or shell character must never reach the pipeline.

    Deliberately NOT cached, for the same reason sr_runtime() is not: the value
    feeds task_fingerprint, so a cached one would make the fingerprint a
    function of process start time — a restart (or a second worker) would then
    disagree about the same logical submit and produce a SECOND run writing the
    same output path, which is exactly what the idempotency layer exists to
    prevent. The visible consequence of reading live is that editing <Suffix>
    between two submits makes the second one a new task; that is correct (it
    names different output files) and shows up on the queue row.
    """
    value = read_bundle_suffix()
    return value if value and SUFFIX_RE.match(value) else DEFAULT_SUFFIX


def normalize_suffix(value) -> str:
    """explicit (stripped) → file <Suffix> → DEFAULT_SUFFIX; raises ValueError.

    The single choke point both submit entries share (api/platform.py and
    tools/run_sr.py), so an identical logical submit from REST and from the
    agent tool normalizes to the same params dict — and therefore to the same
    task_fingerprint. Both entries must default and whitelist identically here,
    otherwise an identical submit produces a duplicate job rather than a reuse.
    """
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return default_suffix()
    if not SUFFIX_RE.match(raw):
        raise ValueError(f"suffix 非法：{raw!r}（{SUFFIX_RULE}）")
    return raw


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


def query_job_status(job_id, task: dict | None = None) -> dict:
    """State of one run, pointing the executor at that task's verdict file.

    A run's *terminal* state lives in `<DatarootLQ>/Debug/_SREXIT_<job_id>.txt`.
    The path is derived from the task's own config.xml, so every caller that has
    the task row — submit replay, the queue view, the status tool — resolves the
    same file.

    All three entries land here: this is the one query implementation. An id
    nothing knows about comes back as UNKNOWN from local_exec (`active` is None
    there, not False) — it never creates anything and never starts a process.
    """
    exit_file = exit_code_file_for((task or {}).get("config_xml"), job_id)
    return local_exec.status(job_id, exit_code_file=exit_file)


def _resolve_existing(task: dict) -> dict:
    """Decide reuse-vs-rerun for a task that already has a stored job_id.

    Reuse dicts for active / contract-satisfying runs (do NOT resubmit); a
    `reuse: False` dict for a run that is confirmed over and produced no usable
    result (safe to rerun). Only a verdict file saying "contract satisfied"
    counts as COMPLETED — a run that exited 0 without producing SRLOG + tif is
    the silent-failure case this whole layer exists to stop caching.

    Two cases raise `SubmitConflict` instead of answering, because both of them
    mean "we do not know what is on that scene directory right now":

    * intent recorded, no job_id — an interrupted submit;
    * UNKNOWN — no verdict file *and* no process this host can identify. Rerun
      here is how a second SR process ends up writing a scene a first one may
      still be writing. Not "probably safe": unproven.
    """
    job_id = task["job_id"]
    if job_id is None:
        raise SubmitConflict(
            "run_sr: 上一次同参数提交在记录运行编号之前中断（配置与脚本可能已写好，"
            "但没有启动任何进程）。不盲目重投：先核实工作目录里没有残留进程，再重试",
            kind="interrupted")
    st = query_job_status(job_id, task=task)
    base = {"job_id": job_id, "config_xml": task["config_xml"],
            "batch_script": task["batch_script"], "log_dir": task["log_dir"]}
    if st["active"]:
        return {"reuse": True, "status": "RESUMED_ACTIVE",
                "state": st["state"], "idempotent": True, **base}
    if st["state"] == "COMPLETED":
        return {"reuse": True, "status": "RESUMED_COMPLETED",
                "state": st["state"], "exit_code": st["exit_code"],
                "idempotent": True, **base}
    if st["state"] == "UNKNOWN":
        script = _posix_basename(task.get("batch_script") or "") or "run_sr"
        raise SubmitConflict(
            f"run_sr: 上一次提交（运行编号 {job_id}）的结局无法确认 —— 本机既没有它的"
            f"进程记录，也没有结果文件，无法判断那个进程是否还在写这个场景目录。"
            f"为避免两个 SR 进程同写一个目录，不自动重投。先核实："
            f"`ps -ef | grep {script}`；确认已停止后在队列里取消该任务"
            f"（取消接口带 confirmed_stopped=true），再重投",
            kind="unknown")
    # Confirmed over with no usable result: FAILED / CANCELLED → rerun.
    return {"reuse": False, "previous_state": st["state"],
            "previous_exit_code": st["exit_code"], "previous_job_id": job_id}


def submit_run_sr(params: dict, store=None, swap=None) -> dict:
    """Assemble config + run script and hand the job to the local executor.

    `swap` is the nosr_swap_plan dict of *this* submit — the source→body
    positions, which the two submit entries resolve (they are the ones that can
    say 400/err when the scene has no un-superresolved copy). Absent → the run
    executes the plain script: it reads whatever the SR script resolves on its
    own and nothing lands on the body name. The plan is deliberately NOT part of
    `params`: it is derived from the scene on disk, so folding it into the
    fingerprint would make the same logical submit hash differently on a scene
    that gained its un-superresolved copy in between.

    Idempotent via the store's sr_tasks table (default store unless one is
    passed): before submitting, the task table is consulted and a stored job_id
    is resolved through `_resolve_existing` — an active or contract-satisfying
    run is returned as-is (RESUMED_ACTIVE / RESUMED_COMPLETED, nothing new
    starts), so a crashed-loop replay cannot double-submit.

    Raises RuntimeError (including `SubmitConflict`) on any failure. Returns
    {"job_id", "status", "config_xml", "batch_script", "log_dir"}, where status
    is SUBMITTED for a fresh submission (possibly with `previous_state` when it
    re-submitted after a failed run) or a RESUMED_* reuse marker.
    """
    _reject_delete_ori(params)      # 原型期禁用，见 DELETE_ORI_MSG；两个入口共用
    if not local_exec.available():
        # The real precondition is that SR_PYTHON / SR_BUNDLE_DIR exist *on this
        # host*, which the run's own log reports if they do not. A missing bash
        # is different: it means nothing could be started at all, so it is
        # rejected before a task row exists.
        raise RuntimeError(
            "本机执行不可用：PATH 上找不到可用的 bash（Windows 上 System32\\bash.exe "
            "是 WSL 启动器，不算）")

    store = store or store_mod.default_store()
    fp = task_fingerprint(params)

    # --- idempotency check BEFORE any side effect -------------------------
    previous = None
    task = store.get_sr_task(fp)
    if task is not None:
        resolved = _resolve_existing(task)
        if resolved.get("reuse"):
            return resolved
        previous = resolved  # rerun: carry the prior failure info

    lq_path = str(params["lq_path"])
    work = Path(sr_runtime().work_dir)
    work.mkdir(parents=True, exist_ok=True)

    # The run number has to be drawn before anything is written, because it is
    # part of every file name below and the verifier is told `--job-id <id>` so
    # it names its verdict file after this run — `exit_code_file_for` then
    # re-derives that same name from the same id. Drawing it only bumps the
    # persisted counter: nothing runs yet.
    job_id = local_exec.reserve_job_id(str(work))
    stem = run_stem(params.get("suffix"), fp, job_id)
    cfg_path = work / f"{stem}.xml"
    cfg_path.write_text(
        build_config_xml(params, mask_path=(swap or {}).get("mask_1x")),
        encoding="utf-8")
    script_path = work / f"{stem}.sh"
    script_path.write_text(
        build_run_script(cfg_path, job_id=job_id, swap=swap), encoding="utf-8")

    # checkpoint the intent BEFORE the external side effect: a crash between
    # here and recording job_id leaves a job_id-less task row, and replay
    # answers "interrupted" instead of starting a second run.
    store.put_sr_task(fp, params, status="new", job_id=None,
                      config_xml=str(cfg_path), batch_script=str(script_path),
                      log_dir=str(work))

    job_id = local_exec.submit(
        script_path, job_id=job_id,
        run={"scene": lq_path, "fingerprint": fp, "config_xml": str(cfg_path)})
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


def cancel_run_sr(task: dict, confirmed_stopped: bool = False) -> bool:
    """Cancel one task's run through the task service (never a scheduler).

    `confirmed_stopped=True` is the operator's explicit statement that they
    checked by hand and no process of that run is left. It is what releases a run
    whose launch cannot be proven either way, so a queued row stuck at UNKNOWN
    can be moved on without pretending the platform stopped something it could
    not find. Without it, an unverifiable run stays as it is — the caller
    reports that back instead of inventing a stop.
    """
    job_id = task.get("job_id")
    if job_id is None:
        return False
    if confirmed_stopped:
        return local_exec.release_unknown(job_id)
    return local_exec.cancel(job_id)
