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

from collections import defaultdict
from datetime import date

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
# RATE LIMITING
# ═══════════════════════════════════════════════════════════

# Owner ID — unlimited access (set via OWNER_ID env var or hardcode)
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# Free limit per user per day
DAILY_FREE_LIMIT = int(os.getenv("DAILY_FREE_LIMIT", "3"))

# VIP users (unlimited) — add user IDs here or via /vip command (owner only)
_vip_users: set = set()

# Usage tracking: {user_id: {"date": "2026-06-03", "count": 5}}
_usage: dict = defaultdict(lambda: {"date": "", "count": 0})


def _check_limit(user_id: int) -> tuple[bool, int]:
    """Check if user can make a request. Returns (allowed, remaining)."""
    # Owner — always unlimited
    if user_id == OWNER_ID:
        return True, 999

    # VIP — unlimited
    if user_id in _vip_users:
        return True, 999

    today = date.today().isoformat()
    usage = _usage[user_id]

    # Reset daily counter
    if usage["date"] != today:
        usage["date"] = today
        usage["count"] = 0

    remaining = DAILY_FREE_LIMIT - usage["count"]
    if remaining <= 0:
        return False, 0

    return True, remaining


def _use_request(user_id: int):
    """Record a request usage."""
    if user_id == OWNER_ID or user_id in _vip_users:
        return
    today = date.today().isoformat()
    usage = _usage[user_id]
    if usage["date"] != today:
        usage["date"] = today
        usage["count"] = 0
    usage["count"] += 1


# ═══════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    limit_text = "♾ Безлимитный доступ" if is_owner or user_id in _vip_users else f"📊 {DAILY_FREE_LIMIT} анализов в день бесплатно"
    text = (
        "🎾 <b>Tennis Analyst Bot</b>\n\n"
        "Я анализирую теннисные матчи ATP и WTA с расчётом вероятностей, "
        "факторным анализом и генерацией PDF-отчётов.\n\n"
        "<b>Как использовать:</b>\n"
        "• /analyze Зверев vs Ходар, RG QF\n"
        "• /analyze Fonseca vs Mensik, Roland Garros\n"
        "• /quick Andreeva vs Cirstea\n\n"
        "Или просто напиши имена игроков:\n"
        "• <i>Фонсека Менсик</i>\n\n"
        "📄 /analyze — полный анализ + PDF (3 стр.)\n"
        "⚡ /quick — быстрый текстовый анализ\n"
        "📅 /today — матчи сегодня\n"
        "📊 /mystats — мой лимит запросов\n"
        "❓ /help — все команды\n\n"
        f"{limit_text}\n\n"
        "<i>Методология v3 | Claude AI + Bo3/Bo5 модель</i>"
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

    # Rate limit check
    allowed, remaining = _check_limit(user_id)
    if not allowed:
        await update.message.reply_text(
            f"⛔ Дневной лимит исчерпан ({DAILY_FREE_LIMIT} анализов в день).\n"
            "Попробуй завтра или обратись к администратору за VIP-доступом.",
            parse_mode=ParseMode.HTML,
        )
        return

    if user_id in _active_users:
        await update.message.reply_text("⏳ Предыдущий анализ ещё выполняется. Подожди.")
        return

    _active_users.add(user_id)
    _use_request(user_id)
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

    # Rate limit check
    allowed, remaining = _check_limit(user_id)
    if not allowed:
        await update.message.reply_text(
            f"⛔ Дневной лимит исчерпан ({DAILY_FREE_LIMIT} анализов в день).\n"
            "Попробуй завтра!",
        )
        return

    if user_id in _active_users:
        await update.message.reply_text("⏳ Подожди, предыдущий запрос выполняется.")
        return

    _active_users.add(user_id)
    _use_request(user_id)
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
    """Handle /today — fetch today's tennis schedule via web search."""
    from datetime import date
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    await update.message.reply_chat_action(ChatAction.TYPING)
    wait_msg = await update.message.reply_text("📅 Ищу расписание матчей на сегодня...")

    try:
        today = date.today().strftime("%d %B %Y")
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }],
            messages=[{"role": "user", "content":
                f"Сегодня {today}. Найди через веб-поиск ВСЕ мужские и женские теннисные матчи, "
                "которые играются СЕГОДНЯ на крупных турнирах (Grand Slam, Masters, ATP/WTA). "
                "Для каждого матча укажи: игроки, турнир, раунд, корт, время. "
                "Ответ на русском языке, в виде структурированного текста (НЕ JSON). "
                "Если турнир один — перечисли все матчи по кортам. "
                "В конце добавь: 'Для анализа любого матча: /analyze Игрок1 vs Игрок2'."
            }],
        )

        text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text += block.text

        if not text.strip():
            text = "Не удалось найти расписание. Попробуйте позже."

        await wait_msg.delete()
        # Split long messages (Telegram limit 4096 chars)
        if len(text) > 4000:
            text = text[:3997] + "..."
        await update.message.reply_text(f"📅 <b>Матчи сегодня ({today})</b>\n\n{text}",
                                         parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Today error: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")


async def cmd_mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's daily usage stats."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    is_vip = user_id in _vip_users

    today = date.today().isoformat()
    usage = _usage[user_id]
    used = usage["count"] if usage["date"] == today else 0

    if is_owner:
        status = "👑 Владелец (безлимит)"
        remaining = "♾"
    elif is_vip:
        status = "⭐ VIP (безлимит)"
        remaining = "♾"
    else:
        status = "👤 Бесплатный"
        remaining = str(max(0, DAILY_FREE_LIMIT - used))

    await update.message.reply_text(
        f"📊 <b>Мой профиль</b>\n\n"
        f"Статус: {status}\n"
        f"Использовано сегодня: {used}\n"
        f"Осталось: {remaining}\n"
        f"Лимит в день: {DAILY_FREE_LIMIT if not (is_owner or is_vip) else '♾'}\n\n"
        f"ID: <code>{user_id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: grant/revoke VIP to a user. Usage: /vip 123456789"""
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ Только владелец может управлять VIP.")
        return

    args = context.args
    if not args:
        vip_list = ", ".join(str(v) for v in _vip_users) if _vip_users else "нет"
        await update.message.reply_text(
            f"⭐ <b>VIP пользователи:</b>\n{vip_list}\n\n"
            "Добавить: <code>/vip 123456789</code>\n"
            "Удалить: <code>/vip remove 123456789</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if args[0] == "remove" and len(args) > 1:
        try:
            target = int(args[1])
            _vip_users.discard(target)
            await update.message.reply_text(f"✅ Пользователь {target} удалён из VIP.")
        except ValueError:
            await update.message.reply_text("❌ Неверный ID.")
    else:
        try:
            target = int(args[0])
            _vip_users.add(target)
            await update.message.reply_text(f"✅ Пользователь {target} добавлен в VIP! ♾")
        except ValueError:
            await update.message.reply_text("❌ Неверный ID. Используй: /vip 123456789")


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
        BotCommand("mystats", "📊 Мой лимит запросов"),
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
    app.add_handler(CommandHandler("mystats", cmd_mystats))
    app.add_handler(CommandHandler("vip", cmd_vip))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Bot is running! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
