#!/bin/sh
# =============================================================================
# probe_slurm.sh -- read-only Slurm readiness probe for the SR pipeline node.
#
# Answers the questions P1 of docs/status/slurm-integration.md left open:
# is Slurm actually installed and working, can a --gres=gpu:1 job get a card,
# is accounting usable, and are the array / bundle paths visible and writable.
#
#   * read-only: no package installs, no writes outside the optional --deep
#     srun, no systemctl start/stop, no config edits;
#   * no root required: every check degrades to WARN when it would need it;
#   * no jq, no bashisms: plain POSIX sh, coreutils only.
#
# Usage:
#   sh probe_slurm.sh              # static checks only (safe, takes seconds)
#   sh probe_slurm.sh --deep       # + one 2-minute --gres=gpu:1 srun job
#   sh probe_slurm.sh --help
#
# Output: one line per check --
#   PROBE: <id> <OK|WARN|FAIL> <one-line message>
# followed by a HINTS block mapping every non-OK line to its fix section.
# Exit code: 0 if no FAIL, 1 if any FAIL (WARN alone still exits 0).
#
# The probe never aborts early: "Slurm is missing entirely" is a result we
# need recorded, not a reason to stop.  Paste the whole output back.
# =============================================================================

DEEP=0
case "${1:-}" in
    --deep) DEEP=1 ;;
    -h|--help)
        sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    "") ;;
    *)
        printf 'unknown argument: %s\n' "$1" >&2
        printf 'usage: sh %s [--deep]\n' "$0" >&2
        exit 2
        ;;
esac

# --- configuration knobs (override via environment) --------------------------
SR_BUNDLE_DIR="${SR_BUNDLE_DIR:-/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes}"
SR_SLURM_WORK_DIR="${SR_SLURM_WORK_DIR:-/tmp/sr_agent_work}"
SR_AGENT_DB="${SR_AGENT_DB:-/data/www/sr-agent-platform/sr_agent.db}"
SR_SCENES_ROOT="${SR_SCENES_ROOT:-/data/scenes}"
SR_SLURM_PARTITION="${SR_SLURM_PARTITION:-}"
SR_OPTIONS_YML="${SR_OPTIONS_YML:-/DiskArray/tmp/wangrz/sr_utils/espan3_2026_gf04_tile500.yml}"
SLURM_STOP_LOG="${SLURM_STOP_LOG:-/DiskArray/ProductionSchedule/config/SlurmStopLog.txt}"

# --- state -------------------------------------------------------------------
OKS=0
WARNS=0
FAILS=0
HINTS=""

report() {
    # report <id> <OK|WARN|FAIL> <message> [hint]
    _id=$1
    _st=$2
    _msg=$3
    _hint=${4:-}
    printf 'PROBE: %-14s %-4s %s\n' "$_id" "$_st" "$_msg"
    case "$_st" in
        OK)   OKS=$((OKS + 1)) ;;
        WARN) WARNS=$((WARNS + 1)) ;;
        FAIL) FAILS=$((FAILS + 1)) ;;
    esac
    if [ -n "$_hint" ] && [ "$_st" != "OK" ]; then
        HINTS="${HINTS}${_st} ${_id} :: ${_hint}
"
    fi
}

have() { command -v "$1" >/dev/null 2>&1; }

# timeout wrapper: coreutils 'timeout' is present on CentOS 7 but degrade
# gracefully so a missing binary cannot hang or abort the probe.
if have timeout; then
    TMO="timeout 30"
    TMO_DEEP="timeout 180"
else
    TMO=""
    TMO_DEEP=""
fi

# can_write_as <dir> [user] -- 0 writable, 1 not writable, 2 cannot test, 3 missing
can_write_as() {
    _p=$1
    _u=${2:-}
    [ -d "$_p" ] || return 3
    if [ -z "$_u" ]; then
        [ -w "$_p" ] && return 0
        return 1
    fi
    have sudo || return 2
    sudo -n -u "$_u" test -w "$_p" >/dev/null 2>&1 && return 0
    return 1
}

show() {
    # show <label> <multi-line evidence, truncated>
    printf '  %s\n' "$1"
    printf '%s\n' "$2" | sed -n '1,12p' | sed 's/^/    /'
}

ME_LONG=$(hostname 2>/dev/null || printf 'unknown')
ME_SHORT=$(hostname -s 2>/dev/null || printf '%s' "$ME_LONG")
ME_USER=$(id -un 2>/dev/null || printf 'unknown')

# =============================================================================
# 1. binaries
# =============================================================================

MISSING_CLIENT=""
for b in sbatch squeue scontrol sinfo srun sacct scancel; do
    have "$b" || MISSING_CLIENT="$MISSING_CLIENT $b"
done
if [ -z "$MISSING_CLIENT" ]; then
    report client-bin OK "all Slurm client binaries on PATH"
else
    report client-bin FAIL "missing client binaries:$MISSING_CLIENT" \
        "H-BIN: install slurm / slurm-client RPMs (offline: from the intranet mirror)"
fi

MISSING_SERVER=""
for b in slurmd slurmctld slurmdbd; do
    have "$b" || [ -x "/usr/sbin/$b" ] || [ -x "/usr/local/sbin/$b" ] || \
        MISSING_SERVER="$MISSING_SERVER $b"
done
if [ -z "$MISSING_SERVER" ]; then
    report server-bin OK "slurmd / slurmctld / slurmdbd all present on this host"
else
    report server-bin WARN "not present:$MISSING_SERVER" \
        "H-SERVER: fine on a submit-only client; otherwise the node cannot run jobs"
fi

# =============================================================================
# 2. munge authentication
# =============================================================================

if have munge && have unmunge; then
    _mout=$($TMO sh -c 'munge -n 2>&1 | unmunge 2>&1')
    if printf '%s' "$_mout" | grep -q 'STATUS:.*Success'; then
        report munge-auth OK "munge round-trip OK ($(printf '%s' "$_mout" | grep 'STATUS:' | head -1 | tr -s ' '))"
    else
        report munge-auth FAIL "munge round-trip failed: $(printf '%s' "$_mout" | head -2 | tr '\n' ' ')" \
            "H-MUNGE: munged down, or /etc/munge/munge.key missing / mismatched / unreadable"
    fi
else
    report munge-auth FAIL "munge and/or unmunge not on PATH" \
        "H-MUNGE: install munge; without it every Slurm RPC fails with a credential error"
fi

if [ -r /etc/munge/munge.key ]; then
    report munge-key OK "/etc/munge/munge.key readable"
else
    report munge-key WARN "/etc/munge/munge.key not readable by $ME_USER" \
        "H-MUNGE: expected for non-root; only fatal if munge-auth also failed"
fi

# =============================================================================
# 3. services
# =============================================================================

if have systemctl; then
    _svc=""
    _core_bad=""    # slurmd / munge must be active on any node that runs jobs
    _other_bad=""   # controller / accounting daemons: only a problem if present-but-stopped
    for u in slurmd slurmctld munge slurmdbd; do
        _st=$(systemctl is-active "$u" 2>/dev/null)
        [ -n "$_st" ] || _st="unknown"
        _svc="$_svc $u=$_st"
        case "$u" in
            slurmd|munge)
                [ "$_st" = "active" ] || _core_bad="$_core_bad $u=$_st" ;;
            *)
                # slurmctld / slurmdbd normally live on the controller host, so
                # "unknown" (no such unit here) is normal on a compute node.
                case "$_st" in
                    active)  ;;
                    unknown) ;;   # unit not installed here -- normal on a compute node
                    *)       _other_bad="$_other_bad $u=$_st" ;;
                esac ;;
        esac
    done
    if [ -n "$_core_bad" ]; then
        report systemd FAIL "core not active:$_core_bad | all:$_svc" \
            "H-SVC: this node cannot run jobs -- systemctl status <unit>"
    elif [ -n "$_other_bad" ]; then
        report systemd WARN "present but stopped:$_other_bad | all:$_svc" \
            "H-SVC: the unit exists on this host but is not running"
    else
        report systemd OK "$_svc"
    fi
else
    report systemd WARN "systemctl not available" \
        "H-SVC: check the daemons directly (ps -ef | grep slurm)"
fi

# =============================================================================
# 4. cluster reachability, partitions
# =============================================================================

if have scontrol; then
    _ping=$($TMO scontrol ping 2>&1)
    _prc=$?
    if [ "$_prc" -eq 0 ] && printf '%s' "$_ping" | grep -qi 'UP'; then
        report scontrol-ping OK "$(printf '%s' "$_ping" | head -1)"
    else
        report scontrol-ping FAIL "rc=$_prc $(printf '%s' "$_ping" | head -1)" \
            "H-PING: slurmctld unreachable -- check its service, SlurmctldHost, and munge"
    fi
else
    report scontrol-ping FAIL "scontrol is not installed" "H-BIN: install the Slurm client packages"
fi

if have sinfo; then
    _parts=$($TMO sinfo -h -o "%P %a %l %D %t" 2>&1)
    _prc=$?
    if [ "$_prc" -eq 0 ] && [ -n "$_parts" ]; then
        report partitions OK "$(printf '%s' "$_parts" | tr '\n' ';') [SR_SLURM_PARTITION=${SR_SLURM_PARTITION:-<unset>}]"
        show 'sinfo -h -o "%P %a %l %D %t":' "$_parts"
    else
        report partitions FAIL "sinfo rc=$_prc, output empty" \
            "H-PART: no PartitionName= in slurm.conf, or no registered nodes"
    fi
else
    report partitions FAIL "sinfo is not installed" "H-BIN: install the Slurm client packages"
fi

if have scontrol; then
    _plim=$($TMO scontrol show partition -o 2>&1 | head -6)
    if [ -n "$_plim" ]; then
        report part-limits OK "$(printf '%s' "$_plim" | grep -o 'MaxTime=[^ ]*' | head -3 | tr '\n' ' ')"
        show 'scontrol show partition -o:' "$_plim"
    else
        report part-limits WARN "no partition detail returned" "H-PART: same as the partitions probe"
    fi
else
    report part-limits FAIL "scontrol is not installed" "H-BIN: install the Slurm client packages"
fi

# =============================================================================
# 5. nodes and GRES
# =============================================================================

NODE_TABLE=""
if have sinfo; then
    NODE_TABLE=$($TMO sinfo -N -h -o "%N %t %G %C %m" 2>&1)
    if [ -n "$NODE_TABLE" ]; then
        report nodes OK "$(printf '%s' "$NODE_TABLE" | wc -l | tr -d ' ') node row(s) reported"
        show 'sinfo -N -h -o "%N %t %G %C %m":' "$NODE_TABLE"
    else
        report nodes FAIL "sinfo -N returned nothing" \
            "H-NODES: no NodeName= registered, or slurmd is down on every node"
    fi
else
    report nodes FAIL "sinfo is not installed" "H-BIN: install the Slurm client packages"
fi

if [ -n "$NODE_TABLE" ]; then
    # Stop the match at whitespace / ',' / '(' -- the node row carries further
    # columns after the GRES token, and they must not be swallowed into it.
    _gres=$(printf '%s\n' "$NODE_TABLE" | grep -o 'gpu:[^,()[:space:]]*' | sort -u | tr '\n' ' ')
    if [ -n "$_gres" ]; then
        report gres-gpu OK "GPU GRES registered: $_gres"
    else
        report gres-gpu FAIL "no 'gpu:' GRES on any node" \
            "H-GRES: --gres=gpu:1 will be rejected; see the config probe"
    fi
else
    report gres-gpu FAIL "cannot read node GRES (no node table)" "H-NODES: fix the node table first"
fi

# GPU registry vs nvidia-smi: only meaningful when this host is itself a node.
_nvlist=""
if have nvidia-smi; then
    _nvlist=$(nvidia-smi -L 2>/dev/null | grep -c '^GPU ')
fi
if [ -n "$_nvlist" ]; then
    _myrow=$(printf '%s\n' "$NODE_TABLE" | grep "^${ME_SHORT}[[:space:]]" | head -1)
    # Both Gres=gpu:4 and Gres=gpu:<type>:4 are in the wild; the count is the
    # field after the last colon.  The previous pattern demanded the typed form,
    # so every plain Gres=gpu:N node was reported as having no GPU at all.
    _myn=$(printf '%s' "$_myrow" | grep -o 'gpu:[^,()[:space:]]*' | head -1 | sed 's/.*://')
    if [ -z "$_myrow" ]; then
        report gpu-count WARN "host $ME_SHORT is not a Slurm node; local nvidia-smi -L = $_nvlist" \
            "H-GPU: run this probe on a compute node to compare the registry against nvidia-smi"
    elif [ -z "$_myn" ]; then
        report gpu-count FAIL "node $ME_SHORT has no gpu GRES but nvidia-smi sees $_nvlist card(s)" \
            "H-GRES: add Gres=gpu:<type>:$_nvlist to the NodeName line and reconfigure"
    elif [ "$_myn" = "$_nvlist" ]; then
        report gpu-count OK "node $ME_SHORT GRES gpu:$_myn matches nvidia-smi -L ($_nvlist)"
    else
        report gpu-count FAIL "node $ME_SHORT GRES gpu:$_myn != nvidia-smi -L ($_nvlist)" \
            "H-GRES: registry out of sync -- fix Gres= on the node and reconfigure"
    fi
else
    report gpu-count WARN "nvidia-smi unavailable or sees no card (user=$ME_USER host=$ME_SHORT)" \
        "H-GPU: cannot cross-check; run this on a compute node as a GPU-enabled user"
fi

# =============================================================================
# 6. config switches that decide whether --gres=gpu:1 can work
# =============================================================================

if have scontrol; then
    _cfg=$($TMO scontrol show config 2>&1)
    _sel=$(printf '%s' "$_cfg" | grep -m1 '^ *SelectType ' | tr -s ' ' | cut -d' ' -f2- | tr -d ' ')
    _task=$(printf '%s' "$_cfg" | grep -m1 '^ *TaskPlugin ' | tr -s ' ' | cut -d' ' -f2- | tr -d ' ')
    _proc=$(printf '%s' "$_cfg" | grep -m1 '^ *ProctrackType ' | tr -s ' ' | cut -d' ' -f2- | tr -d ' ')
    _gres=$(printf '%s' "$_cfg" | grep -m1 '^ *GresTypes ' | tr -s ' ' | cut -d' ' -f2- | tr -d ' ')
    _acct=$(printf '%s' "$_cfg" | grep -m1 '^ *AccountingStorageType ' | tr -s ' ' | cut -d' ' -f2- | tr -d ' ')
    _summary="SelectType=${_sel:-?} TaskPlugin=${_task:-?} ProctrackType=${_proc:-?} GresTypes=${_gres:-?} AccountingStorageType=${_acct:-?}"
    _cfg_bad=""
    _cfg_note=""
    # --gres=gpu:N is granted on GresTypes + SelectType.  The cgroup plugins
    # decide how strictly the granted devices are isolated, not whether one is
    # handed out -- so they are notes, not failures.
    printf '%s' "$_gres" | grep -q 'gpu'      || _cfg_bad="$_cfg_bad GresTypes-lacks-gpu"
    # --gres is honoured by select/cons_tres and by select/cray_aries; the
    # older plugins (linear, cons_res) reject the option outright.  Failing a
    # GRES-capable but non-default plugin would push an operator into editing a
    # working slurm.conf, so non-cons_tres is a note, not a failure.
    if ! printf '%s' "$_sel" | grep -qE 'cons_tres|cray_aries'; then
        _cfg_bad="$_cfg_bad SelectType=${_sel:-?}-cannot-honour-GRES"
    elif ! printf '%s' "$_sel" | grep -q 'cons_tres'; then
        _cfg_note="$_cfg_note SelectType=${_sel}-GRES-capable-but-non-default"
    fi
    printf '%s' "$_proc" | grep -q 'cgroup'   || _cfg_note="$_cfg_note ProctrackType=${_proc:-?}-no-cgroup-tracking"
    printf '%s' "$_task" | grep -q 'cgroup'   || _cfg_note="$_cfg_note TaskPlugin=${_task:-?}-no-cgroup-device-isolation"
    if [ -z "$_cfg_bad" ]; then
        report config OK "$_summary$_cfg_note"
    else
        report config FAIL "unsuitable:$_cfg_bad | $_summary" \
            "H-CONFIG: --gres=gpu:N needs GresTypes=gpu and a GRES-capable SelectType (cons_tres / cray_aries)"
    fi
    show 'selected scontrol show config keys:' "$_summary"
else
    report config FAIL "scontrol not installed; cannot read the config switches" \
        "H-BIN: install the Slurm client packages"
fi

# =============================================================================
# 7. accounting: sacct / slurmdbd / associations
# =============================================================================

ACCT_DISABLED=""

if have sacct; then
    # Deliberately NOT a pipeline: with `cmd | head` the $? below would report
    # head's status, not sacct's -- which silently turned "accounting storage is
    # disabled" (rc=1) into an OK on the real node.
    _sacct=$($TMO sacct -a -X -o JobID,State -n 2>&1)
    _src=$?
    _sacct1=$(printf '%s\n' "$_sacct" | head -1 | tr -s ' ')
    if printf '%s' "$_sacct" | grep -qi 'accounting storage is disabled'; then
        ACCT_DISABLED=1
        report sacct FAIL "accounting is disabled: $_sacct1" \
            "H-ACCT: sacct can never return a terminal state -- see H-ACCT in the P2 notes"
    elif [ "$_src" -eq 0 ] && [ -n "$_sacct" ]; then
        report sacct OK "sacct answers (sample: $_sacct1)"
    elif [ "$_src" -eq 0 ]; then
        report sacct WARN "sacct runs but returns no rows (empty accounting DB)" \
            "H-ACCT: normal on a fresh cluster; job_status stays UNKNOWN until jobs are recorded"
    else
        report sacct FAIL "sacct rc=$_src: $_sacct1" \
            "H-ACCT: sacct needs slurmdbd + a database; without it job_status is always UNKNOWN"
    fi
    # The platform parses State with spaces ('CANCELLED by 1000'); confirm the
    # machine-readable form too -- see slurm-integration.md sec 2.4 item 2.
    _pars=$($TMO sacct -a -X --parsable2 --noheader -o JobID,State,ExitCode 2>&1 | head -2)
    if printf '%s' "$_pars" | grep -q '|'; then
        report sacct-parse OK "parsable2 works: $(printf '%s' "$_pars" | head -1)"
    elif [ -n "$ACCT_DISABLED" ]; then
        report sacct-parse FAIL "accounting disabled -- parsable2 cannot be exercised" \
            "H-ACCT: same root cause as the sacct probe; there is nothing to parse yet"
    else
        report sacct-parse WARN "no '|'-separated output: $(printf '%s' "$_pars" | head -1)" \
            "H-ACCT: report this -- the platform parser must move to --parsable2"
    fi
else
    report sacct FAIL "sacct not installed" \
        "H-ACCT: install the Slurm client packages; without sacct the platform cannot resolve job status"
    report sacct-parse FAIL "sacct not installed" "H-ACCT: same as above"
fi

if have sacctmgr; then
    _assoc=$($TMO sacctmgr -n -P show assoc where user="$ME_USER" format=Cluster,Account,User,Partition 2>&1)
    _arc=$?
    _assoc1=$(printf '%s\n' "$_assoc" | head -1)
    if [ "$_arc" -eq 0 ] && [ -n "$_assoc" ]; then
        report assoc OK "association for $ME_USER: $_assoc1"
    elif [ -n "$ACCT_DISABLED" ]; then
        report assoc WARN "accounting disabled -- associations are not enforced" \
            "H-ACCT: no action; with accounting_storage/none submissions are not gated by association"
    else
        report assoc FAIL "no association for $ME_USER: $_assoc1" \
            "H-ASSOC: sacctmgr add user $ME_USER Account=<acct> -- required when accounting is enforced"
    fi
else
    report assoc FAIL "sacctmgr not installed" \
        "H-ASSOC: install slurmdbd/slurm-perlapi; associations gate submissions"
fi

# nginx is the sr-api identity -- the job is submitted as that user.
if id nginx >/dev/null 2>&1; then
    if have sacctmgr; then
        # Test accounting first: with accounting_storage/none, sacctmgr prints
        # "You are not running a supported accounting_storage plugin" and the
        # content check below would read that as a valid association (it is
        # non-empty and says neither "error" nor "not found").
        if [ -n "$ACCT_DISABLED" ]; then
            report assoc-nginx WARN "accounting disabled -- the nginx association is moot" \
                "H-ACCT: no action; association gating only applies once accounting is enabled"
        else
            _nassoc=$($TMO sacctmgr -n -P show assoc where user=nginx format=Cluster,Account,User 2>&1)
            _narc=$?
            if [ "$_narc" -eq 0 ] && [ -n "$_nassoc" ] \
               && ! printf '%s' "$_nassoc" | grep -qi 'error\|not found\|not supported\|disabled'; then
                report assoc-nginx OK "nginx association: $(printf '%s\n' "$_nassoc" | head -1)"
            else
                report assoc-nginx FAIL "no accounting association for user nginx" \
                    "H-ASSOC: sr-api runs as User=nginx -- without an association every submission is rejected"
            fi
        fi
    else
        report assoc-nginx FAIL "sacctmgr not installed; cannot check the nginx association" "H-ASSOC: see above"
    fi
else
    report assoc-nginx WARN "user nginx does not exist on this host" \
        "H-ASSOC: only relevant on the host that runs sr-api"
fi

# =============================================================================
# 8. path visibility
# =============================================================================

if [ -d /DiskArray ]; then
    report diskarray OK "/DiskArray visible ($(df -h /DiskArray 2>/dev/null | tail -1 | tr -s ' '))"
else
    report diskarray FAIL "/DiskArray is not mounted here" \
        "H-PATH: mount the array on every node that runs the job, and on the submit host"
fi

_bundle_bad=""
[ -d "$SR_BUNDLE_DIR" ] || _bundle_bad="$_bundle_bad missing-dir"
for _sub in models utils options util.py; do
    [ -e "$SR_BUNDLE_DIR/$_sub" ] || _bundle_bad="$_bundle_bad $_sub"
done
# The batch script cds here and runs the entry file by name; a bundle without
# one is not a bundle the platform can submit into.
_entry=$(ls "$SR_BUNDLE_DIR"/code_*_prod*.py 2>/dev/null | head -1)
[ -n "$_entry" ] || _bundle_bad="$_bundle_bad code_*_prod*.py(entry script)"
if [ -z "$_bundle_bad" ]; then
    report bundle OK "$SR_BUNDLE_DIR has models/ utils/ options/ util.py"
else
    report bundle FAIL "missing under $SR_BUNDLE_DIR:$_bundle_bad" \
        "H-PATH: point SR_BUNDLE_DIR at mmsr_bundle/codes (script + imports must live there)"
fi

_lib=$(ls "$SR_BUNDLE_DIR"/tools/ImgHistMatch.* 2>/dev/null | head -1)
if [ -n "$_lib" ]; then
    report bundle-lib OK "ImgHistMatch shared object present: $_lib"
else
    report bundle-lib WARN "no tools/ImgHistMatch.* under $SR_BUNDLE_DIR" \
        "H-PATH: fatal only for restormer ymls; the variant now degrades instead of crashing at import"
fi

if [ -f "$SR_OPTIONS_YML" ]; then
    report bundle-yml OK "OPT yml present: $SR_OPTIONS_YML"
else
    report bundle-yml WARN "OPT yml not found: $SR_OPTIONS_YML" \
        "H-PATH: point SR_OPTIONS_YML (and config.xml <OPT>) at a yml that exists"
fi

if [ -f "$SLURM_STOP_LOG" ]; then
    report stop-log OK "GPU-error log exists: $SLURM_STOP_LOG"
elif [ -d "$(dirname "$SLURM_STOP_LOG")" ]; then
    report stop-log WARN "$SLURM_STOP_LOG absent but its directory exists" \
        "H-PATH: harmless -- the variant only appends when the file already exists"
else
    report stop-log WARN "$(dirname "$SLURM_STOP_LOG") does not exist" \
        "H-PATH: the GPU-error log is skipped entirely; no action needed unless you rely on it"
fi

# =============================================================================
# 9. write permissions (two identities: nginx for sr-api, job user for output)
# =============================================================================

# 9a. sr-api (User=nginx) must write SR_SLURM_WORK_DIR and the SR_AGENT_DB dir.
_agentdb_dir=$(dirname "$SR_AGENT_DB")
for _pair in "write-work:$SR_SLURM_WORK_DIR" "write-db:$_agentdb_dir"; do
    _pid=${_pair%%:*}
    _ppath=${_pair#*:}
    can_write_as "$_ppath" nginx
    _w=$?
    case "$_w" in
        0) report "$_pid" OK "nginx can write $_ppath" ;;
        3) report "$_pid" FAIL "$_ppath does not exist" \
               "H-PERM: mkdir -p $_ppath && chown nginx:nginx $_ppath" ;;
        1) report "$_pid" FAIL "nginx CANNOT write $_ppath" \
               "H-PERM: chown nginx:nginx $_ppath (config.xml, batch scripts and the SQLite DB land here)" ;;
        *) report "$_pid" WARN "cannot test nginx write access without root" \
               "H-PERM: run  sudo -u nginx test -w $_ppath  and report the result" ;;
    esac
done

# 9b. the job user must write <lq_path> and <lq_path>/Debug/ (SRLOG + output tif).
_lq_sample=""
if [ -n "$SR_SCENES_ROOT" ] && [ -d "$SR_SCENES_ROOT" ]; then
    _lq_sample=$(ls -d "$SR_SCENES_ROOT"/*/ 2>/dev/null | head -1)
fi

if [ -z "$SR_SCENES_ROOT" ]; then
    report write-lq WARN "SR_SCENES_ROOT is unset on this host" \
        "H-PERM: set it on the sr-api host (must match the nginx.conf alias /data/scenes/)"
    report write-lq-debug WARN "not testable without a scene dir"
elif [ ! -d "$SR_SCENES_ROOT" ]; then
    report write-lq FAIL "SR_SCENES_ROOT=$SR_SCENES_ROOT does not exist here" \
        "H-PERM: point SR_SCENES_ROOT at the real scene root"
    report write-lq-debug WARN "not testable without a scene dir"
elif [ -z "$_lq_sample" ]; then
    report write-lq WARN "$SR_SCENES_ROOT contains no scene subdirectory" \
        "H-PERM: no sample scene to test against; re-run once the array is populated"
    report write-lq-debug WARN "not testable without a scene dir"
else
    can_write_as "$_lq_sample" ""
    [ $? -eq 0 ] && _lq_ok=1 || _lq_ok=0
    if [ "$_lq_ok" -eq 1 ]; then
        report write-lq OK "$ME_USER can write $_lq_sample"
    else
        report write-lq FAIL "$ME_USER CANNOT write $_lq_sample" \
            "H-PERM: the job user needs write access to the scene dir and its Debug/ subdir"
    fi
    if [ -d "${_lq_sample}Debug" ]; then
        can_write_as "${_lq_sample}Debug" ""
        [ $? -eq 0 ] && _dbg_ok=1 || _dbg_ok=0
        if [ "$_dbg_ok" -eq 1 ]; then
            report write-lq-debug OK "Debug/ exists and is writable in $_lq_sample"
        else
            report write-lq-debug FAIL "Debug/ is not writable in $_lq_sample" \
                "H-PERM: chown/chmod the per-scene Debug dir"
        fi
    else
        report write-lq-debug WARN "no Debug/ under $_lq_sample (created on first run)" \
            "H-PERM: fine as long as the parent directory is writable"
    fi
fi

# 9c. does the submit host have a usable client config?
if have sbatch; then
    if [ -r /etc/slurm/slurm.conf ] || [ -r /etc/slurm-llnl/slurm.conf ] || [ -n "${SLURM_CONF:-}" ]; then
        report submit-conf OK "client slurm.conf reachable (${SLURM_CONF:-/etc/slurm/slurm.conf})"
    else
        report submit-conf WARN "no readable slurm.conf and SLURM_CONF unset" \
            "H-PING: the client needs a slurm.conf that matches the controller's"
    fi
fi

# =============================================================================
# 10. versions
# =============================================================================

_ver=""
have sinfo && _ver="$(sinfo -V 2>&1 | head -1)"
have slurmd && _ver="$_ver | slurmd $(slurmd -V 2>&1 | head -1)"
if [ -n "$_ver" ]; then
    _osname=$(sed -n 's/^PRETTY_NAME="\(.*\)"/\1/p' /etc/os-release 2>/dev/null | head -1)
    report versions OK "$(printf '%s' "$_ver" | sed 's/^ | //') | $(uname -r) | ${_osname:-unknown OS}"
    show 'packages:' "$(rpm -qa 2>/dev/null | grep -i 'slurm\|munge' | head -8)"
else
    report versions FAIL "no Slurm binaries to version" "H-BIN: Slurm is not installed on this host"
fi

# =============================================================================
# 11. --deep: prove a --gres=gpu:1 job really gets one card
# =============================================================================

if [ "$DEEP" -eq 1 ]; then
    if ! have srun; then
        report deep-gres FAIL "srun is not available" "H-BIN: install the Slurm client packages"
        report deep-cvd FAIL "not reached"
    else
        printf '\n[deep] submitting a 2-minute --gres=gpu:1 job (may queue)...\n'
        _part=""
        [ -n "$SR_SLURM_PARTITION" ] && _part="-p $SR_SLURM_PARTITION"
        DEEP_CMD='echo hostname=$(hostname); echo user=$(id -un); echo SLURM_JOB_ID=$SLURM_JOB_ID; echo SLURM_JOB_GPUS=${SLURM_JOB_GPUS:-<unset>}; echo CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-<unset>}; nvidia-smi -L; nvidia-smi --query-gpu=index,uuid,name --format=csv,noheader'
        _dout=$($TMO_DEEP srun -N1 -n1 --gres=gpu:1 --time=00:02:00 $_part sh -c "$DEEP_CMD" 2>&1)
        _drc=$?
        show 'srun --gres=gpu:1 output:' "$_dout"
        if [ "$_drc" -eq 0 ]; then
            _cvd=$(printf '%s\n' "$_dout" | sed -n 's/^CUDA_VISIBLE_DEVICES=//p' | head -1)
            _gpus=$(printf '%s\n' "$_dout" | sed -n 's/^SLURM_JOB_GPUS=//p' | head -1)
            _ncards=$(printf '%s\n' "$_dout" | grep -c '^GPU ')
            if [ -z "$_cvd" ] || [ "$_cvd" = "<unset>" ]; then
                report deep-gres FAIL "job ran but CUDA_VISIBLE_DEVICES was not injected (SLURM_JOB_GPUS=$_gpus)" \
                    "H-DEEP: the GPU is not exposed to the job -- check GresTypes/SelectType and cgroup device access"
                report deep-cvd FAIL "not reached (no device list injected)" "H-DEEP: same as above"
            elif [ "$_ncards" -ge 1 ]; then
                report deep-gres OK "job got a GPU: CUDA_VISIBLE_DEVICES=$_cvd SLURM_JOB_GPUS=$_gpus, $_ncards card(s) visible in-job"
            else
                report deep-gres WARN "job ran with CUDA_VISIBLE_DEVICES=$_cvd but in-job nvidia-smi saw no card" \
                    "H-DEEP: report this output -- the allocation exists but the device is not usable"
            fi
            case "$_cvd" in
                [0-9]*) report deep-cvd OK "injected form is a plain integer ($_cvd) -- int(gpuid) works" ;;
                GPU-*)  report deep-cvd WARN "injected form is a UUID ($_cvd)" \
                            "H-DEEP: expected; the variant's E3 fallback handles it (gpuid=0 for logging only)" ;;
                *)      report deep-cvd WARN "injected form is '$_cvd' (neither integer nor UUID)" \
                            "H-DEEP: report this value; E3's int() fallback keeps the job alive" ;;
            esac
        else
            report deep-gres FAIL "srun --gres=gpu:1 rc=$_drc: $(printf '%s' "$_dout" | head -1)" \
                "H-DEEP: the allocation itself failed -- fix GRES/config first, then re-run with --deep"
            report deep-cvd FAIL "not reached (srun failed)" "H-DEEP: same as above"
        fi
    fi
fi

# =============================================================================
# summary + hints
# =============================================================================

printf '\n---- SUMMARY ----\n'
printf 'OK=%s WARN=%s FAIL=%s\n' "$OKS" "$WARNS" "$FAILS"
if [ "$DEEP" -ne 1 ]; then
    printf '(--deep NOT run: on-job GPU allocation is UNVERIFIED; re-run as: sh %s --deep)\n' "$0"
fi

if [ -n "$HINTS" ]; then
    printf '\n---- HINTS (one line per non-OK probe -> where to fix it) ----\n'
    printf '%s' "$HINTS" | while IFS= read -r _line; do
        [ -n "$_line" ] && printf '  %s\n' "$_line"
    done
else
    printf '\n---- HINTS ----\n  (none: every probe passed)\n'
fi

printf '\nHost: %s  User: %s  Time: %s\n' "$ME_LONG" "$ME_USER" "$(date 2>/dev/null)"
printf 'Paste this entire output back; the sections above are what the next batch needs.\n'

[ "$FAILS" -gt 0 ] && exit 1
exit 0
