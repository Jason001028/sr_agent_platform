#!/usr/bin/env bash
# 阶段2 离线交付打包：dist + deploy/nginx.conf + deploy/README.md → 一个 tar.gz。
# 产出 release/sr-agent-platform-<日期>-<版本>.tar.gz，整个拷到内网机解压即可跑。
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
tar -czf "$OUT_FILE" -C "$STAGE_ROOT" sr-agent-platform
rm -rf "$STAGE_ROOT"

echo "✅ 离线包：$OUT_FILE"
echo "   包含 dist/ + nginx.conf + README.md（${VERSION}），全离线无 CDN。"
echo "   部署步骤见包内 README.md（解压到 /data/www → cp nginx.conf → nginx -t && reload）。"
