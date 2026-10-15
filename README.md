# kb-bot

Telegram-бот **@jade_kb_bot** для личной базы знаний
[`technical-jade/knowledge-base`](https://github.com/technical-jade/knowledge-base):
пишет и правит статьи через Claude, публикует сайт
**https://jade.tail2b43aa.ts.net/kb/** (только тайлнет), отвечает на вопросы.
Требования и решения — [`SPEC.md`](SPEC.md).

## Устройство

```
Telegram ─► kb-bot (systemd, user kb, /opt/kb-bot)
              │ ALLOWED_ID? → route (Claude, без инструментов) → намерение
              │ очередь: запись — по одной, чтение — до 2 параллельно
              ├─ sudo -u kb-claude kb-claude-run <route|read|write>   ← Claude, без Bash
              │     cwd /srv/kb/repo, писать может только в docs/
              ├─ git (deploy key kb) /srv/kb/repo
              ├─ проверка Markdown (kbbot/mdcheck.py)
              ├─ sudo kb-build-run build   ← Docker kb-build, без сети и секретов
              └─ публикация /srv/kb/releases/<sha> ← /srv/kb/site
kb-web (127.0.0.1:8090) ◄─ tailscale serve /kb
```

| Что | Где |
|---|---|
| Код (установленный) | `/opt/kb-bot` (root, только чтение) |
| Секреты бота | `/etc/kb-bot/env` (`BOT_TOKEN`, `ALLOWED_ID`; kb 600) |
| Токен Claude | `/etc/kb-claude/token` (kb-claude 600) |
| Deploy key | `/home/kb/.ssh/id_ed25519_kb` |
| Рабочая копия | `/srv/kb/repo` (docs/ — `kb-docs 2775`, остальное kb-claude только читает) |
| Релизы сайта | `/srv/kb/releases/<sha>`, текущий — ссылка `/srv/kb/site` (хранится 5) |
| Патчи конфликтов | `/srv/kb/conflicts/*.patch` (хранятся 30 дней) |
| Состояние | `/var/lib/kb-bot/state.db` (SQLite) |
| Обёртки | `/usr/local/bin/kb-claude-run`, `/usr/local/bin/kb-build-run`, `/usr/local/bin/kb-bot-cli` |
| sudo | `/etc/sudoers.d/kb-bot` |
| Настройки Claude | `/home/kb-claude/.claude/settings.json` (root, `chattr +i`) |
| Службы | `kb-bot.service`, `kb-web.service` |

Модули `kbbot/`: `core` (команды, намерения, конвейер изменения),
`telegram` / `cli` (два «ответчика» к одному ядру), `claude` (запуск и разбор
stream-json), `intent` (разбор JSON), `mdcheck`, `gitops`, `builder`,
`publish`, `web`, `store`, `tgformat` (Markdown → HTML Telegram).

### Изоляция

- Claude работает от `kb-claude`: нет Bash (инструменты задаёт обёртка по
  режиму), deny-правила в `settings.json`, а главное — права ОС: запись
  только в `docs/`, чтение `/home/stan`, `/home/kb`, `/etc/kb-bot` запрещено.
- `settings.json` неизменяем (`chattr +i`): Write пишет через временный файл
  и `rename`, без флага файл можно было бы подменить целиком.
- Бот после Claude проверяет `git status`: изменения вне `docs/` — откат всего.
- Сборка — в контейнере `--network none --read-only`, не root, без секретов;
  зависимости ставятся отдельным запуском с сетью, только при смене lockfile.
- `kb` не в группе `docker`: только `sudo kb-build-run install|build`.

## Установка и обновление

```bash
cd ~/projects/knowledge-base-bot
git pull
sudo deploy/install.sh          # код → /opt/kb-bot, venv, обёртки, sudoers, образ, службы
```

`install.sh` идемпотентен; `--no-bot` — не (пере)запускать `kb-bot`.
Первичная подготовка (пользователи, секреты, deploy key) описана в SPEC.md, этап 0.

## Самопроверка без Telegram

```bash
kb-bot-cli "Чем HTTP/2 отличается от HTTP/3?"
kb-bot-cli --choose dev "напиши гайд по Docker volumes"   # ответ на кнопки стиля
kb-bot-cli --reply 1000000007 "сделай короче"              # правка «ответом» на сообщение CLI
kb-bot-cli --choose yes /undo                               # подтверждение
kb-bot-cli --tasks                                          # журнал задач
```

CLI работает от `kb` тем же кодом, что и бот, и делит с ним замок записи.

Тесты: `venv/bin/pytest` (на временных репозиториях с поддельными Claude и сборкой).

## Обновление токена Claude

Ошибка авторизации → бот пишет «Claude не авторизован». Тогда:

```bash
claude setup-token                 # на любой машине, скопировать токен
sudo -u kb-claude sh -c 'read -r t; printf "%s" "$t" > /etc/kb-claude/token'
# вставить токен, Enter; перезапуск не нужен
```

Токен бота Telegram — строка `BOT_TOKEN=` в `/etc/kb-bot/env`, затем
`sudo systemctl restart kb-bot`.

## Откат

- **Сайт** на предыдущий релиз:
  `sudo -u kb -H bash -c 'cd /opt/kb-bot && venv/bin/python -m kbbot.publish rollback'`
  (`… publish status` — список релизов).
- **Изменение бота** — `/undo` в Telegram (только если последний коммит от `kb-bot`).
- **Код бота** — `git checkout <коммит>` здесь и `sudo deploy/install.sh`.
- **tailscale serve** — бэкап исходной конфигурации был в `backup/` (вне git);
  убрать путь: `sudo tailscale serve --https=443 --set-path /kb off`.

## Перезапуск и журналы

```bash
sudo systemctl restart kb-bot kb-web
journalctl -u kb-bot -f            # JSON-строки, токен вырезан
journalctl -u kb-web -f
```
