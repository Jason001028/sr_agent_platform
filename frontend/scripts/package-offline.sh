#!/usr/bin/env bash
# 阶段2/4 离线交付打包：dist + deploy 件 + backend → 一个 tar.gz。
# 产物 release/sr-agent-platform-<日期>-<版本>.tar.gz，整个拷到内网机解压即可跑。
# 阶段4 起含 backend/（FastAPI：/api/scenes 检索 + 懒生成预览）+ sr-api.service + requirements-api.txt。
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
[ -f ../deploy/nginx.conf ] || { echo "❌ 缺 ../deploy/nginx.conf"; exit 1; }
[ -f ../deploy/README.md ]  || { echo "❌ 缺 ../deploy/README.md"; exit 1; }
[ -d ../backend/api ]        || { echo "❌ 缺 ../backend/api"; exit 1; }
[ -f ../deploy/sr-api.service ] || { echo "❌ 缺 ../deploy/sr-api.service"; exit 1; }
[ -f ../deploy/requirements-api.txt ] || { echo "❌ 缺 ../deploy/requirements-api.txt"; exit 1; }

# —— 版本与产物路径 ——
VERSION="$(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD 2>/dev/null || echo dev)"
STAMP="$(date +%Y%m%d)"
OUT_DIR="release"
OUT_FILE="${OUT_DIR}/sr-agent-platform-${STAMP}-${VERSION}.tar.gz"
mkdir -p "$OUT_DIR"

# —— 组包（先铺平到暂存目录，再打成带顶层目录的 tar.gz，解压不散一地）——
STAGE_ROOT="$(mktemp -d)"
STAGE="${STAGE_ROOT}/sr-agent-platform"
mkdir -p "$STAGE"
cp -r dist           "$STAGE/dist"
cp ../deploy/nginx.conf "$STAGE/nginx.conf"
cp ../deploy/README.md  "$STAGE/README.md"
# 阶段4：后端（FastAPI）+ systemd 单元 + 运行依赖清单
cp -r ../backend      "$STAGE/backend"
cp ../deploy/sr-api.service      "$STAGE/sr-api.service"
cp ../deploy/requirements-api.txt "$STAGE/requirements-api.txt"
# 清理打包污染：pycache 与测试（跑测试用开发机仓库，内网机只跑服务）
find "$STAGE/backend" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -rf "$STAGE/backend/tests" "$STAGE/backend/__pycache__"
tar -czf "$OUT_FILE" -C "$STAGE_ROOT" sr-agent-platform
rm -rf "$STAGE_ROOT"

echo "✅ 离线包：$OUT_FILE"
echo "   包含 dist/ + nginx.conf + README.md + backend/ + sr-api.service + requirements-api.txt（${VERSION}），全离线无 CDN。"
echo "   部署步骤见包内 README.md（解压 → nginx.conf + systemd 起 FastAPI → reload）。"
