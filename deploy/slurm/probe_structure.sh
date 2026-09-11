#!/bin/sh
# ============================================================================
# 阶段 0 · 盘阵结构核查（只读，不写任何数据文件）
#
# 产物 = 若干**短** txt，落在**本脚本所在目录**，一屏一个，便于逐张截屏 / OCR：
#     00-summary.txt     ← 最重要，判读要点全在这一张
#     01-tree.txt        盘阵顶层 + 逐层目录数
#     02-scene-dir.txt   找到的场景目录 + 目录内清单 + 一层子目录
#     03-inputs.txt      栅格清单 + SR 输入判据逐条比对
#     04-meta-debug.txt  meta.xml 与 Debug/
#     05-step.txt        RC 步还是 SC 步 + 元数据取值
#     06-manifest.txt    产物清单（行数），用来确认没被截断
#
# 用法（在 node81-135 上）：
#     sh probe_structure.sh
#
# 可覆盖的环境变量：
#     SR_PROD=/DiskArray/GSHC2IMPS      盘阵根
#     SR_D=/DiskArray/.../<生产编号>     手工指定场景目录（跳过自动发现）
#     SR_CHECK_OUT=/tmp/xxx             产物目录（默认 = 本脚本所在目录）
#
# 输出里 @PROD@ 代指上面的 PROD 路径（缩短行宽，方便 OCR）。
# 不依赖 jq / python；缺 Slurm、缺 conda 也能跑完。
# ============================================================================

PROD=${SR_PROD:-/DiskArray/GSHC2IMPS}
SR_PYTHON_PROD=/run/media/root/SSD/program/anaconda/installed/envs/torch1.9.1py36/bin/python
TAG='@PROD@'

# ---- 产物目录 = 脚本所在目录 ----------------------------------------------
case "$0" in
  */*) OUT=$(cd "$(dirname "$0")" 2>/dev/null && pwd) ;;
  *)   OUT="" ;;
esac
[ -n "$OUT" ] && [ -d "$OUT" ] || OUT=$(pwd)
OUT=${SR_CHECK_OUT:-$OUT}
mkdir -p "$OUT" 2>/dev/null || OUT=/tmp
mkdir -p "$OUT" 2>/dev/null

# 把 PROD 前缀换成 @PROD@：行更短、OCR 更准
sh_paths() { sed "s#${PROD}#${TAG}#g"; }

# 再去掉 D/ 前缀（只留文件名）。D 为空时原样输出 —— 否则会把所有斜杠都吃掉。
# 用在「D 内清单 / 栅格清单 / 判据比对」这些 D 已在上方打印过的地方，行宽能少一大截。
strip_d() {
  if [ -n "$D" ]; then sed "s#${D}/##g"; else cat; fi
}

open_f() {          # $1 = 文件名  $2 = 这一段回答什么问题
  F="$OUT/$1"
  {
    echo "===== $1 :: $2 ====="
    echo "PROD = $PROD    (@PROD@ 代指此路径)"
    echo "时间 = $(date '+%Y-%m-%d %H:%M:%S')"
    echo "----------------------------------------"
  } > "$F"
}
close_f() {         # $1 = 文件名
  {
    echo "----------------------------------------"
    echo "----- EOF $1  共 $(wc -l < "$OUT/$1" | tr -d ' ') 行 -----"
  } >> "$OUT/$1"
}

# ============================================================================
# 1) 顶层 + 层级形状
# ============================================================================
open_f 01-tree.txt "盘阵顶层长什么样；层级是几层"
{
  echo "[PROD 是否存在]"
  ls -d "$PROD" 2>&1
  echo
  echo "[PROD 顶层内容，最多 25 行]"
  ls -la "$PROD" 2>&1 | sh_paths | head -25
  echo
  echo "[逐层目录数：深度 N 表示 PROD 之下第 N 层]"
  n=1
  while [ $n -le 6 ]; do
    g="$PROD"; i=0
    while [ $i -lt $n ]; do g="$g/*"; i=$((i+1)); done
    out=$(ls -d $g/ 2>/dev/null | head -1000)
    cnt=$(printf '%s\n' "$out" | grep -c .)
    cap=""; [ "$cnt" -ge 1000 ] && cap="+"
    # 例只取一个并截到 58 字符：深层例子（PRODUCT/年/月/日/卫星/…）否则会把行拉到 120+
    ex=$(printf '%s\n' "$out" | head -1 | sh_paths | cut -c1-58)
    echo "深度 $n : ${cnt}${cap} 个目录 / 例: $ex"
    n=$((n+1))
  done
} >> "$OUT/01-tree.txt"
close_f 01-tree.txt

# ============================================================================
# 2) 找场景目录（含 *_meta.xml）+ 目录内清单
# ============================================================================
# meta 在生产形如 <根>/PRODUCT/<年>/<月>/<日>/<卫星>/<编号>/<编号>_meta.xml，
# 即 PROD 之下第 7 层，故 maxdepth 放到 8。
# ⚠️ 用 *meta.xml 而不是 *_meta.xml：真机上两种拼法都见过 —— PRODUCT 下是
#    <目录名>_meta.xml，而 2026-09-10 实测的一个 L1_PAN 目录里是 <目录名>.meta.xml
#    （点，不是下划线）。只认一种会误报「meta 不存在」。
META_LIST=$(find "$PROD" -maxdepth 8 -name '*meta.xml' 2>/dev/null | head -5)
D="${SR_D:-}"
FOUND_BY="手工指定 SR_D"
if [ -n "$D" ] && [ ! -d "$D" ]; then
  echo "!! SR_D=$D 不是目录，忽略" >&2
  D=""
fi
if [ -z "$D" ]; then
  first=$(printf '%s\n' "$META_LIST" | head -1)
  [ -n "$first" ] && D="${first%/*}"
  FOUND_BY="find -maxdepth 8"
fi
if [ -z "$D" ] || [ ! -d "$D" ]; then
  D=""
  FOUND_BY="逐层 glob 试探"
  n=4
  while [ $n -le 8 ] && [ -z "$D" ]; do
    g="$PROD"; i=0
    while [ $i -lt $n ]; do g="$g/*"; i=$((i+1)); done
    for cand in $(ls -d $g/ 2>/dev/null | head -50); do
      cand="${cand%/}"
      if ls "$cand"/*meta.xml >/dev/null 2>&1; then D="$cand"; break; fi
    done
    n=$((n+1))
  done
  [ -z "$D" ] && FOUND_BY="没找到"
fi

BASE=""
[ -n "$D" ] && BASE=$(basename "$D")

# 层级形状：计划里写的是 <根>/<年>/<月>/<日>/<生产编号>，但同仓库 CloudReview 的
# core.py:2056 与 SR 测试 config 的注释路径都是 <根>/GSHC2IMPS/PRODUCT/<年>/…/…
# —— 多一层 PRODUCT。这里两条都探，判读页直接给结论，不用再猜。
PRODUCT_LAYER="无"
if [ -d "$PROD/PRODUCT" ]; then
  PRODUCT_LAYER="有  ($(printf '%s' "$PROD/PRODUCT" | sh_paths))"
elif [ -d "$PROD/PRODUCT/PRODUCT" ]; then
  PRODUCT_LAYER="有（双层）"
fi
# meta 相对 PROD 的层数 —— 最直接回答「E-D 该在第几层收件」
EX1=$(printf '%s\n' "$META_LIST" | head -1)
if [ -n "$EX1" ]; then
  META_DEPTH="PROD 之下第 $(printf '%s' "${EX1#$PROD/}" | awk -F/ '{print NF}') 层"
  # 只留文件名（相对 D）：完整相对路径动辄 130+ 字符，一屏截不下、OCR 也易错。
  # ⚠️ 顺序必须是 strip_d 在前：sh_paths 会把 PROD 换成 @PROD@，那之后字符串里
  #    就再也找不到 $D 的前缀，strip_d 的 sed 会空转（踩过）。
  META_EXAMPLE=$(printf '%s' "$EX1" | strip_d | sh_paths)
else
  META_DEPTH="（PROD 之下 8 层内没扫到 *meta.xml）"
  META_EXAMPLE=""
fi
# 层数只在 D 确实位于 PROD 之下时才有意义（SR_D 可能指到 PROD 之外）
LEVEL=""
case "$D" in
  "$PROD"/*) LEVEL="PROD 之下第 $(printf '%s' "${D#$PROD/}" | awk -F/ '{print NF}') 层" ;;
  "")        LEVEL="（没找到目录）" ;;
  *)         LEVEL="（不在 PROD 之下）" ;;
esac

open_f 02-scene-dir.txt "找到的场景目录是哪个；它里面有什么；有没有更深一层"
{
  echo "[自动发现方式] $FOUND_BY"
  echo "[候选 meta（最多 5 个）]（落在 D 里的只留文件名，其余缩短 PROD）"
  printf '%s\n' "$META_LIST" | strip_d | sh_paths | head -5
  echo
  if [ -z "$D" ]; then
    echo "!! 没找到任何含 *meta.xml 的场景目录。"
    echo "!! 请手挑一个生产编号目录后重跑："
    echo "!!   SR_D=<该目录绝对路径> sh $0"
  else
    echo "[选中 D] $D" | sh_paths
    echo "[D 的层级] $LEVEL"
    echo
    echo "[D 内清单，最多 30 行]（已去掉 D/ 前缀，只留文件名）"
    ls -la "$D" 2>&1 | strip_d | sh_paths | head -30
    echo
    echo "[D 的一层子目录，最多 10 个]"
    subs=$(find "$D" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | head -10)
    # strip_d：D 上面已打印过，这里留目录名即可（内层名 76 字符，带全路径会到 136 宽）
    printf '%s\n' "$subs" | strip_d | sh_paths
    if [ -n "$subs" ]; then
      echo
      echo "[每个子目录自身是不是一个场景（有 _meta.xml/.meta.xml，或有 tif）]"
      for sd in $subs; do
        sb=$(basename "$sd")
        m="无meta"
        [ -e "$sd/${sb}_meta.xml" ] && m="有${sb}_meta.xml"
        [ -e "$sd/${sb}.meta.xml" ] && m="${m} 有${sb}.meta.xml"
        t=""; [ -e "$sd/${sb}.tif" ] && t="有${sb}.tif"
        [ -e "$sd/PAN.tif" ] && t="$t 有PAN.tif"
        echo "  $sb : $m ${t:-无同名tif}"
      done
    fi
  fi
} >> "$OUT/02-scene-dir.txt"
close_f 02-scene-dir.txt

# ============================================================================
# 3) SR 输入判据逐条比对（SC 步要 <目录名>.tif，RC 步要 PAN.tif）
# ============================================================================
HAS_SC="?"; HAS_RC="?"; HAS_META="?"; HAS_DEBUG="?"; N_TIF="?"
open_f 03-inputs.txt "SR 判定的输入文件到底在不在（util.py:989-991）"
{
  if [ -z "$D" ]; then
    echo "!! 没找到场景目录，本节跳过（见 02-scene-dir.txt）"
  else
    echo "[目录名 BASE] $BASE"
    echo
    echo "[目录内栅格，最多 20 行]（已去掉 D/ 前缀）"
    ls -la "$D"/*.tif "$D"/*.tiff "$D"/*.img 2>/dev/null | strip_d | head -20
    echo "(以上为空 = 目录里没有任何 tif/tiff/img)"
    N_TIF=$(ls "$D"/*.tif "$D"/*.tiff "$D"/*.img 2>/dev/null | wc -l | tr -d ' ')
    echo "栅格数 = $N_TIF"
    echo
    echo "[判据逐条比对]（文件名相对 D）"
    for f in "$D/$BASE.tif" "$D/PAN.tif" "$D/$BASE.tiff" "$D/$BASE.img"; do
      if [ -e "$f" ]; then
        echo "存在   ${f#$D/}   ($(du -h "$f" 2>/dev/null | cut -f1))"
      else
        echo "不存在 ${f#$D/}"
      fi
    done
  fi
} >> "$OUT/03-inputs.txt"
close_f 03-inputs.txt
[ -n "$D" ] && {
  [ -e "$D/$BASE.tif" ] && HAS_SC="存在" || HAS_SC="不存在"
  [ -e "$D/PAN.tif" ] && HAS_RC="存在" || HAS_RC="不存在"
}

# ============================================================================
# 4) meta 与 Debug
# ============================================================================
open_f 04-meta-debug.txt "meta.xml 与 Debug/ 在不在（两种拼法都查）"
{
  if [ -z "$D" ]; then
    echo "!! 没找到场景目录，本节跳过（见 02-scene-dir.txt）"
  else
    echo "[目录内的 *meta.xml，最多 10 行]（已去掉 D/ 前缀）"
    ls -la "$D"/*meta.xml 2>&1 | strip_d | head -10
    echo
    # 两种拼法都查：PRODUCT 下见过 <目录名>_meta.xml，L1_PAN 目录里见过 <目录名>.meta.xml
    if [ -e "$D/${BASE}_meta.xml" ]; then
      echo "SR 要的名字 <目录名>_meta.xml : 存在"
      HAS_META="存在(_meta.xml)"
    elif [ -e "$D/${BASE}.meta.xml" ]; then
      echo "SR 要的名字 <目录名>.meta.xml : 存在（点，不是下划线）"
      HAS_META="存在(.meta.xml)"
    else
      echo "SR 要的两种名字都 <不存在>  <<< 注意"
      HAS_META="不存在"
    fi
    echo
    echo "[Debug/，最多 15 行]（已去掉 D/ 前缀）"
    if [ -d "$D/Debug" ]; then
      HAS_DEBUG="存在"
      ls -la "$D/Debug" 2>&1 | strip_d | head -15
    else
      HAS_DEBUG="不存在"
      echo "（没有 Debug/ 子目录）"
    fi
  fi
} >> "$OUT/04-meta-debug.txt"
close_f 04-meta-debug.txt

# ============================================================================
# 5) RC 步还是 SC 步
# ============================================================================
STEP="?"; CLOUD="?"; BITS="?"
open_f 05-step.txt "SolarAzimuth 空 → RC 步；非空 → SC 步"
{
  if [ -z "$D" ]; then
    echo "!! 没找到场景目录，本节跳过（见 02-scene-dir.txt）"
  else
    # 解释器必须真能跑起来才算数（command -v 找到的可能只是个壳）
    PY=""
    for cand in "$SR_PYTHON_PROD" python3 python; do
      if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "pass" >/dev/null 2>&1; then
        PY="$cand"; break
      fi
    done
    if [ -z "$PY" ]; then
      echo "!! 没有可用解释器（$SR_PYTHON_PROD / python3 / python 都不可用）"
      echo "!! 请手工看：grep -o '<SolarAzimuth[^<]*' \"$D/${BASE}_meta.xml\" （或 .meta.xml）" | sh_paths
    else
      echo "[解释器] $PY"
      OUT_STEP=$("$PY" - "$D" "$BASE" <<'PYEOF'
import sys, os, xml.dom.minidom as m
d, base = sys.argv[1], sys.argv[2]
path = None
for cand in (base + "_meta.xml", base + ".meta.xml"):
    p = os.path.join(d, cand)
    if os.path.exists(p):
        path = p
        break
if path is None:
    print("!! 目录里没有 %s_meta.xml / %s.meta.xml，本节无从解析" % (base, base)); sys.exit(0)
print("  meta 文件      :", os.path.basename(path))
try:
    doc = m.parse(path)
except Exception as e:
    print("!! meta 解析失败:", type(e).__name__, e); sys.exit(0)
def kids(tag):
    els = doc.getElementsByTagName(tag)
    if not els:
        print("  %-14s : <标签不存在>" % tag); return None
    return [c.data for c in els[0].childNodes if c.nodeType == c.TEXT_NODE]
sa = kids("SolarAzimuth")
if sa is not None:
    print("  step           :", "RC" if len(sa) == 0 else "SC", "(SolarAzimuth=%r)" % (sa,))
print("  CloudPercent   :", kids("CloudPercent"))
print("  DataBits       :", kids("DataBits"))
PYEOF
)
      printf '%s\n' "$OUT_STEP"
      STEP=$(printf '%s\n' "$OUT_STEP" | grep -m1 'step' | sed 's/.*: *\([A-Z]*\).*/\1/')
      CLOUD=$(printf '%s\n' "$OUT_STEP" | grep -m1 'CloudPercent' | sed "s/.*: *//")
      BITS=$(printf '%s\n' "$OUT_STEP" | grep -m1 'DataBits' | sed "s/.*: *//")
    fi
  fi
} >> "$OUT/05-step.txt"
close_f 05-step.txt

# ============================================================================
# 6) 汇总页（最后写：它依赖前面各节的结果）
# ============================================================================
open_f 00-summary.txt "判读要点全在这一张"
{
  if [ -z "$D" ]; then
    echo "结论: 没找到任何含 *meta.xml 的场景目录"
    echo "PROD : $PROD" | sh_paths
    echo
    echo "请手挑一个生产编号目录后重跑（只重跑这一个脚本即可）："
    echo "  SR_D=<该目录绝对路径> sh $0"
  else
    echo "PROD 是否存在        : $( [ -d "$PROD" ] && echo 是 || echo 否 )"
    echo "场景目录 D           : $D" | sh_paths
    echo "D 的层级              : $LEVEL"
    echo "发现方式              : $FOUND_BY"
    echo "目录名 BASE           : $BASE"
    echo "----------------------------------------"
    echo "PROD/PRODUCT 这一层   : $PRODUCT_LAYER"
    echo "meta 所在层           : $META_DEPTH"
    [ -n "$META_EXAMPLE" ] && echo "meta 例               : $META_EXAMPLE"
    echo "----------------------------------------"
    echo "输入 <BASE>.tif       : $HAS_SC      (SC 步的输入)"
    echo "输入 PAN.tif          : $HAS_RC      (RC 步的输入)"
    echo "meta <BASE>(_或.)meta.xml : $HAS_META"
    echo "Debug/ 子目录         : $HAS_DEBUG"
    echo "目录内栅格数          : $N_TIF"
    echo "----------------------------------------"
    echo "step                  : $STEP"
    echo "CloudPercent          : $CLOUD"
    echo "DataBits              : $BITS"
    echo "----------------------------------------"
    echo "判据（util.py:989-991）看 step 与上面两个输入哪一条对得上："
    echo "  step=RC  -> 用 PAN.tif"
    echo "  step=SC  -> 用 <BASE>.tif"
  fi
} >> "$OUT/00-summary.txt"
close_f 00-summary.txt

# ============================================================================
# 7) 产物清单
# ============================================================================
open_f 06-manifest.txt "本次产出的文件与行数（确认没被截断）"
{
  for f in 00-summary.txt 01-tree.txt 02-scene-dir.txt 03-inputs.txt 04-meta-debug.txt 05-step.txt; do
    if [ -f "$OUT/$f" ]; then
      echo "$f   $(wc -l < "$OUT/$f" | tr -d ' ') 行"
    else
      echo "$f   <未生成>"
    fi
  done
} >> "$OUT/06-manifest.txt"
close_f 06-manifest.txt

echo
echo "================ 完成，产物目录 = $OUT ================"
cat "$OUT/06-manifest.txt"
echo
echo "截屏顺序建议：先 00-summary.txt（判读要点），再按需补 01~05。"
