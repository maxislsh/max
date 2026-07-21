# Tarot Telegram Bot — Карта дня

Готовый бот на `python-telegram-bot` с собственной базой знаний по 78 картам Таро
(22 Старших Аркана прописаны вручную, 56 Младших Арканов — по проверенным
тематическим шаблонам масть+номер) и хранением истории раскладов в SQLite.

## Структура проекта

```
tarot_bot/
├── bot.py             # логика бота (команды, кнопки, SQLite)
├── generate_data.py   # генератор базы знаний -> tarot_data.json (уже сгенерирован)
├── tarot_data.json     # база знаний, 78 карт
├── requirements.txt
└── README.md
```

## Установка

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Настройка

1. Создайте бота через **@BotFather** в Telegram, получите токен.
2. Экспортируйте токен в переменную окружения:

```bash
export TELEGRAM_BOT_TOKEN="123456789:AA...ваш_токен"
```

## Запуск

```bash
python3 bot.py
```

Бот стартует в режиме long polling. Для продакшена рекомендуется:
- systemd unit / supervisor для авто-рестарта,
- либо webhook + reverse proxy (nginx) вместо polling, если нужна масштабируемость.

## Команды бота

| Команда | Что делает |
|---|---|
| `/start` | Приветствие + меню с кнопками |
| `/today` | Карта дня — фиксируется одна карта на сутки (таймзона Europe/Kyiv, настраивается в `bot.py` константой `TIMEZONE`) |
| `/random` | Случайная карта без ограничений — можно спрашивать сколько угодно раз |
| `/card <название>` | Поиск конкретной карты в базе, например `/card Луна` или `/card Король Пентаклей` |
| `/history` | Последние 10 раскладов пользователя |
| `/help` | Справка |

Бот также распознаёт свободный текст, если пользователь просто напишет название карты
без команды.

## Хранилище (SQLite)

Файл `tarot_bot.db` создаётся автоматически при первом запуске. Таблица `draws`:

| поле | описание |
|---|---|
| user_id | Telegram ID пользователя |
| username | username на момент раскладa |
| draw_date | дата в формате YYYY-MM-DD (для логики "одна карта в сутки") |
| card_name | название карты |
| orientation | upright / reversed |
| kind | daily / random |
| created_at | UTC timestamp |

## Как расширять базу знаний

- Отредактируйте `MAJOR`, `SUITS`, `NUMBER_TEMPLATES`, `COURTS` в `generate_data.py`
  и перезапустите `python3 generate_data.py` — файл `tarot_data.json` пересоздастся.
- Либо редактируйте `tarot_data.json` напрямую (это плоский список из 78 объектов
  с полями `name`, `en`, `arcana`, `suit`, `keywords_up/rev`, `upright`, `reversed`,
  `love`, `work`, `advice`).
- Можно добавить картинки карт: положите файлы `images/<en-name>.jpg` и в `format_card`/
  хендлерах используйте `reply_photo` вместо `reply_text`.

## Идеи для развития

- **Расклад на 3 карты** (прошлое/настоящее/будущее) — добавить команду `/spread3`,
  тянуть 3 уникальные карты через `random.sample(CARDS, 3)`.
- **Ежедневная рассылка**: `JobQueue` из `python-telegram-bot` + таблица подписчиков,
  чтобы присылать карту дня в 9:00 по Киеву без запроса пользователя.
- **Мультиязычность**: продублировать поля `upright/reversed/love/work/advice` на EN
  и переключать по `update.effective_user.language_code`.
- **Инлайн-режим** (`InlineQueryHandler`), чтобы карту можно было прислать в любой чат.
