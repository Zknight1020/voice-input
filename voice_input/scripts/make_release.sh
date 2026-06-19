#!/bin/bash
# 打包浏览器版发布 zip。在项目根（mac 或 linux）执行。
# 默认 public-safe：不包含 .env / key。
# 如确需私有包，显式传 --include-private-env。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

NAME="VoiceInput"
STAGE="$(mktemp -d)/$NAME"
ZIP="$PROJECT_ROOT/${NAME}_release.zip"
INCLUDE_PRIVATE_ENV=0

if [ "${1:-}" = "--include-private-env" ]; then
  INCLUDE_PRIVATE_ENV=1
fi

mkdir -p "$STAGE"

if [ -f "$PROJECT_ROOT/voice_input/release/setup.bat" ]; then
  RELEASE_DIR="$PROJECT_ROOT/voice_input/release"
else
  RELEASE_DIR="$PROJECT_ROOT"
fi

echo "== 复制源码 =="
mkdir -p "$STAGE/voice_input"
cp -r voice_input/core      "$STAGE/voice_input/core"
cp -r voice_input/scripts   "$STAGE/voice_input/scripts"
cp -r voice_input/web       "$STAGE/voice_input/web"
cp    voice_input/__init__.py    "$STAGE/voice_input/"
cp    voice_input/config.py      "$STAGE/voice_input/"
cp    voice_input/main.py        "$STAGE/voice_input/"
cp    voice_input/server.py      "$STAGE/voice_input/"
cp    voice_input/hotwords.txt   "$STAGE/voice_input/"
cp    voice_input/.env.example   "$STAGE/voice_input/"
cp    voice_input/requirements.txt "$STAGE/voice_input/"

echo "== 复制启动脚本 + 说明 =="
cp "$RELEASE_DIR/setup.bat"   "$STAGE/"
cp "$RELEASE_DIR/start.bat"   "$STAGE/"
cp "$RELEASE_DIR/使用说明.md" "$STAGE/"
cp "$RELEASE_DIR/voice_input_context.py" "$STAGE/"
cp voice_input/README.md           "$STAGE/README.md"
cp voice_input/.env.example        "$STAGE/.env.example"

if [ "$INCLUDE_PRIVATE_ENV" = "1" ] && [ -f voice_input/release/.env ]; then
  echo "== 复制私有 .env（git ignored） =="
  cp voice_input/release/.env "$STAGE/.env"
else
  echo "== public-safe：发布包不包含 .env / key =="
fi

echo "== 清理 __pycache__ =="
find "$STAGE" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name "*.pyc" -delete 2>/dev/null || true

echo "== 打包 zip =="
rm -f "$ZIP"
( cd "$(dirname "$STAGE")" && zip -rq "$ZIP" "$NAME" )

# 清理 staging
rm -rf "$(dirname "$STAGE")"

SIZE=$(du -h "$ZIP" | cut -f1)
echo
echo "== 完成 =="
echo "  $ZIP ($SIZE)"
