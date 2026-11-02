#!/usr/bin/env bash
# ==========================================================================
# apply_bundle_verifier.sh - sync the SR contract verifier into the bundle
#
# Why a script: pasting a long block into the server terminal wraps and
# truncates lines, so every step is checked and reported here instead.
#
# Modes:
#   (default)  back up the bundle verifier, install the app-side one,
#              verify the hash and print the SR script env names.
#   --check    compare only; no file is written.
#   --report   inspect the newest SR job: verdict file, repair receipt,
#              PAN byte size and dimensions.
#
# Run as root on node81-135:
#   bash /tmp/apply_bundle_verifier.sh
#   bash /tmp/apply_bundle_verifier.sh --check
#   bash /tmp/apply_bundle_verifier.sh --report
#
# Paths can be overridden:
#   APP=... BUNDLE=... WORK=... bash /tmp/apply_bundle_verifier.sh
#
# This script only touches $BUNDLE/verify_sr_run.py and one .bak-* copy of
# it. It never writes to scenes, the database or the application tree.
# ==========================================================================
set -u
LC_ALL=C
export LC_ALL

APP=${APP:-/run/media/root/SSD/workspace/wangrz/sr-agent-platform}
BUNDLE=${BUNDLE:-/DiskArray/tmp/wangrz/mmsr_bundle_240617/codes}
WORK=${WORK:-/DiskArray/tmp/wangrz/sr_agent_work}

SRC="$APP/SR_code/variants/verify_sr_run.py"
SRC_REPAIR="$APP/SR_code/variants/repair_sr.py"
DST="$BUNDLE/verify_sr_run.py"

EXPECT_VERIFY=e3eb2fc560489117e3f9ed8980973f059eec07d757e0194a17b539bdc095aa4a
EXPECT_REPAIR=9b93c427db0f80779b5e67e6d1ac0d09507887ad191e14768ca5797f6723c081

say() { printf '%s\n' "$*"; }
hr()  { printf '%s\n' "------------------------------------------------------------"; }
sha() { sha256sum "$1" 2>/dev/null | awk '{print $1}'; }

need_file() {
  if [ ! -f "$1" ]; then
    echo "MISSING: $1" >&2
    return 1
  fi
}

env_check() {
  hr
  say "SR script env (expect SR_SR_SCRIPT=code_0817_prod_slurm.py):"
  systemctl show sr-api -p Environment 2>/dev/null | tr ' ' '\n' \
    | grep -E '^SR_(SR|VERIFY)_SCRIPT=' || say "  <SR_SR_SCRIPT / SR_VERIFY_SCRIPT not found>"
  hr
  say "Next: submit one job from the page, then run:"
  say "  bash $0 --report"
}

check_mode() {
  local rc=0 s t
  hr
  say "source: $SRC"
  if need_file "$SRC"; then
    s=$(sha "$SRC")
    say "  sha : $s"
    if [ "$s" != "$EXPECT_VERIFY" ]; then
      say "  DIFF: expected $EXPECT_VERIFY"
      rc=1
    else
      say "  OK"
    fi
  else
    rc=1
  fi

  hr
  say "target: $DST"
  if [ -f "$DST" ]; then
    t=$(sha "$DST")
    say "  size: $(stat -c '%s' "$DST" 2>/dev/null)"
    say "  sha : $t"
    if [ "$t" != "$EXPECT_VERIFY" ]; then
      say "  DIFF: expected $EXPECT_VERIFY (run without --check to install)"
      rc=1
    else
      say "  OK"
    fi
  else
    say "  MISSING (run without --check to install)"
    rc=1
  fi

  hr
  say "repair adapter (not installed by this script): $SRC_REPAIR"
  if [ -f "$SRC_REPAIR" ]; then
    s=$(sha "$SRC_REPAIR")
    say "  sha : $s"
    if [ "$s" != "$EXPECT_REPAIR" ]; then
      say "  DIFF: expected $EXPECT_REPAIR (backend package not deployed yet?)"
      rc=1
    else
      say "  OK"
    fi
  else
    say "  MISSING"
    rc=1
  fi
  return $rc
}

apply_mode() {
  local s t bak
  need_file "$SRC" || exit 1

  s=$(sha "$SRC")
  if [ "$s" != "$EXPECT_VERIFY" ]; then
    echo "refusing to install: source sha $s != $EXPECT_VERIFY" >&2
    exit 1
  fi
  say "source : $SRC"
  say "  sha  : $s"

  if [ -f "$SRC_REPAIR" ]; then
    if [ "$(sha "$SRC_REPAIR")" != "$EXPECT_REPAIR" ]; then
      echo "WARN: repair_sr.py sha differs from $EXPECT_REPAIR" >&2
    fi
  else
    echo "WARN: repair_sr.py is missing; repair jobs cannot run" >&2
  fi

  hr
  if [ -f "$DST" ]; then
    t=$(sha "$DST")
    say "target : $DST"
    say "  size : $(stat -c '%s' "$DST" 2>/dev/null)"
    say "  sha  : $t"
    if [ "$t" = "$EXPECT_VERIFY" ]; then
      say "  already current; nothing to write"
      env_check
      return 0
    fi
    bak="$DST.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$DST" "$bak" || { echo "backup failed" >&2; exit 1; }
    say "backup : $bak"
  else
    say "target : $DST (missing, installing fresh)"
  fi

  install -m 0644 "$SRC" "$DST" || { echo "install failed" >&2; exit 1; }
  t=$(sha "$DST")
  if [ "$t" != "$EXPECT_VERIFY" ]; then
    echo "post-install sha mismatch: $t != $EXPECT_VERIFY" >&2
    exit 1
  fi
  say "installed: $DST"
  say "  sha   : $t"
  env_check
}

report_mode() {
  local xml lq v r py
  xml=$(ls -1t "$WORK"/run_sr_*.xml 2>/dev/null | head -n 1)
  if [ -z "$xml" ]; then
    echo "no run_sr_*.xml under $WORK" >&2
    return 1
  fi
  lq=$(sed -n 's:.*<DatarootLQ>\(.*\)</DatarootLQ>.*:\1:p' "$xml" | head -n 1)
  hr
  say "config : $xml"
  say "scene  : $lq"
  if [ -z "$lq" ] || [ ! -d "$lq" ]; then
    echo "scene directory not found" >&2
    return 1
  fi

  hr
  v=$(ls -1t "$lq"/Debug/_SREXIT_*.txt 2>/dev/null | head -n 1)
  if [ -n "$v" ]; then
    say "verdict: $v"
    sed 's/^/  /' "$v"
  else
    say "verdict: <none> (job not finished, or the verifier did not write it)"
  fi

  hr
  r=$(ls -1t "$lq"/Debug/_SRREPAIR_*.json 2>/dev/null | head -n 1)
  if [ -z "$r" ]; then
    say "receipt: <none> - this job did NOT go through the repair path"
    say "Do not accept a verdict from this job as proof of the repair flow."
    return 0
  fi
  say "receipt: $r"

  if [ -x /opt/sr-venv/bin/python ]; then
    py=/opt/sr-venv/bin/python
  elif command -v python3 >/dev/null 2>&1; then
    py=python3
  else
    echo "no python3 available; print the receipt manually" >&2
    return 0
  fi

  "$py" - "$lq" "$r" <<'PY'
import json, os, sys
scene, receipt = sys.argv[1], sys.argv[2]
with open(receipt) as stream:
    data = json.load(stream)
pan = os.path.join(scene, 'PAN.tif')
if not os.path.isfile(pan):
    print('PAN missing:', pan)
    sys.exit(1)
size = os.path.getsize(pan)
print('mode        :', data.get('mode'))
print('PAN bytes   :', size)
print('receipt size:', data.get('size_bytes'), 'match:', size == data.get('size_bytes'))
print('dimensions  :', data.get('width'), 'x', data.get('height'))
print('backup      :', data.get('backup'), os.path.exists(data.get('backup') or ''))
print('nosr        :', data.get('nosr'), os.path.exists(data.get('nosr') or ''))
PY
}

case "${1:-}" in
  "")        apply_mode ;;
  --check)   check_mode ;;
  --report)  report_mode ;;
  -h|--help)
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    echo "unknown option: $1 (use --check, --report or no option)" >&2
    exit 2
    ;;
esac
