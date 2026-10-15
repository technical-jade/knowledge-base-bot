#!/bin/bash
# Права рабочей копии (ТЗ, раздел 5): kb-claude пишет только в docs/.
set -euo pipefail
R=/srv/kb/repo
chown -R kb:kb-docs "$R"
# Всё, кроме docs/ и .git: группа только читает
find "$R" -path "$R/.git" -prune -o -path "$R/docs" -prune -o -type d -exec chmod 750 {} +
find "$R" -path "$R/.git" -prune -o -path "$R/docs" -prune -o -type f -exec chmod 640 {} +
# .git — только kb
chown -R kb:kb "$R/.git"
chmod -R go-rwx "$R/.git"
# docs/: setgid-группа kb-docs, запись для группы
find "$R/docs" -type d -exec chmod 2775 {} +
find "$R/docs" -type f -exec chmod 664 {} +
