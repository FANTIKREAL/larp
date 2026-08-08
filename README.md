# 🔴 LARP COIN — Telegram Mini App

Готовая развлекательная тапалка LARP COIN для Telegram. Игроки тапают по твоему изображению.

## Что есть

- 🪙 Тап по монете
- ⚡ Энергия и автоматическое восстановление
- 📈 Улучшение силы клика
- 🔋 Улучшение максимальной энергии
- 🏆 Таблица лидеров
- 👥 Реферальный старт-параметр
- 💾 SQLite — прогресс сохраняется
- 🔐 Проверка Telegram `initData` на сервере
- 📱 Адаптация под Telegram Mini App
- 🤖 Telegram-бот с кнопкой запуска Mini App

## 1. Создать бота

В Telegram открой @BotFather и создай бота через `/newbot`.
Скопируй токен.

## 2. Настроить

Сделай копию `.env.example` с именем `.env`:

```env
BOT_TOKEN=твой_токен
WEBAPP_URL=https://твой-домен/
DB_PATH=tapalka.db
```

Для Mini App нужен HTTPS-адрес.

## 3. Установить зависимости

```bash
pip install -r requirements.txt
```

## 4. Запустить сервер

```bash
uvicorn server:app --host 0.0.0.0 --port 8000
```

## 5. Запустить бота

В другом окне:

```bash
python bot.py
```

## 6. Подключить Mini App в BotFather

В @BotFather настрой кнопку Menu/Web App и укажи тот же HTTPS-адрес, что в `WEBAPP_URL`.

### Рефералы

Можно открывать игру с параметром:

`https://твой-домен/?tgWebAppStartParam=ID_ПОЛЬЗОВАТЕЛЯ`

В проекте реферальный ID ожидается как числовой start parameter.

## Важно

Не публикуй `.env` и токен бота. Если токен случайно попал в GitHub/чат — перевыпусти его через BotFather.
