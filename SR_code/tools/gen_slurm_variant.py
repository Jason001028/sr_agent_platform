#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the Slurm deployment variant of the SR production script.

The production script ``SR_code/code_0817_prod.py`` is kept byte-for-byte
untouched (it is the single source of truth).  This generator derives a
deployment variant from it by mechanical anchor replacement, so every
difference is reviewable, reproducible and machine-checkable.

Contract
--------
* ``EDITS`` is an ordered list of ``(edit_id, anchor, replacement, expected)``.
  ``anchor`` must be the exact source substring *including indentation*.
* If an anchor does not occur exactly ``expected`` times, the generator prints
  the offending anchors and exits 2.  It never skips an edit silently.
* The source is normalised CRLF -> LF on read; the variant is written with
  a uniform ``\\n``.
* ``provenance.json`` carries no timestamp, so ``--check`` is byte-deterministic.

Usage
-----
    python SR_code/tools/gen_slurm_variant.py            # write SR_code/variants/
    python SR_code/tools/gen_slurm_variant.py --check    # verify, exit 1 if stale

Exit codes: 0 = ok, 1 = stale (only with ``--check``), 2 = anchor mismatch.
"""

import argparse
import difflib
import hashlib
import json
import os
import sys

GENERATOR_VERSION = "1.0.0"
GENERATOR_REL = "SR_code/tools/gen_slurm_variant.py"

SOURCE_REL = "SR_code/code_0817_prod.py"
OUT_DIR_REL = "SR_code/variants"
VARIANT_NAME = "code_0817_prod_slurm.py"
DIFF_NAME = "code_0817_prod_slurm.diff"
PROVENANCE_NAME = "code_0817_prod_slurm.provenance.json"
GITATTRIBUTES_NAME = ".gitattributes"
GITATTRIBUTES_TEXT = "* text eol=lf\n"

BANNER_RULE = "# " + "=" * 75

# ---------------------------------------------------------------------------
# The edit table.  One entry per finding in docs/status/slurm-integration.md
# sec 2.2 (E1-E9).  Order matches the numbering, not the file position; the
# anchors are disjoint so the order does not affect the result.
# ---------------------------------------------------------------------------

EDITS = [
    # E1 -- module-level load_library() with no guard: a missing .so kills the
    # process during import, before any log file exists.  Mirror the precedent
    # in code_0820_prod_windows.py:25-28.
    (
        "E1",
        'lib = npct.load_library("/DiskArray/ProductionSchedule/exe_CentOS7/'
        'SR_bundle/mmsr_bundle/codes/tools/ImgHistMatch", ".")',
        'try:\n'
        '    lib = npct.load_library("/DiskArray/ProductionSchedule/exe_CentOS7/'
        'SR_bundle/mmsr_bundle/codes/tools/ImgHistMatch", ".")\n'
        'except Exception:\n'
        '    lib = None',
        1,
    ),
    # E2 -- unconditional CUDA_VISIBLE_DEVICES="0" wipes the Slurm allocation
    # and piles every --gres=gpu:1 job onto physical card 0.
    (
        "E2",
        '    os.environ[\'CUDA_VISIBLE_DEVICES\'] = "0"',
        '    # CUDA_VISIBLE_DEVICES is injected by Slurm (--gres=gpu:1). '
        'Never overwrite it.',
        1,
    ),
    # E3 -- gpuid feeds pynvml/log reporting (a *physical* index).  Read it from
    # the environment with an int() fallback: Slurm may inject a UUID or a MIG
    # device form, and int(gpuid) later would raise ValueError.
    (
        "E3",
        "    gpuid = '0'",
        '    # gpuid is used only for pynvml/log reporting (physical index).\n'
        '    # Slurm may inject a UUID or MIG form; fall back to \'0\' if it is not\n'
        '    # a plain integer so the int(gpuid) calls below cannot raise.\n'
        '    try:\n'
        '        _cvd = os.environ.get(\'CUDA_VISIBLE_DEVICES\', \'0\').split(\',\')[0]\n'
        '        gpuid = str(int(_cvd))\n'
        '    except (ValueError, TypeError):\n'
        "        gpuid = '0'",
        1,
    ),
    # E4 -- same as E2, in the __main__ guard.
    (
        "E4",
        '    os.environ[\'CUDA_VISIBLE_DEVICES\'] = "1"',
        '    # CUDA_VISIBLE_DEVICES is injected by Slurm (--gres=gpu:1). '
        'Never overwrite it.',
        1,
    ),
    # E5 -- pynvml counts *physical* cards and ignores the injected device list;
    # on a 4-card node it always reports 4.  Count what this job can see.
    (
        "E5",
        "    pynvml.nvmlInit()\n"
        "    gpu_count = pynvml.nvmlDeviceGetCount()\n"
        "    pynvml.nvmlShutdown()",
        "    # Count the devices this job can actually see.  nvml reports the\n"
        "    # host's physical cards and ignores the list Slurm injects.\n"
        "    gpu_count = torch.cuda.device_count()",
        1,
    ),
    # E6 -- "!= 4" is always true for a single-GPU allocation, so every job
    # fails.  Mirror code_0820_prod_windows.py:717 ("< 1").
    (
        "E6",
        "    if gpu_available is False or gpu_count != 4:",
        "    if gpu_available is False or gpu_count < 1:",
        1,
    ),
    # E7 -- stopping slurmd inside a job either fails on permissions (job hangs
    # around dead) or, as root, really evicts the node from the pool.
    (
        "E7",
        '        cmd = "systemctl stop slurmd.service"\n'
        "        os.system(cmd)",
        "        # Slurm job: never stop the node daemon.  A failed job must not\n"
        "        # evict the node from the pool; report it via the exit code.",
        1,
    ),
    # E8 -- the shared log used to mean "node offlined"; keep writing it, but
    # with wording that does not imply the node was taken out of service.
    (
        "E8",
        '                srlog.writelines("{:s} stop {:s}\\n".format('
        "util.get_timestamp(), hostname))",
        '                srlog.writelines("{:s} gpu-error {:s}\\n".format('
        "util.get_timestamp(), hostname))",
        1,
    ),
    # E9 -- the cloud-limit skip exits 0 before SRLOG is created, which is
    # indistinguishable from a silent failure (and gets cached as a success).
    # Create the log and write an explicit terminal line instead.
    (
        "E9",
        "    if cloudpercent > cloudlimit:\n"
        "        exit(0)",
        "    if cloudpercent > cloudlimit:\n"
        "        # Legitimate policy skip.  Create SRLOG and write an explicit\n"
        "        # terminal line so this stays distinguishable from a silent\n"
        "        # failure (exit 0 with no SRLOG).\n"
        '        _skip_log = osp.join(lq_path, "Debug", img_name[:-4] + "_SRLOG.txt")\n'
        "        os.makedirs(osp.dirname(_skip_log), exist_ok=True)\n"
        "        with open(_skip_log, 'w') as _srlog:\n"
        "            _srlog.writelines('CloudPercent {:d} > CloudLimit {:d}, SR "
        "not needed.\\n'\n"
        "                              .format(cloudpercent, cloudlimit))\n"
        '            _srlog.writelines("Run skipped: cloud limit exceeded\\n")\n'
        "        print('CloudPercent {:d} > CloudLimit {:d}, SR skipped by policy.'\n"
        "              .format(cloudpercent, cloudlimit))\n"
        "        exit(0)",
        1,
    ),
]

# One-line rationale per edit id, copied into provenance.json for review.
EDIT_NOTES = {
    "E1": "guard module-level load_library() so a missing .so cannot kill import",
    "E2": "drop the hard-coded GPU selection in main() (Slurm --gres decides)",
    "E3": "derive gpuid from the environment with an int() fallback",
    "E4": "drop the hard-coded GPU selection in __main__",
    "E5": "count visible devices instead of physical cards (nvml)",
    "E6": "GPU guard: '< 1' instead of '!= 4'",
    "E7": "never systemctl stop slurmd from inside a job",
    "E8": "GPU-error log wording no longer claims the node was stopped",
    "E9": "cloud-limit skip writes SRLOG + 'Run skipped:' before exit(0)",
}


class AnchorError(Exception):
    """One or more anchors did not match the expected number of times."""

    def __init__(self, problems):
        # problems: list of (edit_id, expected, got, anchor)
        self.problems = problems
        super(AnchorError, self).__init__("anchor mismatch")

    def render(self, source_rel):
        lines = ["", "anchor mismatch -- refusing to write a partial variant:",
                 "  source: %s" % source_rel, ""]
        for edit_id, expected, got, anchor in self.problems:
            lines.append("  %s: expected %d occurrence(s), found %d"
                         % (edit_id, expected, got))
            for raw in anchor.split("\n"):
                lines.append("      | %s" % raw)
            lines.append("")
        lines.append("  Fix the anchor in %s so it matches the source exactly,"
                     % GENERATOR_REL)
        lines.append("  or update the source and re-derive the edit table.")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def normalize(text):
    """CRLF/CR -> LF so the variant is identical on every checkout."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_source(path):
    with open(path, "rb") as fh:
        return normalize(fh.read().decode("utf-8"))


def repo_root():
    """d:/.../sr_agent_platform -- SR_code/tools/this_file.py -> up 3."""
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def rel_or_abs(path, root):
    path = os.path.abspath(path)
    try:
        rel = os.path.relpath(path, root)
    except ValueError:  # different drive on Windows
        return path.replace("\\", "/")
    if rel.startswith(".."):
        return path.replace("\\", "/")
    return rel.replace("\\", "/")


def check_anchors(source, edits):
    """Return a list of (edit_id, expected, got, anchor) for every mismatch."""
    problems = []
    for edit_id, anchor, _replacement, expected in edits:
        got = source.count(anchor)
        if got != expected:
            problems.append((edit_id, expected, got, anchor))
    return problems


def apply_edits(source, edits):
    """Apply every edit.  Call only after check_anchors() returned empty."""
    out = source
    for _edit_id, anchor, replacement, _expected in edits:
        out = out.replace(anchor, replacement, 1)
    return out


def render_banner(source_rel, source_sha256):
    return (
        BANNER_RULE + "\n"
        "# GENERATED FILE \u2014 DO NOT EDIT\n"
        "#\n"
        "# Slurm deployment variant of the SR production script.  Edit the\n"
        "# source and regenerate; hand edits here are lost and fail --check.\n"
        "#\n"
        "# Generator : %s v%s\n"
        "# Source    : %s\n"
        "# Source sha256 (CRLF->LF normalised) : %s\n"
        "# Regenerate: python %s\n"
        "# Verify    : python %s --check\n"
        % (GENERATOR_REL, GENERATOR_VERSION, source_rel, source_sha256,
           GENERATOR_REL, GENERATOR_REL)
        + BANNER_RULE + "\n"
    )


def build_artifacts(source_text, source_rel=SOURCE_REL, out_dir_rel=OUT_DIR_REL,
                    variant_name=VARIANT_NAME):
    """Return {file_name: text} for every artifact, or raise AnchorError.

    Pure function: same inputs -> byte-identical outputs, no timestamps.
    """
    problems = check_anchors(source_text, EDITS)
    if problems:
        raise AnchorError(problems)

    source_sha = sha256_text(source_text)
    variant_text = render_banner(source_rel, source_sha) + apply_edits(source_text, EDITS)

    # lineterm="" + explicit "\n".join keeps control lines on their own line;
    # no filename timestamps (they would break --check determinism).
    diff_text = "\n".join(difflib.unified_diff(
        source_text.splitlines(),
        variant_text.splitlines(),
        fromfile=source_rel,
        tofile=out_dir_rel + "/" + variant_name,
        lineterm="",
    )) + "\n"

    provenance = {
        "generator": GENERATOR_REL,
        "generator_version": GENERATOR_VERSION,
        "source": {
            "path": source_rel,
            "sha256": source_sha,
            "normalisation": "CRLF->LF before hashing",
        },
        "variant": {
            "path": out_dir_rel + "/" + variant_name,
            "sha256": sha256_text(variant_text),
        },
        "diff": {
            "path": out_dir_rel + "/" + DIFF_NAME,
            "sha256": sha256_text(diff_text),
        },
        "edits": [
            {
                "id": edit_id,
                "note": EDIT_NOTES.get(edit_id, ""),
                "anchor_sha256": sha256_text(anchor),
                "occurrences": source_text.count(anchor),
            }
            for edit_id, anchor, _replacement, _expected in EDITS
        ],
    }
    provenance_text = json.dumps(provenance, indent=2, ensure_ascii=False) + "\n"

    return {
        variant_name: variant_text,
        DIFF_NAME: diff_text,
        PROVENANCE_NAME: provenance_text,
        GITATTRIBUTES_NAME: GITATTRIBUTES_TEXT,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def write_artifacts(out_dir, artifacts):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    for name in sorted(artifacts):
        path = os.path.join(out_dir, name)
        with open(path, "wb") as fh:
            fh.write(artifacts[name].encode("utf-8"))
        print("wrote %s" % path.replace("\\", "/"))


def check_artifacts(out_dir, artifacts):
    """Return a list of human-readable staleness descriptions."""
    stale = []
    for name in sorted(artifacts):
        path = os.path.join(out_dir, name)
        want = artifacts[name].encode("utf-8")
        if not os.path.isfile(path):
            stale.append("%s: missing" % path.replace("\\", "/"))
            continue
        with open(path, "rb") as fh:
            have = fh.read()
        if have != want:
            stale.append("%s: differs (on disk %d bytes, expected %d bytes, sha256 %s)"
                         % (path.replace("\\", "/"), len(have), len(want),
                            hashlib.sha256(have).hexdigest()[:12]))
    return stale


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate / verify the Slurm deployment variant of %s" % SOURCE_REL)
    parser.add_argument("--check", action="store_true",
                        help="compare on-disk artifacts, do not write; exit 1 if stale")
    parser.add_argument("--source", default=None,
                        help="override the source script (default %s)" % SOURCE_REL)
    parser.add_argument("--out-dir", default=None,
                        help="override the output directory (default %s)" % OUT_DIR_REL)
    args = parser.parse_args(argv)

    root = repo_root()
    source_path = args.source or os.path.join(root, SOURCE_REL)
    out_dir = args.out_dir or os.path.join(root, OUT_DIR_REL)
    source_rel = rel_or_abs(source_path, root)
    out_dir_rel = rel_or_abs(out_dir, root)

    if not os.path.isfile(source_path):
        sys.stderr.write("source not found: %s\n" % source_path)
        return 2

    source_text = read_source(source_path)

    try:
        artifacts = build_artifacts(source_text, source_rel=source_rel,
                                    out_dir_rel=out_dir_rel)
    except AnchorError as exc:
        sys.stderr.write(exc.render(source_rel) + "\n")
        return 2

    if args.check:
        stale = check_artifacts(out_dir, artifacts)
        if stale:
            sys.stderr.write("STALE -- variant artifacts do not match the source:\n")
            for line in stale:
                sys.stderr.write("  %s\n" % line)
            sys.stderr.write("\nRe-run: python %s\n" % GENERATOR_REL)
            return 1
        print("OK -- %d artifact(s) up to date in %s"
              % (len(artifacts), out_dir_rel))
        return 0

    write_artifacts(out_dir, artifacts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
