import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://YOUR-DOMAIN.example/")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪙 Открыть тапалку", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])

@dp.message(CommandStart())
async def start(message: Message):
    ref_id = None

    if message.text:
        parts = message.text.split(maxsplit=1)

        if len(parts) == 2 and parts[1].startswith("ref_"):
            ref_id = parts[1][4:]

    webapp_url = WEBAPP_URL

    if ref_id and ref_id.isdigit():
        webapp_url = f"{WEBAPP_URL}?ref={ref_id}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🪙 Открыть тапалку",
                    web_app=WebAppInfo(url=webapp_url)
                )
            ]
        ]
    )

    await message.answer(
        "🔴 <b>LARP COIN</b>\n\n"
        "Тапай по LARP COIN, копи монеты, прокачивай силу клика и энергию.\n\n"
        "🪙 Реферальный бонус будет учтён автоматически.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    )

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
