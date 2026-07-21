# -*- coding: utf-8 -*-
"""
Telegram-бот «Карта дня Таро»
=============================
Функционал:
  /start        - приветствие + главное меню
  /today        - карта дня (одна и та же карта для пользователя в течение суток,
                  по умолчанию таймзона Europe/Kyiv)
  /card <имя>   - найти конкретную карту в базе знаний (поиск по русскому/англ. имени)
  /random       - случайная карта прямо сейчас (не привязана к дню, можно спрашивать много раз)
  /history      - последние 10 раскладов пользователя
  /help         - список команд

Хранилище: SQLite (tarot_bot.db), создаётся автоматически.
База знаний: tarot_data.json (78 карт, генерируется generate_data.py).

Зависимости:
    pip install python-telegram-bot==21.* python-dateutil pytz

Запуск:
    export TELEGRAM_BOT_TOKEN="123456:ABC-..."
    python3 bot.py
"""

import json
import logging
import os
import random
import sqlite3
import threading
import http.server
from datetime import datetime, timezone
from pathlib import Path

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# --------------------------------------------------------------------------- #
# Конфигурация
# --------------------------------------------------------------------------- #

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "tarot_data.json"
DB_PATH = BASE_DIR / "tarot_bot.db"
TIMEZONE = pytz.timezone("Europe/Kyiv")  # день считается по этой TZ

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("tarot_bot")

# --------------------------------------------------------------------------- #
# База знаний
# --------------------------------------------------------------------------- #

with open(DATA_PATH, "r", encoding="utf-8") as f:
    CARDS = json.load(f)

# индекс для быстрого поиска по имени (ru/en, регистр не важен)
NAME_INDEX = {}
for c in CARDS:
    NAME_INDEX[c["name"].lower()] = c
    if c.get("en"):
        NAME_INDEX[c["en"].lower()] = c


def find_card_by_name(query: str):
    query = query.strip().lower()
    if query in NAME_INDEX:
        return NAME_INDEX[query]
    # частичное совпадение
    matches = [c for name, c in NAME_INDEX.items() if query in name]
    return matches[0] if matches else None


def format_card(card: dict, orientation: str) -> str:
    """orientation: 'upright' | 'reversed'"""
    is_up = orientation == "upright"
    arrow = "⬆️ Прямая позиция" if is_up else "⬇️ Перевёрнутая позиция"
    meaning = card["upright"] if is_up else card["reversed"]
    keywords = card["keywords_up"] if is_up else card["keywords_rev"]

    suit_line = ""
    if card["arcana"] == "minor":
        suit_line = f"_Младший Аркан, масть: {card['suit']}_\n"
    else:
        suit_line = f"_Старший Аркан №{card['num']}_\n"

    text = (
        f"🔮 *{card['name']}* ({card['en']})\n"
        f"{suit_line}"
        f"{arrow}\n\n"
        f"*Значение:* {meaning}\n\n"
        f"*Ключевые слова:* {', '.join(keywords)}\n\n"
        f"❤️ *Любовь:* {card['love']}\n\n"
        f"💼 *Работа/финансы:* {card['work']}\n\n"
        f"✨ *Совет:* {card['advice']}"
    )
    return text


def draw_card():
    card = random.choice(CARDS)
    orientation = random.choice(["upright", "reversed"])
    return card, orientation


# --------------------------------------------------------------------------- #
# База данных (SQLite)
# --------------------------------------------------------------------------- #

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS draws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            draw_date TEXT NOT NULL,   -- YYYY-MM-DD в таймзоне TIMEZONE
            card_name TEXT NOT NULL,
            orientation TEXT NOT NULL,
            created_at TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'daily'  -- 'daily' | 'random'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_draws_user_date ON draws(user_id, draw_date)"
    )
    conn.commit()
    conn.close()


def today_str():
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


def get_daily_draw(user_id: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM draws WHERE user_id=? AND draw_date=? AND kind='daily'",
        (user_id, today_str()),
    ).fetchone()
    conn.close()
    return row


def save_draw(user_id: int, username: str, card_name: str, orientation: str, kind: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO draws (user_id, username, draw_date, card_name, orientation, created_at, kind) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            username or "",
            today_str(),
            card_name,
            orientation,
            datetime.now(timezone.utc).isoformat(),
            kind,
        ),
    )
    conn.commit()
    conn.close()


def get_history(user_id: int, limit: int = 10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM draws WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------- #
# Хендлеры команд
# --------------------------------------------------------------------------- #

MAIN_KEYBOARD = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("🃏 Карта дня", callback_data="today")],
        [InlineKeyboardButton("🎲 Случайная карта", callback_data="random")],
        [InlineKeyboardButton("📜 История", callback_data="history")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ]
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔮 *Добро пожаловать в Таро-бот!*\n\n"
        "Я помогу вытянуть карту дня, объясню её значение и дам совет.\n\n"
        "Доступные команды:\n"
        "• /today — карта дня (одна на сутки)\n"
        "• /random — случайная карта прямо сейчас\n"
        "• /card <название> — найти карту в базе (например: /card Луна)\n"
        "• /history — последние расклады\n"
        "• /help — справка"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=MAIN_KEYBOARD
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*Команды бота:*\n"
        "/today — получить карту дня (фиксируется на сутки)\n"
        "/random — вытянуть случайную карту без ограничений\n"
        "/card <название> — найти карту по имени, например `/card Императрица`\n"
        "/history — последние 10 раскладов\n"
        "/help — эта справка"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = get_daily_draw(user.id)
    if existing:
        card = NAME_INDEX[existing["card_name"].lower()]
        text = format_card(card, existing["orientation"])
        prefix = "🃏 *Ваша карта дня уже вытянута:*\n\n"
    else:
        card, orientation = draw_card()
        save_draw(user.id, user.username, card["name"], orientation, kind="daily")
        text = format_card(card, orientation)
        prefix = "🃏 *Ваша карта дня:*\n\n"

    await update.message.reply_text(prefix + text, parse_mode=ParseMode.MARKDOWN)


async def random_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    card, orientation = draw_card()
    save_draw(user.id, user.username, card["name"], orientation, kind="random")
    text = format_card(card, orientation)
    await update.message.reply_text(
        "🎲 *Случайная карта:*\n\n" + text, parse_mode=ParseMode.MARKDOWN
    )


async def card_lookup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Укажите название карты, например:\n`/card Луна`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    query = " ".join(context.args)
    card = find_card_by_name(query)
    if not card:
        await update.message.reply_text(
            f"Карта «{query}» не найдена. Проверьте написание (например: Шут, Маг, Луна, "
            f"Туз Кубков, Король Пентаклей и т.д.)."
        )
        return
    # для поиска по названию показываем прямое значение с кнопкой "показать перевёрнутое"
    text = format_card(card, "upright")
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Показать перевёрнутое значение", callback_data=f"rev::{card['name']}")]]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    rows = get_history(user.id, limit=10)
    if not rows:
        await update.message.reply_text("У вас пока нет раскладов. Используйте /today или /random.")
        return
    lines = ["📜 *Ваши последние расклады:*\n"]
    for r in rows:
        orient = "⬆️" if r["orientation"] == "upright" else "⬇️"
        kind_label = "день" if r["kind"] == "daily" else "случайная"
        lines.append(f"{r['draw_date']} ({kind_label}) — {orient} {r['card_name']}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "today":
        user = query.from_user
        existing = get_daily_draw(user.id)
        if existing:
            card = NAME_INDEX[existing["card_name"].lower()]
            text = format_card(card, existing["orientation"])
            prefix = "🃏 *Ваша карта дня уже вытянута:*\n\n"
        else:
            card, orientation = draw_card()
            save_draw(user.id, user.username, card["name"], orientation, kind="daily")
            text = format_card(card, orientation)
            prefix = "🃏 *Ваша карта дня:*\n\n"
        await query.message.reply_text(prefix + text, parse_mode=ParseMode.MARKDOWN)

    elif data == "random":
        user = query.from_user
        card, orientation = draw_card()
        save_draw(user.id, user.username, card["name"], orientation, kind="random")
        text = format_card(card, orientation)
        await query.message.reply_text("🎲 *Случайная карта:*\n\n" + text, parse_mode=ParseMode.MARKDOWN)

    elif data == "history":
        user = query.from_user
        rows = get_history(user.id, limit=10)
        if not rows:
            await query.message.reply_text("У вас пока нет раскладов. Используйте /today или /random.")
        else:
            lines = ["📜 *Ваши последние расклады:*\n"]
            for r in rows:
                orient = "⬆️" if r["orientation"] == "upright" else "⬇️"
                kind_label = "день" if r["kind"] == "daily" else "случайная"
                lines.append(f"{r['draw_date']} ({kind_label}) — {orient} {r['card_name']}")
            await query.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    elif data == "help":
        await help_cmd_from_query(query)

    elif data.startswith("rev::"):
        card_name = data.split("::", 1)[1]
        card = NAME_INDEX.get(card_name.lower())
        if card:
            text = format_card(card, "reversed")
            await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def help_cmd_from_query(query):
    text = (
        "*Команды бота:*\n"
        "/today — получить карту дня (фиксируется на сутки)\n"
        "/random — вытянуть случайную карту без ограничений\n"
        "/card <название> — найти карту по имени, например `/card Императрица`\n"
        "/history — последние 10 раскладов\n"
        "/help — эта справка"
    )
    await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Пытаемся распознать свободный текст как название карты
    card = find_card_by_name(update.message.text)
    if card:
        text = format_card(card, "upright")
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(
            "Не понимаю команду. Используйте /help для списка доступных команд.",
            reply_markup=MAIN_KEYBOARD,
        )




# --------------------------------------------------------------------------- #
# Health-check HTTP server (for platform probing on port 8000)
# --------------------------------------------------------------------------- #

class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args):
        pass  # silence access logs


def _start_health_server():
    port = int(os.environ.get('PORT', 8000))
    server = http.server.HTTPServer(('0.0.0.0', port), _HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info('Health-check server listening on port %d', port)

# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #

def main():
    if not TOKEN:
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN. Установите переменную окружения:\n"
            "  export TELEGRAM_BOT_TOKEN='ваш_токен_от_BotFather'"
        )

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("random", random_cmd))
    app.add_handler(CommandHandler("card", card_lookup_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_text))

    _start_health_server()
    logger.info("Bot started, polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
