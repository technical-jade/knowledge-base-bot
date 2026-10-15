#!/bin/sh
# Поддельная сборка: FAKE_BUILD_FAIL=1 — ошибка
set -e
out="$1"
if [ -f "$(dirname "$out")/FAIL_BUILD" ]; then echo "TypeError: boom"; exit 1; fi
mkdir -p "$out/site"
echo "<html>ok</html>" > "$out/site/index.html"
echo "build complete"
