#!/usr/bin/env bash
# 阶段4 离线交付打包：dist 与 backend **各一个包**（用户 2026-09-18 定的口径）。
# ------------------------------------------------------------------
# 产物固定在**仓库根** release/，两个包、顶层都**不带** sr-agent-platform/ 前缀 ——
# 真机的更新命令是把包直接解进应用目录（`tar -xzf /tmp/dist-….tar.gz -C $APP`），
# 多一层目录会解出 `$APP/dist-<日期>/`，而不是覆盖 `$APP/dist/`：
#   dist-<日期>-<时分>-<版本>.tar.gz      顶层 dist/
#   backend-<日期>-<时分>-<版本>.tar.gz   顶层 backend/（去 __pycache__/ 与 tests/）
#
# 首次安装件（nginx.conf / sr-api.service / requirements-api.txt / deploy/README.md）
# **不进包**（用户选定）：它们是机器配置模板，真机上已被手工 sed 成真实路径，进包解压
# 会把真路径盖回出厂示例 —— 那是 deploy/README.md §5.6 那类事故。首次部署照
# deploy/README.md 办，不走这两个包；这两个包只用于「已装好的机器上换新版本」。
#
# 用法（在 frontend/ 下，即 npm script）：
#   npm run package:offline          # 用现有 dist（不存在则报错）
#   npm run package:offline -- build # 先 vite build 再打包
#
# 依赖：tar（GNU tar，Windows git bash / CentOS 都自带；不依赖 zip）。
set -euo pipefail
cd "$(dirname "$0")/.."   # 保证从 frontend/ 出发

DO_BUILD="${1:-}"
if [ "$DO_BUILD" = "build" ]; then
  npm run build
fi

# —— 校验原料 ——
[ -d dist ] || { echo "❌ 没有 dist/。先 npm run build，或 npm run package:offline -- build"; exit 1; }
[ -d ../backend/api ] || { echo "❌ 缺 ../backend/api"; exit 1; }

REPO_ROOT="$(cd .. && pwd)"
OUT_DIR="${REPO_ROOT}/release"
VERSION="$(git describe --tags --always 2>/dev/null || echo dev)"
STAMP="$(date +%Y%m%d)-$(date +%H%M)"
DIST_PKG="dist-${STAMP}-${VERSION}.tar.gz"
BACK_PKG="backend-${STAMP}-${VERSION}.tar.gz"

mkdir -p "$OUT_DIR"

# —— 组包 ——
# 先 cd 进输出目录、只给**相对**包名：GNU tar 见到归档名里的 `D:` 会按 `host:path`
# 当成远程主机（`Cannot connect to D`），这是 Windows 上必踩的坑。
cd "$OUT_DIR"
tar -czf "$DIST_PKG" -C "${REPO_ROOT}/frontend" dist
# backend 包排除测试与字节码：跑测试用开发机仓库，内网机只跑服务。
# --exclude 匹配成员名，命中目录即整棵子树一起排除。
tar -czf "$BACK_PKG" -C "$REPO_ROOT" \
  --exclude='__pycache__' --exclude='backend/tests' \
  backend

# —— 清掉 release/ 里同族的旧包（只认这三个前缀，无关文件一律不碰）——
shopt -s nullglob
for f in "$OUT_DIR"/dist-*.tar.gz "$OUT_DIR"/backend-*.tar.gz "$OUT_DIR"/sr-agent-platform-*.tar.gz; do
  base="$(basename "$f")"
  if [ "$base" = "$DIST_PKG" ] || [ "$base" = "$BACK_PKG" ]; then continue; fi
  rm -f "$f"
  echo "   清旧包 $base"
done

echo "✅ 离线包（${VERSION}，release/）："
ls -l "$OUT_DIR/$DIST_PKG" "$OUT_DIR/$BACK_PKG" | awk '{printf "   %-46s %8.1f KB\n", $9, $5/1024}'
echo "   部署/更新：两个包拷到内网机 /tmp，分别 tar -xzf <包> -C \$APP；"
echo "   前端 systemctl reload nginx、后端 systemctl restart sr-api。详见 deploy/README.md §一。"
