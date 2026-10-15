#!/bin/bash
# Установка/обновление kb-bot на jade. Запуск: sudo deploy/install.sh [--no-bot]
# Идемпотентен: повторный запуск обновляет код, обёртки, образ и службы.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
DST=/opt/kb-bot
START_BOT=1
[[ ${1:-} == --no-bot ]] && START_BOT=0

[[ $EUID -eq 0 ]] || { echo "нужен root: sudo $0" >&2; exit 1; }
for u in kb kb-claude; do id "$u" >/dev/null || { echo "нет пользователя $u" >&2; exit 1; }; done

step() { printf '\n== %s\n' "$*"; }

step "код → $DST"
install -d -o root -g root -m 755 "$DST"
rsync -a --delete --chown=root:root --chmod=D755,F644 \
    --exclude venv --exclude '__pycache__' --exclude .pytest_cache --exclude .git \
    --exclude tests --exclude 'REPORT.md' \
    "$SRC/kbbot" "$SRC/prompts" "$SRC/requirements.txt" "$DST/"
install -m 755 -o root -g root "$SRC/deploy/kb-bot-cli" /usr/local/bin/kb-bot-cli

step "venv"
if [[ ! -x $DST/venv/bin/python ]]; then
    python3 -m venv "$DST/venv"
fi
want=$(sha256sum "$DST/requirements.txt" | cut -d' ' -f1)
have=$(cat "$DST/venv/.req-sha" 2>/dev/null || true)
if [[ $want != "$have" ]]; then
    "$DST/venv/bin/pip" install -q --upgrade pip
    "$DST/venv/bin/pip" install -q -r "$DST/requirements.txt"
    echo "$want" > "$DST/venv/.req-sha"
fi
chmod -R go-w "$DST"

step "обёртки и sudoers"
install -m 755 -o root -g root "$SRC/deploy/kb-build-run" /usr/local/bin/kb-build-run
install -m 755 -o root -g root "$SRC/deploy/kb-claude-run" /usr/local/bin/kb-claude-run
tmp=$(mktemp)
cp "$SRC/deploy/sudoers-kb-bot" "$tmp"
visudo -cf "$tmp" >/dev/null
install -m 440 -o root -g root "$tmp" /etc/sudoers.d/kb-bot
rm -f "$tmp"
visudo -c >/dev/null

step "папки"
install -d -o kb -g kb-docs -m 750 /srv/kb
install -d -o kb -g kb -m 755 /srv/kb/releases /srv/kb/build-out
install -d -o kb -g kb -m 750 /srv/kb/conflicts
install -d -o kb -g kb -m 700 /var/lib/kb-bot

step "рабочая копия /srv/kb/repo"
if [[ ! -d /srv/kb/repo/.git ]]; then
    sudo -u kb -H git clone -q git@github.com:technical-jade/knowledge-base.git /srv/kb/repo
fi
sudo -u kb -H git -C /srv/kb/repo config user.name kb-bot
sudo -u kb -H git -C /srv/kb/repo config user.email kb-bot@jade
sudo -u kb -H git -C /srv/kb/repo config core.sharedRepository false
"$SRC/deploy/fix-perms.sh"

step "образ kb-build"
pnpm_ver=$(python3 -c 'import json;print(json.load(open("/srv/kb/repo/package.json"))["packageManager"].split("@")[1].split("+")[0])')
docker build -q --build-arg "PNPM_VERSION=$pnpm_ver" -t kb-build "$SRC/deploy" >/dev/null
echo "kb-build (pnpm $pnpm_ver)"

step "Claude для kb-claude"
install -d -o kb-claude -g kb-claude -m 700 /home/kb-claude/.claude
# settings.json принадлежит root: Claude (Write) не может его переписать
install -m 644 -o root -g root "$SRC/deploy/kb-claude-settings.json" /home/kb-claude/.claude/settings.json


step "службы"
install -m 644 -o root -g root "$SRC/deploy/kb-web.service" /etc/systemd/system/kb-web.service
install -m 644 -o root -g root "$SRC/deploy/kb-bot.service" /etc/systemd/system/kb-bot.service
systemctl daemon-reload
systemctl enable -q kb-web
systemctl restart kb-web
if [[ $START_BOT == 1 ]]; then
    systemctl enable -q kb-bot
    systemctl restart kb-bot
fi
systemctl --no-pager --lines=0 status kb-web | head -3
echo "готово"
