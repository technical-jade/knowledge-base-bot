#!/bin/sh
# Запускается внутри контейнера kb-build. /src — репозиторий (только чтение),
# /work — tmpfs, /work/node_modules — том kb-node-modules, /out — вывод.
set -eu

copy_src() {
    # .git и node_modules не нужны; всё остальное — во временную папку.
    # Корень /work (tmpfs) не трогаем: его права менять нельзя
    find /src -mindepth 1 -maxdepth 1 ! -name .git ! -name node_modules -exec cp -R {} /work/ \;
}

case "${1:-}" in
install)
    copy_src
    pnpm install --frozen-lockfile --ignore-scripts --store-dir /store
    sha256sum /work/pnpm-lock.yaml | cut -d' ' -f1 > /work/node_modules/.kb-lock-sha
    ;;
build)
    copy_src
    if [ ! -f /work/node_modules/.kb-lock-sha ] \
        || [ "$(sha256sum /work/pnpm-lock.yaml | cut -d' ' -f1)" != "$(cat /work/node_modules/.kb-lock-sha)" ]; then
        echo "kb-build: зависимости не установлены для текущего pnpm-lock.yaml" >&2
        exit 3
    fi
    # Без pnpm: на этапе сборки сети нет, а vitepress запускается напрямую
    rc=0
    node node_modules/vitepress/bin/vitepress.js build --outDir /out/site >/tmp/build.log 2>&1 || rc=$?
    cat /tmp/build.log
    [ "$rc" -eq 0 ] || exit "$rc"
    # VitePress завершает сборку с кодом 0, даже если страница упала при
    # рендере (проверено фактом) — такие ошибки ищем в логе
    if grep -Eq '(^|[[:space:]])([A-Za-z]*Error|✖|build error)' /tmp/build.log; then
        echo "kb-build: в логе сборки есть ошибки" >&2
        exit 4
    fi
    test -f /out/site/index.html
    ;;
*)
    echo "usage: kb-build-inner install|build" >&2
    exit 2
    ;;
esac
