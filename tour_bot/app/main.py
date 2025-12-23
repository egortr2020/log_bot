import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.handlers import newtour, start  # <-- добавила start


async def main():
    bot = Bot(
        token=settings.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()

    # важно: сначала базовые команды, потом сложные сценарии
    dp.include_router(start.router)
    dp.include_router(newtour.router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
