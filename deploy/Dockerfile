# Образ kb-build: установка зависимостей и сборка VitePress.
# Секретов в образе и при запуске нет; пользователь задаётся при `docker run`.
FROM node:24.22.0-slim
LABEL kb-bot=kb-build

ARG PNPM_VERSION=12.11.2
ENV COREPACK_HOME=/opt/corepack \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    COREPACK_ENABLE_STRICT=0 \
    NEXT_TELEMETRY_DISABLED=1 \
    HOME=/tmp

RUN corepack enable \
 && corepack prepare "pnpm@${PNPM_VERSION}" --activate \
 && chmod -R a+rX /opt/corepack \
 && pnpm --version

COPY kb-build-inner.sh /usr/local/bin/kb-build-inner
RUN chmod 755 /usr/local/bin/kb-build-inner

WORKDIR /work
