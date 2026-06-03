"""
Tennis Analyst Telegram Bot
Публичный бот для анализа теннисных матчей с Claude API.

Команды:
  /start    — Приветствие и инструкции
  /help     — Помощь по командам
  /analyze  — Анализ матча (с PDF)
  /quick    — Быстрый анализ (без PDF)
  /today    — Матчи сегодня (заглушка для будущего)

Примеры:
  /analyze Зверев vs Ходар, RG2026 QF
  /analyze Fonseca vs Mensik
  /quick Andreeva vs Cirstea WTA
"""

import asyncio
import logging
import os
import traceback

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

from config import TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, PDF_DIR
from analyzer import analyze_match, format_summary
from pdf_generator import generate_pdf

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Track active analyses to prevent spam
_active_users = set()


# ═══════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    text = (
        "🎾 <b>Tennis Analyst Bot</b>\n\n"
        "Я анализирую теннисные матчи ATP и WTA с расчётом вероятностей, "
        "факторным анализом и генерацией PDF-отчётов.\n\n"
        "<b>Как использовать:</b>\n"
        "• /analyze Зверев vs Ходар, RG QF\n"
        "• /analyze Fonseca vs Mensik, Roland Garros\n"
        "• /quick Andreeva vs Cirstea\n\n"
        "Или просто напиши имена игроков:\n"
        "• <i>Фонсека Менсик</i>\n"
        "• <i>Zverev vs Jodar Roland Garros quarterfinal</i>\n\n"
        "📄 /analyze — полный анализ + PDF\n"
        "⚡ /quick — быстрый текстовый анализ\n"
        "❓ /help — все команды\n\n"
        "<i>Powered by Claude AI + математическая модель Bo3/Bo5</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    text = (
        "🎾 <b>Команды:</b>\n\n"
        "📊 /analyze [матч] — Полный анализ:\n"
        "   • Профили игроков\n"
        "   • Факторная корректировка\n"
        "   • Вероятности (победитель, сеты, тоталы)\n"
        "   • Стилистический разбор\n"
        "   • Сценарии матча\n"
        "   • PDF-отчёт\n\n"
        "⚡ /quick [матч] — Быстрый анализ:\n"
        "   • Текстовая сводка без PDF\n\n"
        "📝 <b>Формат запроса:</b>\n"
        "Указывай двух игроков и (опционально) турнир:\n"
        "   • <code>/analyze Зверев vs Ходар, RG2026 QF</code>\n"
        "   • <code>/analyze Fonseca Mensik French Open</code>\n"
        "   • <code>/quick Kostyuk Svitolina</code>\n\n"
        "🌍 Работает на русском и английском.\n\n"
        "<i>⚠️ Это исследовательский анализ, не рекомендация по ставкам.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyze command — full analysis with PDF."""
    user_id = update.effective_user.id
    query = " ".join(context.args) if context.args else ""

    if not query:
        await update.message.reply_text(
            "❌ Укажи матч для анализа.\n"
            "Пример: <code>/analyze Зверев vs Ходар, RG QF</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if user_id in _active_users:
        await update.message.reply_text("⏳ Предыдущий анализ ещё выполняется. Подожди.")
        return

    _active_users.add(user_id)
    try:
        await update.message.reply_chat_action(ChatAction.TYPING)
        wait_msg = await update.message.reply_text(
            f"🔍 Анализирую: <b>{query}</b>\n\n"
            "⏳ Сбор данных, расчёт вероятностей, генерация PDF...\n"
            "Обычно это занимает 30-60 секунд.",
            parse_mode=ParseMode.HTML,
        )

        # Run analysis
        data = await analyze_match(query)

        # Generate summary text
        summary = format_summary(data)

        # Generate PDF
        await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
        pdf_path = generate_pdf(data)

        # Send summary
        await wait_msg.delete()
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

        # Send PDF
        with open(pdf_path, "rb") as pdf_file:
            fav = data.get("player1", {}) if data.get("favorite", 1) == 1 else data.get("player2", {})
            dog = data.get("player2", {}) if data.get("favorite", 1) == 1 else data.get("player1", {})
            caption = (
                f"📄 Полный отчёт: {fav.get('name', '')} vs {dog.get('name', '')}\n"
                f"🏆 {fav.get('name', '')} {round(data.get('probability', 0.5) * 100)}%"
            )
            await update.message.reply_document(
                document=pdf_file,
                filename=os.path.basename(pdf_path),
                caption=caption,
            )

        # Cleanup
        try:
            os.remove(pdf_path)
        except OSError:
            pass

    except Exception as e:
        logger.error(f"Analysis error: {e}\n{traceback.format_exc()}")
        error_text = (
            f"❌ Ошибка анализа: <code>{str(e)[:200]}</code>\n\n"
            "Попробуй уточнить запрос, например:\n"
            "<code>/analyze Alexander Zverev vs Rafael Jodar, Roland Garros 2026 QF</code>"
        )
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(error_text, parse_mode=ParseMode.HTML)
    finally:
        _active_users.discard(user_id)


async def cmd_quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /quick command — text-only analysis without PDF."""
    user_id = update.effective_user.id
    query = " ".join(context.args) if context.args else ""

    if not query:
        await update.message.reply_text(
            "❌ Укажи матч.\nПример: <code>/quick Зверев vs Ходар</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if user_id in _active_users:
        await update.message.reply_text("⏳ Подожди, предыдущий запрос выполняется.")
        return

    _active_users.add(user_id)
    try:
        await update.message.reply_chat_action(ChatAction.TYPING)
        wait_msg = await update.message.reply_text(f"⚡ Быстрый анализ: <b>{query}</b>...", parse_mode=ParseMode.HTML)

        data = await analyze_match(query)
        summary = format_summary(data)

        await wait_msg.delete()
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error(f"Quick analysis error: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
    finally:
        _active_users.discard(user_id)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /today — placeholder for future schedule feature."""
    await update.message.reply_text(
        "📅 <b>Расписание дня</b>\n\n"
        "Эта функция в разработке. Пока используй:\n"
        "• /analyze [матч] — для анализа конкретного матча\n\n"
        "В будущих версиях бот будет автоматически показывать "
        "все матчи дня с анализом.",
        parse_mode=ParseMode.HTML,
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages as match queries."""
    text = update.message.text.strip()

    # Skip very short or command-like messages
    if len(text) < 5 or text.startswith("/"):
        return

    # Check if it looks like a match query (contains "vs" or two capitalized words)
    words = text.split()
    looks_like_match = (
        "vs" in text.lower() or
        "против" in text.lower() or
        (len(words) >= 2 and words[0][0].isupper() and words[-1][0].isupper())
    )

    if looks_like_match:
        # Treat as quick analysis
        context.args = words
        await cmd_quick(update, context)
    else:
        await update.message.reply_text(
            "🤔 Не понял запрос. Попробуй:\n"
            "• <code>/analyze Зверев vs Ходар</code>\n"
            "• <code>/quick Fonseca Mensik</code>\n"
            "• Или просто: <i>Зверев Ходар</i>",
            parse_mode=ParseMode.HTML,
        )


async def post_init(app: Application):
    """Set bot commands in Telegram menu."""
    commands = [
        BotCommand("analyze", "📊 Полный анализ матча + PDF"),
        BotCommand("quick", "⚡ Быстрый анализ (текст)"),
        BotCommand("today", "📅 Матчи сегодня"),
        BotCommand("help", "❓ Помощь"),
    ]
    await app.bot.set_my_commands(commands)


# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════

def main():
    """Start the bot."""
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN not set! Add it to .env file.")
        print("   Get a token from @BotFather in Telegram.")
        return

    if not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY not set! Add it to .env file.")
        print("   Get a key from console.anthropic.com")
        return

    os.makedirs(PDF_DIR, exist_ok=True)

    print("🎾 Tennis Analyst Bot starting...")
    print(f"   PDF directory: {PDF_DIR}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Bot is running! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
