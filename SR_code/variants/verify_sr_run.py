#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""In-job contract verifier for the Slurm SR pipeline (P2 deliverable).

Runs *inside the batch job*, right after `code_0817_prod.py -f <config.xml>`,
and turns "did this job actually super-resolve anything?" into a single exit
code the platform can read back from disk:

    exit 0   contract satisfied
    exit 90  contract not satisfied (every failing condition on stderr)

Why this exists
---------------
`code_0817_prod.py` has five reachable `exit(0)` paths that all fire *before*
the SRLOG is created (docs/status/slurm-integration.md 2.3, "闭环毒药"). Slurm
then reports the step as COMPLETED, the platform's idempotency layer caches
that as a permanent success, and the scene silently never gets super-resolved.
The contract (docs/sr_code/sr-pipeline-interface.md 2) is therefore three
conditions, checked here rather than taken on trust:

    1. the SR process exited 0                    (--sr-exit-code)
    2. the SRLOG's last non-empty line says so    ("Run finished.")
    3. the output tif exists and is non-empty

A `Run skipped:` last line is the *legal* policy skip introduced by the deploy
variant's E9 edit (cloud cover over the limit): condition 1 and 2 hold, and no
output tif is expected, so the contract is satisfied without condition 3.

Exit-code file
--------------
The verdict is also written to

    <DatarootLQ>/Debug/_SREXIT_<job_id>.txt        (EXIT_FILE_FMT)

next to the SRLOG. That file is how `backend/services/slurm.py` resolves a
job's *terminal* state: the array server has `AccountingStorageType=none`, so
`sacct` is permanently unusable and `squeue` only knows about live jobs. The
name/format is a cross-language contract -- backend/services/run_sr.py derives
the same path from config.xml, and backend/tests/test_sr_verify.py asserts the
two agree. `job_id` comes from `--job-id`, else `$SLURM_JOB_ID`.

Semantics are copied from the production source, not from intuition
------------------------------------------------------------------
* `img_name[:-4]` (not `os.path.splitext`) -- that is what code_0817_prod.py
  does at :165/:582 and the two must agree even for odd extensions.
* `<Suffix></Suffix>` (or `<Suffix />`) means "no suffix" (util.get_cfg_value
  returns None when the node has no children), so the output keeps the input
  stem and `util.writeTiff` RENAMES the input to `*_NOSR.tif`. Empty suffix is
  the default in the platform's `_norm_sr_params`, and it is known-broken -- we
  reproduce it faithfully here so the verifier cannot pass a run the pipeline
  itself considers a collision.
* output path = `<DatarootLQ>/<img_name[:-4]>` + ("_"+suffix if any) + tiftype,
  which is code_0817_prod.py:582/585 plus the extension `util.writeTiff` adds.
* RC-vs-SC input choice mirrors `util.check_sr_previous_step`: `<SolarAzimuth>`
  empty => RC step => the input is literally `PAN.tif`; otherwise SC => the
  input is `<scene_dir_name><tiftype>`.

Stdlib only, and Python 3.6 compatible: this runs under the SR job interpreter
(py3.6 + torch 1.9.1), not the platform venv (py3.9).

Never raises: every IO/parse error is folded into a definite verdict, because a
verifier that dies leaves no exit-code file, which is indistinguishable from a
job that never ran.
"""

import argparse
import os
import sys
import time
from xml.dom import minidom

__version__ = "1.0.0"

EXIT_CONTRACT_OK = 0
EXIT_CONTRACT_FAIL = 90

RUN_FINISHED_MARKER = "Run finished."
RUN_SKIPPED_PREFIX = "Run skipped:"

#: Exit-code file name, shared with backend/services/run_sr.py (see module doc).
EXIT_FILE_FMT = "_SREXIT_{job_id}.txt"
EXIT_FILE_PREFIX = "_SREXIT_"

#: Bytes read from the end of the SRLOG (see 2.6: the marker is written as
#: "\nRun finished." with NO trailing newline, and nvidia-smi blobs precede it,
#: so `readlines()[-1]` is not usable).
TAIL_WINDOW = 4096

#: SRLOG/output mtime may lag config.xml by this much and still count as fresh.
#: config.xml is written on the API host, the artefacts on a compute node: with
#: 12 nodes and no NTP guarantee, strict mtime ordering would fail good runs.
#: A genuinely stale leftover is minutes-to-hours old, far outside this slack.
STALE_SLACK_SEC = 60.0

#: Top-level keys accepted for the tif extension in the OPT yml. The yml ships
#: `tiftype: .tif`; the code reads `opt["tif_type"]`, so the options loader
#: evidently maps one onto the other. Accept both rather than guess.
_TIFTYPE_KEYS = ("tif_type", "tiftype")
_DEFAULT_TIFTYPE = ".tif"


class Verdict(object):
    """Outcome of the contract check (plain object: no dataclasses on py3.6)."""

    def __init__(self):
        self.ok = True
        self.skip = False
        self.reasons = []
        self.lq_path = None
        self.srlog = None
        self.output = None
        self.reason_text = ""

    def fail(self, reason):
        self.ok = False
        self.reasons.append(reason)


class _Missing(object):
    """Sentinel: the tag is absent (distinct from present-but-empty -> None)."""

    def __repr__(self):
        return "<missing tag>"


_MISSING = _Missing()


def _cfg_value(root, tag):
    """<tag>text</tag> -> "text"; <tag></tag> / <tag/> -> None; absent -> _MISSING.

    Mirrors util.get_cfg_value (`childNodes.length != 0`), which is what makes
    an empty <Suffix> mean None rather than "".
    """
    nodes = root.getElementsByTagName(tag)
    if not nodes:
        return _MISSING
    node = nodes[0]
    if node.childNodes.length == 0:
        return None
    return node.childNodes[0].data


def _parse_xml(path):
    """minidom Document, or None when the file is absent/unparseable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        return minidom.parse(path)
    except Exception:
        return None


def _yml_scalar(path, keys):
    """First top-level `key: value` scalar in a yml, or None.

    Hand-rolled on purpose: the verifier runs in the SR py3.6 env and must not
    grow a PyYAML dependency. Only scalars are needed, and indented (nested)
    lines are skipped so a `tiftype:` inside a sub-block cannot win.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    for line in lines:
        stripped = line.split("#")[0].rstrip()
        if not stripped.strip():
            continue
        if line[:1] in (" ", "\t"):
            continue                       # nested mapping, not a top-level key
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        if key.strip() not in keys:
            continue
        value = value.strip().strip("\"'").strip()
        if value in ("", "~", "null", "None"):
            return None
        return value
    return None


def resolve_previous_step(lq_path):
    """RC / SC per util.check_sr_previous_step, or None when undecidable."""
    meta = os.path.join(lq_path, os.path.basename(lq_path) + "_meta.xml")
    doc = _parse_xml(meta)
    if doc is None:
        return None
    try:
        nodes = doc.getElementsByTagName("SolarAzimuth")
        if not nodes:
            return None
        return "RC" if nodes[0].childNodes.length == 0 else "SC"
    except Exception:
        return None


def resolve_image_name(lq_path, tiftype, previous_step):
    """Input file name, mirroring util.get_l1_pan_tif_rcsc."""
    if previous_step == "RC":
        return "PAN.tif"
    if previous_step == "SC":
        return os.path.basename(lq_path) + tiftype
    return None


def output_path_for(lq_path, img_name, suffix, tiftype):
    """Path util.writeTiff() creates (code_0817_prod.py:582/585 + extension)."""
    stem = img_name[:-4]                       # literal, NOT splitext
    name = stem + "_" + suffix if suffix is not None else stem
    return os.path.join(lq_path, name + tiftype)


def srlog_path_for(lq_path, img_name):
    return os.path.join(lq_path, "Debug", img_name[:-4] + "_SRLOG.txt")


def tail_last_line(path, window=TAIL_WINDOW):
    """Last non-empty line of a file, read from the end; "" when unavailable.

    Seeking to size-window may slice the first line in half, which cannot
    affect the *last* line -- and the whole point is that the last line is the
    only one that counts.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > window:
                f.seek(size - window)
            data = f.read()
    except Exception:
        return ""
    try:
        text = data.decode("utf-8", "replace")
    except Exception:
        return ""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def exit_code_file_for(lq_path, job_id):
    """Path of this verifier's verdict file (shared naming contract)."""
    if not lq_path or job_id in (None, ""):
        return None
    return os.path.join(lq_path, "Debug", EXIT_FILE_FMT.format(job_id=job_id))


def check_contract(config_path, sr_exit_code, job_id=None):
    """Evaluate the three conditions; returns a Verdict (never raises)."""
    v = Verdict()
    try:
        _check_contract_inner(v, config_path, sr_exit_code, job_id)
    except Exception as exc:                       # pragma: no cover - safety net
        v.fail("verifier内部错误 %s: %s" % (type(exc).__name__, exc))
    v.reason_text = "; ".join(v.reasons)
    return v


def _check_contract_inner(v, config_path, sr_exit_code, job_id):
    # ---- prerequisite: the config we are validating against ----------------
    doc = _parse_xml(config_path)
    if doc is None:
        v.fail("config.xml 不可读或非 XML: %s" % (config_path,))
        return
    config_mtime = _safe_mtime(config_path)

    lq_path = _cfg_value(doc, "DatarootLQ")
    if lq_path is _MISSING or not (lq_path or "").strip():
        v.fail("config.xml 缺 <DatarootLQ>")
        return
    lq_path = lq_path.replace("\\", "/").strip()
    v.lq_path = lq_path
    if not os.path.isdir(lq_path):
        v.fail("DatarootLQ 不是目录: %s" % (lq_path,))
        return

    suffix = _cfg_value(doc, "Suffix")
    if suffix is _MISSING:
        # util.get_cfg_value would IndexError on a missing tag; degrade to
        # "no suffix" instead of crashing, and say so.
        suffix = None

    opt_yml = _cfg_value(doc, "OPT")
    if opt_yml is _MISSING or not (opt_yml or "").strip():
        v.fail("config.xml 缺 <OPT>，无法确定 tiftype（输出名不可推导）")
        return
    tiftype = _yml_scalar(opt_yml.strip(), _TIFTYPE_KEYS) or _DEFAULT_TIFTYPE

    # ---- condition 0: input name is derivable at all ----------------------
    previous_step = resolve_previous_step(lq_path)
    if previous_step is None:
        v.fail("meta.xml 不可读或缺 <SolarAzimuth>，无法判定 RC/SC 输入: %s"
               % (os.path.join(lq_path, os.path.basename(lq_path) + "_meta.xml"),))
        return
    img_name = resolve_image_name(lq_path, tiftype, previous_step)
    if not img_name:
        v.fail("无法从 lq_path 推导输入文件名（previous_step=%s）" % (previous_step,))
        return

    v.srlog = srlog_path_for(lq_path, img_name)
    v.output = output_path_for(lq_path, img_name, suffix, tiftype)

    # ---- condition 1: the SR process exited 0 -----------------------------
    if sr_exit_code is None:
        v.fail("未提供 SR 退出码（--sr-exit-code），无法确认进程成功")
    elif sr_exit_code != 0:
        v.fail("SR 进程退出码 %s != 0" % (sr_exit_code,))

    # ---- condition 2: SRLOG exists, is fresh, and ends correctly ----------
    finished = False
    if not os.path.isfile(v.srlog):
        v.fail("缺 SRLOG（静默失败或作业未跑到日志阶段）: %s" % (v.srlog,))
    elif _is_stale(v.srlog, config_mtime):
        v.fail("SRLOG 早于 config.xml，是上次残留: %s" % (v.srlog,))
    else:
        last = tail_last_line(v.srlog)
        if last.startswith(RUN_SKIPPED_PREFIX):
            v.skip = True                       # legal policy skip (E9 variant)
        elif last == RUN_FINISHED_MARKER:
            finished = True
        else:
            v.fail("SRLOG 末行不是 %r 也不是 %r 开头: %r"
                   % (RUN_FINISHED_MARKER, RUN_SKIPPED_PREFIX, last))

    # ---- condition 3: output tif exists and is non-empty ------------------
    # A skipped run legitimately produces no tif (contract 2), so only a run
    # that claims "Run finished." has to show the artefact.
    if finished:
        try:
            size = os.path.getsize(v.output)
        except Exception:
            v.fail("缺输出 tif: %s" % (v.output,))
        else:
            if size <= 0:
                v.fail("输出 tif 为 0 字节: %s" % (v.output,))
            elif _is_stale(v.output, config_mtime):
                v.fail("输出 tif 早于 config.xml，是上次残留: %s" % (v.output,))


def _safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except Exception:
        return None


def _is_stale(path, reference_mtime):
    """True when `path` predates config.xml by more than STALE_SLACK_SEC."""
    if reference_mtime is None:
        return False
    mtime = _safe_mtime(path)
    if mtime is None:
        return False
    return mtime < reference_mtime - STALE_SLACK_SEC


def write_exit_code_file(v, job_id, sr_exit_code):
    """Persist the verdict next to the SRLOG; returns the path or None.

    Written on failure too -- that is the whole point: the platform must be
    able to tell "ran and failed the contract" from "never ran".
    """
    path = exit_code_file_for(v.lq_path, job_id)
    if path is None:
        return None
    body = "\n".join([
        "job_id=%s" % (job_id,),
        "sr_exit_code=%s" % ("" if sr_exit_code is None else sr_exit_code,),
        "verdict=%d" % (EXIT_CONTRACT_OK if v.ok else EXIT_CONTRACT_FAIL,),
        "skip=%d" % (1 if v.skip else 0,),
        "written_at=%d" % (int(time.time()),),
        "srlog=%s" % (v.srlog or "",),
        "output=%s" % (v.output or "",),
        "reason=%s" % (v.reason_text.replace("\n", " "),),
    ]) + "\n"
    try:
        debug_dir = os.path.dirname(path)
        if debug_dir and not os.path.isdir(debug_dir):
            os.makedirs(debug_dir)
        with open(path, "w") as f:
            f.write(body)
    except Exception as exc:
        sys.stderr.write("verify_sr_run: 无法写退出码文件 %s: %s\n" % (path, exc))
        return None
    return path


def _env_job_id():
    raw = os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="verify_sr_run.py",
        description="Check the SR job contract and record the verdict on disk.")
    ap.add_argument("--config", required=True,
                    help="the same config.xml the SR script was given (-f)")
    ap.add_argument("--sr-exit-code", type=int, default=None,
                    help="exit code of code_0817_prod.py (the $? right after it)")
    ap.add_argument("--job-id", default=None,
                    help="Slurm job id (default: $SLURM_JOB_ID)")
    ap.add_argument("--quiet", action="store_true",
                    help="only print the verdict line")
    args = ap.parse_args(argv)

    job_id = args.job_id if args.job_id is not None else _env_job_id()
    v = check_contract(args.config, args.sr_exit_code, job_id=job_id)
    exit_path = write_exit_code_file(v, job_id, args.sr_exit_code)

    for reason in v.reasons:
        sys.stderr.write("verify_sr_run: %s\n" % (reason,))
    if exit_path:
        sys.stderr.write("verify_sr_run: verdict file %s\n" % (exit_path,))
    else:
        sys.stderr.write("verify_sr_run: 未写退出码文件（lq_path/job_id 不可用），"
                         "平台将判为 UNKNOWN 并重投\n")

    if not args.quiet:
        if v.ok:
            print("SR contract satisfied%s [%s]"
                  % (" (policy skip)" if v.skip else "", v.output or "-"))
        else:
            print("SR contract NOT satisfied: %s" % (v.reason_text or "unknown",))
    return EXIT_CONTRACT_OK if v.ok else EXIT_CONTRACT_FAIL


if __name__ == "__main__":
    sys.exit(main())
