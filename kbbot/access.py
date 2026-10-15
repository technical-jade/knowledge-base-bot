"""Единая точка контроля доступа: outer-middleware на все апдейты."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

log = logging.getLogger("access")


class AccessMiddleware(BaseMiddleware):
    """Пропускает к обработчикам только апдейты от единственного ALLOWED_ID.

    Висит на dp.update, поэтому покрывает сообщения, нажатия кнопок и любые
    будущие типы апдейтов — отдельные проверки в обработчиках не нужны.
    Посторонним — никакого ответа; их ID пишется в журнал бота.
    """

    def __init__(self, allowed_id: int) -> None:
        self._allowed = allowed_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # aiogram кладёт отправителя в data["event_from_user"] для любого типа
        # апдейта. Сверяем числовой ID — username меняется, ID постоянен.
        user = data.get("event_from_user")
        if user is not None and user.id == self._allowed:
            return await handler(event, data)

        # Содержимое сообщений посторонних не пишем — только кто и что за апдейт.
        log.warning(
            "access denied",
            extra={
                "user_id": user.id if user else None,
                "username": user.username if user else None,
                "update_type": event.event_type if isinstance(event, Update) else None,
            },
        )
        return None
