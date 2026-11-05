#!/usr/bin/env bash
# ==========================================================================
# sr_agent_platform - read-only location probe (CentOS 7 / bash 4.2)
#
# Purpose: print the *effective* paths and permissions of the running
# sr-api service, the SR production runtime, the SR bundle and data dirs.
# It never writes to any checked directory; the only file it creates is the
# report under /tmp.
#
# Usage (as root):
#   bash deploy/probe_current_locations.sh
#   bash deploy/probe_current_locations.sh 2>&1 | less
#
# Exit code is always 0; read the report, not the exit code.
# ==========================================================================
set -u
LC_ALL=C
export LC_ALL

REPORT="/tmp/sr-location-probe-$(date +%Y%m%d-%H%M%S).txt"
exec > >(tee "$REPORT") 2>&1

MISS=0
WARN=0

section() { printf '\n========== %s ==========\n' "$*"; }
kv()      { printf '  %-30s %s\n' "$1" "$2"; }
miss()    { MISS=$((MISS + 1)); printf '  %-30s MISSING: %s\n' "$1" "$2"; }
warn()    { WARN=$((WARN + 1)); printf '  %-30s WARN: %s\n' "$1" "$2"; }

run_as_nginx() {
  if [ "$(id -u)" -eq 0 ]; then
    runuser -u nginx -- "$@"
  else
    sudo -n -u nginx -- "$@"
  fi
}

NGINX_OK=0
id -u nginx >/dev/null 2>&1 && NGINX_OK=1

nginx_read() {  # path -> yes/no
  [ "$NGINX_OK" -eq 1 ] || { echo "no-nginx-user"; return; }
  if run_as_nginx test -r "$1" >/dev/null 2>&1; then echo yes; else echo no; fi
}
nginx_write() {
  [ "$NGINX_OK" -eq 1 ] || { echo "no-nginx-user"; return; }
  if run_as_nginx test -w "$1" >/dev/null 2>&1; then echo yes; else echo no; fi
}
nginx_exec() {
  [ "$NGINX_OK" -eq 1 ] || { echo "no-nginx-user"; return; }
  if run_as_nginx test -x "$1" >/dev/null 2>&1; then echo yes; else echo no; fi
}

show_file() {  # path label [expected-sha256]
  local p=$1 label=$2 exp=${3:-}
  if [ ! -e "$p" ]; then
    miss "$label" "$p"
    return
  fi
  local sz sha
  sz=$(stat -c '%s' "$p" 2>/dev/null)
  sha=$(sha256sum "$p" 2>/dev/null | awk '{print $1}')
  kv "$label" "$p"
  kv "  size / sha256" "${sz:-?} / ${sha:-?}"
  kv "  nginx read" "$(nginx_read "$p")"
  if [ -n "$exp" ]; then
    if [ "$sha" = "$exp" ]; then
      kv "  sha vs repo" "MATCH"
    else
      kv "  sha vs repo" "DIFF (repo=$exp)"
    fi
  fi
}

show_dir() {  # path label
  local p=$1 label=$2
  if [ ! -d "$p" ]; then
    miss "$label" "$p"
    return
  fi
  kv "$label" "$p"
  kv "  nginx r/w/x" "$(nginx_read "$p")/$(nginx_write "$p")/$(nginx_exec "$p")"
  df -h "$p" 2>/dev/null | tail -n 1 | awk '{printf "  %-30s %s %s %s %s\n", "disk", $2, $3, $4, $5}'
}

# --------------------------------------------------------------------------
section "0. host"
kv "hostname" "$(hostname 2>/dev/null)"
kv "date" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
kv "whoami / uid" "$(id -un) / $(id -u)"
kv "os" "$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null | tr -d '\"')"
kv "nginx user exists" "$([ "$NGINX_OK" -eq 1 ] && echo yes || echo no)"
kv "report" "$REPORT"

# --------------------------------------------------------------------------
section "1. systemd unit"
UNIT=$(systemctl show sr-api -p FragmentPath 2>/dev/null | sed -n 's/^FragmentPath=//p')
[ -n "$UNIT" ] && kv "FragmentPath" "$UNIT" || miss "FragmentPath" "sr-api not found"
DROPINS=$(systemctl show sr-api -p DropInPaths 2>/dev/null | sed -n 's/^DropInPaths=//p')
kv "DropInPaths" "${DROPINS:-<none>}"
kv "is-active" "$(systemctl is-active sr-api 2>/dev/null)"
kv "WorkingDirectory" "$(systemctl show sr-api -p WorkingDirectory 2>/dev/null | sed -n 's/^WorkingDirectory=//p')"
kv "User / Group" "$(systemctl show sr-api -p User 2>/dev/null | sed -n 's/^User=//p') / $(systemctl show sr-api -p Group 2>/dev/null | sed -n 's/^Group=//p')"

EXEC_LINE=$(systemctl show sr-api -p ExecStart 2>/dev/null)
EXEC_PATH=$(printf '%s' "$EXEC_LINE" | sed -n 's/.*path=\([^ ;]*\).*/\1/p')
EXEC_ARGV=$(printf '%s' "$EXEC_LINE" | sed -n 's/.*argv\[\]=\([^;]*\).*/\1/p')
kv "ExecStart path" "${EXEC_PATH:-?}"
kv "ExecStart argv" "${EXEC_ARGV:-?}"
PORT=$(printf '%s' "$EXEC_LINE" | sed -n 's/.*--port[= ]\([0-9][0-9]*\).*/\1/p')
PORT=${PORT:-8000}
kv "listen port" "$PORT"

# --------------------------------------------------------------------------
section "2. effective environment (systemctl show, merged)"
ENV_DUMP=$(systemctl show sr-api -p Environment 2>/dev/null | sed 's/^Environment=//')
get_env() {
  printf '%s\n' "$ENV_DUMP" | tr ' ' '\n' \
    | sed -n "s/^${1}=//p" | head -n 1 | sed 's/^"//; s/"$//'
}

APP=$(systemctl show sr-api -p WorkingDirectory 2>/dev/null | sed -n 's/^WorkingDirectory=//p' | sed 's/ (root)$//')
[ -n "$APP" ] && kv "APP (WorkingDirectory)" "$APP" || warn "APP" "WorkingDirectory unset"

ENV_KEYS="SR_EXECUTOR SR_PYTHON SR_BUNDLE_DIR SR_SLURM_WORK_DIR SR_SR_SCRIPT SR_VERIFY_SCRIPT SR_LOCAL_GPU SR_SCENES_ROOT SR_AGENT_DB SR_PREVIEWS_ROOT SR_PREVIEW_ROOTS SR_PREVIEW_CACHE_ROOT SR_TEMP_PREVIEWS_ROOT SR_LOCKED_DIR SR_SANDBOX_ROOT SR_ALLOWED_ROOTS SR_DRIVE_MAP SR_SCENE_PATH_TEMPLATE SR_LLM_BASE_URL SR_LLM_MODEL SR_LLM_MAX_TOKENS SR_LLM_MOCK SR_PRODUCT_PREVIEW_DIV"
for k in $ENV_KEYS; do
  v=$(get_env "$k")
  if [ -n "$v" ]; then
    kv "$k" "$v"
  else
    kv "$k" "<unset: code default>"
  fi
done

[ "$(get_env SR_EXECUTOR)" = "local" ] || warn "SR_EXECUTOR" "not 'local'; jobs may use the frozen scheduler branch"
[ -n "$(get_env SR_SR_SCRIPT)" ] || warn "SR_SR_SCRIPT" "unset; jobs run production code_0817_prod.py"
[ -n "$(get_env SR_VERIFY_SCRIPT)" ] || warn "SR_VERIFY_SCRIPT" "unset; bundle verifier name defaults to verify_sr_run.py"

# --------------------------------------------------------------------------
section "3. API runtime"
if [ -n "$EXEC_PATH" ] && [ -x "$EXEC_PATH" ]; then
  API_BIN="$(dirname "$EXEC_PATH")/python"
  [ -x "$API_BIN" ] || API_BIN="$EXEC_PATH"
else
  API_BIN=/opt/sr-venv/bin/python
fi
if [ -x "$API_BIN" ]; then
  kv "API python" "$API_BIN"
  kv "  version" "$("$API_BIN" -c 'import sys; print(sys.version.split()[0])' 2>&1)"
  kv "  nginx exec" "$(nginx_exec "$API_BIN")"
  PKGS=$("$API_BIN" -c 'import fastapi,uvicorn,openai,numpy,PIL; print(fastapi.__version__, uvicorn.__version__, openai.__version__, numpy.__version__, PIL.__version__)' 2>&1)
  kv "  packages" "$PKGS"
  if [ -n "$APP" ] && [ -d "$APP/backend" ]; then
    IMP=$(cd "$APP" && timeout 60 "$API_BIN" -c 'from backend.api.app import create_app; print("import ok")' 2>&1 | tail -n 1)
    kv "  app import" "$IMP"
    show_file "$APP/backend/requirements-api.txt" "requirements-api"
    show_file "$APP/backend/check_runtime.py" "check_runtime"
  else
    miss "APP/backend" "${APP:-<APP unset>}/backend"
  fi
else
  miss "API python" "$API_BIN"
fi

if [ -n "$APP" ]; then
  for d in dist backend SR_code SR_code/variants; do
    show_dir "$APP/$d" "APP/$d"
  done
fi

# --------------------------------------------------------------------------
section "4. SR production runtime"
SR_PY=$(get_env SR_PYTHON)
[ -n "$SR_PY" ] || { SR_PY=$(command -v python 2>/dev/null); warn "SR_PYTHON" "unset; falling back to ${SR_PY:-not found}"; }
if [ -x "$SR_PY" ]; then
  kv "SR python" "$SR_PY"
  kv "  nginx exec" "$(nginx_exec "$SR_PY")"
  PYOUT=$(timeout 120 "$SR_PY" -c 'import sys; print(sys.version.split()[0]); import torch, osgeo; print(torch.__version__, osgeo.__version__)' 2>&1 | tail -n 2 | tr '\n' ' ')
  kv "  py / torch / gdal" "$PYOUT"
else
  miss "SR python" "$SR_PY"
fi

BUNDLE=$(get_env SR_BUNDLE_DIR)
if [ -n "$BUNDLE" ]; then
  kv "SR_BUNDLE_DIR" "$BUNDLE (from env)"
else
  BUNDLE=/DiskArray/ProductionSchedule/exe_CentOS7/SR_bundle/mmsr_bundle/codes
  warn "SR_BUNDLE_DIR" "unset; probing documented default $BUNDLE"
fi
show_dir "$BUNDLE" "SR bundle"
if [ -d "$BUNDLE" ]; then
  if [ -f "$BUNDLE/code_0817_prod.py" ]; then
    show_file "$BUNDLE/code_0817_prod.py" "production script"
  else
    kv "production script" "<absent: SR_SR_SCRIPT points at the variant>"
  fi
  show_file "$BUNDLE/code_0817_prod_slurm.py" "platform variant" "041bea73b36f6e3c8289d4e4c029c5fe492cbd09502a178c5f1d9798287474a9"
  show_file "$BUNDLE/verify_sr_run.py" "bundle verifier" "e3eb2fc560489117e3f9ed8980973f059eec07d757e0194a17b539bdc095aa4a"
  for f in sfsr_confgig_test_espan2_cuda1.xml sfsr_config_espan2_cuda1.xml; do
    [ -e "$BUNDLE/$f" ] && kv "suffix config" "$BUNDLE/$f"
  done
  for d in models utils options; do
    [ -d "$BUNDLE/$d" ] && kv "bundle dir" "$BUNDLE/$d"
  done
fi

if [ -n "$APP" ]; then
  show_file "$APP/SR_code/variants/repair_sr.py" "repair adapter" "f301d2341d4863c9b1924f305796342fde1cbe99493b829e109979de2079fd35"
  show_file "$APP/SR_code/variants/verify_sr_run.py" "app verifier" "e3eb2fc560489117e3f9ed8980973f059eec07d757e0194a17b539bdc095aa4a"
  if [ -f "$APP/SR_code/variants/code_0817_prod_slurm.py" ]; then
    show_file "$APP/SR_code/variants/code_0817_prod_slurm.py" "app variant copy" "041bea73b36f6e3c8289d4e4c029c5fe492cbd09502a178c5f1d9798287474a9"
  else
    kv "app variant copy" "<not required: bundle copy is what matters>"
  fi
fi

# --------------------------------------------------------------------------
section "5. data directories"
WORK=$(get_env SR_SLURM_WORK_DIR)
[ -n "$WORK" ] || { WORK=/tmp/sr_agent_work; warn "SR_SLURM_WORK_DIR" "unset; probing default $WORK"; }
show_dir "$WORK" "work dir"
if [ -d "$WORK" ]; then
  if [ -f "$WORK/.local_job_seq" ]; then
    kv "job sequence" "$WORK/.local_job_seq"
    kv "  last seq" "$(cat "$WORK/.local_job_seq" 2>/dev/null)"
  else
    kv "job sequence" "<not created yet>"
  fi
  LATEST_XML=$(ls -1t "$WORK"/run_sr_*.xml 2>/dev/null | head -n 1)
  if [ -n "$LATEST_XML" ]; then
    kv "latest config.xml" "$LATEST_XML"
    LQ=$(sed -n 's:.*<DatarootLQ>\(.*\)</DatarootLQ>.*:\1:p' "$LATEST_XML" | head -n 1)
    kv "  DatarootLQ" "${LQ:-?}"
    if [ -n "$LQ" ] && [ -d "$LQ/Debug" ]; then
      LAST_V=$(ls -1t "$LQ"/Debug/_SREXIT_*.txt 2>/dev/null | head -n 1)
      kv "  latest verdict" "${LAST_V:-<none>}"
      [ -n "$LAST_V" ] && sed 's/^/    /' "$LAST_V"
    fi
  fi
fi

DB=$(get_env SR_AGENT_DB)
if [ -n "$DB" ]; then
  kv "SR_AGENT_DB" "$DB"
  show_dir "$(dirname "$DB")" "db parent"
  [ -f "$DB" ] && show_file "$DB" "db file" || warn "db file" "not created yet"
else
  warn "SR_AGENT_DB" "unset; would default to <cwd>/sr_agent.db"
fi

SCENES=$(get_env SR_SCENES_ROOT)
if [ -n "$SCENES" ]; then
  show_dir "$SCENES" "scenes root"
else
  miss "SR_SCENES_ROOT" "unset"
fi

LOCKED=$(get_env SR_LOCKED_DIR)
if [ -n "$LOCKED" ]; then
  show_dir "$LOCKED" "locked dir"
  if [ -d "$LOCKED/Debug" ]; then
    kv "  verdict count" "$(ls -1 "$LOCKED"/Debug/_SREXIT_*.txt 2>/dev/null | wc -l)"
    LAST_V=$(ls -1t "$LOCKED"/Debug/_SREXIT_*.txt 2>/dev/null | head -n 1)
    [ -n "$LAST_V" ] && { kv "  latest verdict" "$LAST_V"; sed 's/^/    /' "$LAST_V"; }
  fi
else
  kv "SR_LOCKED_DIR" "<unset: any allowed path is accepted>"
fi

for key in SR_PREVIEWS_ROOT SR_TEMP_PREVIEWS_ROOT SR_PREVIEW_CACHE_ROOT; do
  d=$(get_env "$key")
  [ -n "$d" ] && show_dir "$d" "$key" || kv "$key" "<unset>"
done

ROOTS=$(get_env SR_PREVIEW_ROOTS)
if [ -n "$ROOTS" ]; then
  IFS=';' read -r -a ROOT_ARR <<< "$ROOTS"
  for d in "${ROOT_ARR[@]}"; do
    [ -n "$d" ] && show_dir "$d" "preview-only root"
  done
else
  kv "SR_PREVIEW_ROOTS" "<unset: external previews disabled>"
fi

# --------------------------------------------------------------------------
section "6. nginx"
if command -v nginx >/dev/null 2>&1; then
  kv "nginx -t" "$(nginx -t 2>&1 | tail -n 1)"
  CONF=/etc/nginx/conf.d/sr-agent-platform.conf
  if [ -f "$CONF" ]; then
    kv "site conf" "$CONF"
    grep -nE '^\s*(root|alias|proxy_pass|location|listen|server_name)' "$CONF" 2>/dev/null | sed 's/^/    /'
  else
    warn "site conf" "$CONF not found; nginx -T | grep for alias/root"
    nginx -T 2>/dev/null | grep -nE 'server_name|root |alias |proxy_pass' | head -n 20 | sed 's/^/    /'
  fi
else
  warn "nginx" "command not found"
fi
STATIC=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1/ 2>/dev/null)
kv "static / http code" "${STATIC:-000}"

# --------------------------------------------------------------------------
section "7. sr-api health"
HEALTH=$(curl -s -m 5 "http://127.0.0.1:${PORT}/api/health" 2>/dev/null)
kv "GET /api/health" "${HEALTH:-<no response>}"
kv "systemctl status" "$(systemctl is-active sr-api 2>/dev/null) / $(systemctl is-enabled sr-api 2>/dev/null)"
printf '\n  --- journalctl -u sr-api (last 20) ---\n'
journalctl -u sr-api -n 20 --no-pager 2>/dev/null | sed 's/^/    /'

# --------------------------------------------------------------------------
section "8. summary"
kv "report" "$REPORT"
kv "missing items" "$MISS"
kv "warnings" "$WARN"
printf '\n  Read the sections above. Missing items are paths the running\n'
printf '  service, the SR runtime or the repo delivery expects but did\n'
printf '  not find. Warnings are values that fell back to a default.\n'
exit 0
