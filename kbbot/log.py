"""Структурированные логи в stdout: одна JSON-строка на событие."""

import json
import logging
import sys
from datetime import UTC, datetime

_STD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, secret: str = "") -> None:
        super().__init__()
        self._secret = secret

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Поля, переданные через extra={...}
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        # Страховка: токен входит в URL Bot API, и сетевые исключения
        # aiohttp могут его содержать. Вырезаем из итоговой строки целиком.
        if self._secret:
            line = line.replace(self._secret, "<redacted>")
        return line


def setup_logging(secret: str = "") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(secret))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
