import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEB_APP_URL = os.getenv("WEB_APP_URL", "").rstrip("/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")
if not WEB_APP_URL:
    raise RuntimeError("WEB_APP_URL is not set")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    referral = parts[1] if len(parts) == 2 else ""

    # The referral is put into the WebApp URL. The web app also accepts
    # ref=... directly, so the friend is opened inside Telegram.
    url = f"{WEB_APP_URL}/?ref={referral}" if referral else f"{WEB_APP_URL}/"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🪙 ОТКРЫТЬ LARP COIN",
                web_app=WebAppInfo(url=url),
            )]
        ]
    )

    await message.answer(
        "🔴 <b>LARP COIN</b>\n\n"
        "Тапай по монете, прокачивай силу клика и энергию.\n"
        "Приглашай друзей по реферальной ссылке.",
        reply_markup=kb,
        parse_mode="HTML",
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
