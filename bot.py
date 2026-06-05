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

from telegram import Update, BotCommand, LabeledPrice
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

from datetime import date

from config import TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, PDF_DIR
from analyzer import analyze_match, format_summary
from pdf_generator import generate_pdf
import database as db

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

# All persistent data (VIP, predictions, follows, usage) stored in SQLite via database.py


def _check_limit(user_id: int) -> tuple[bool, int]:
    """Check if user can make a request. Returns (allowed, remaining)."""
    if user_id == OWNER_ID or db.is_vip(user_id):
        return True, 999
    used = db.get_usage(user_id)
    remaining = DAILY_FREE_LIMIT - used
    return (True, remaining) if remaining > 0 else (False, 0)


def _use_request(user_id: int):
    """Record a request usage."""
    if user_id == OWNER_ID or db.is_vip(user_id):
        return
    db.increment_usage(user_id)


# ═══════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user_id = update.effective_user.id
    is_owner = user_id == OWNER_ID
    limit_text = "♾ Безлимитный доступ" if is_owner or db.is_vip(user_id) else f"📊 {DAILY_FREE_LIMIT} анализов в день бесплатно"
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
        "<b>Анализ:</b>\n"
        "📊 /analyze [матч] — PDF-отчёт (3 стр.)\n"
        "⚡ /quick [матч] — текстовый анализ\n"
        "📅 /today — все матчи сегодня\n\n"
        "<b>Отслеживание:</b>\n"
        "⭐ /follow [игрок] — добавить в избранные\n"
        "❌ /unfollow [игрок] — убрать из избранных\n"
        "📋 /results — проверка вчерашних прогнозов\n\n"
        "<b>Аккаунт:</b>\n"
        "📊 /mystats — мой лимит запросов\n\n"
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
            f"⛔ Дневной лимит исчерпан ({DAILY_FREE_LIMIT} анализа в день).\n\n"
            f"⭐ Безлимит — /subscribe ({VIP_PRICE_STARS} Stars/мес)",
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

        # Save prediction to database for /results verification
        try:
            p1d = data.get("player1", {})
            p2d = data.get("player2", {})
            fav_idx = data.get("favorite", 1)
            favd = p1d if fav_idx == 1 else p2d
            db.save_prediction(
                p1=p1d.get("name", "?"), p2=p2d.get("name", "?"),
                prob=data.get("probability", 0.5), fav=favd.get("name", "?"),
                tournament=data.get("tournament", "?"),
                confidence=data.get("confidence", "?"),
            )
        except Exception:
            pass

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
    is_vip = db.is_vip(user_id)

    used = db.get_usage(user_id)

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


# Price in Telegram Stars (1 Star ≈ 1.5-2 rub)
VIP_PRICE_STARS = int(os.getenv("VIP_PRICE_STARS", "250"))


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show VIP plan and send Stars payment invoice."""
    user_id = update.effective_user.id
    if user_id == OWNER_ID or db.is_vip(user_id):
        await update.message.reply_text("⭐ У тебя уже есть VIP-доступ! Безлимитные анализы.")
        return

    # Send invoice via Telegram Stars
    await update.message.reply_invoice(
        title="Tennis Analyst VIP",
        description=(
            "Безлимитные анализы матчей на 30 дней:\n"
            "- Безлимитные /analyze + PDF (3 стр.)\n"
            "- Проверка прогнозов (/results)\n"
            "- Избранные игроки (/follow)\n"
            "- Приоритетная поддержка"
        ),
        payload=f"vip_monthly_{user_id}",
        provider_token="",  # Empty for Telegram Stars
        currency="XTR",
        prices=[LabeledPrice("VIP подписка (30 дней)", VIP_PRICE_STARS)],
    )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve the payment before it's processed."""
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle successful Stars payment — activate VIP."""
    user_id = update.effective_user.id
    payment = update.message.successful_payment

    logger.info(f"Payment received: user={user_id}, amount={payment.total_amount} Stars")

    # Activate VIP in database
    db.add_vip(user_id)

    await update.message.reply_text(
        "🎉 <b>VIP активирован!</b>\n\n"
        f"⭐ Оплачено: {payment.total_amount} Stars\n"
        "📅 Доступ: 30 дней\n\n"
        "Теперь тебе доступно:\n"
        "• Безлимитные /analyze + 3-стр. PDF\n"
        "• /results — проверка прогнозов\n"
        "• /follow — избранные игроки\n\n"
        "Проверь статус: /mystats",
        parse_mode=ParseMode.HTML,
    )

    # Notify owner
    if OWNER_ID:
        try:
            name = update.effective_user.first_name or "Unknown"
            await context.bot.send_message(
                OWNER_ID,
                f"💰 Новая VIP-оплата!\n"
                f"User: {name} (ID: {user_id})\n"
                f"Сумма: {payment.total_amount} Stars",
            )
        except Exception:
            pass


async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: grant/revoke VIP to a user. Usage: /vip 123456789"""
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ Только владелец может управлять VIP.")
        return

    args = context.args
    if not args:
        all_vips = db.get_all_vips()
        vip_list = ", ".join(str(v) for v in all_vips) if all_vips else "нет"
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
            db.remove_vip(target)
            await update.message.reply_text(f"✅ Пользователь {target} удалён из VIP.")
        except ValueError:
            await update.message.reply_text("❌ Неверный ID.")
    else:
        try:
            target = int(args[0])
            db.add_vip(target)
            await update.message.reply_text(f"✅ Пользователь {target} добавлен в VIP! ♾")
        except ValueError:
            await update.message.reply_text("❌ Неверный ID. Используй: /vip 123456789")


async def cmd_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check yesterday's predictions against actual results via web search."""
    from datetime import timedelta
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    user_id = update.effective_user.id
    if user_id != OWNER_ID and not db.is_vip(user_id):
        await update.message.reply_text(
            "⭐ Проверка прогнозов — функция VIP.\n"
            "Подключи VIP: /subscribe",
        )
        return

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    preds = db.get_predictions(yesterday)

    if not preds:
        await update.message.reply_text(
            "📋 Нет прогнозов за вчера для проверки.\n"
            "Сделай /analyze на предстоящий матч — завтра сможешь проверить!",
        )
        return

    await update.message.reply_chat_action(ChatAction.TYPING)
    wait_msg = await update.message.reply_text("🔍 Проверяю вчерашние прогнозы...")

    try:
        matches_text = "\n".join(
            f"- {p['p1']} vs {p['p2']}: прогноз {p['fav']} {round(p['prob']*100)}%"
            for p in preds
        )

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
            messages=[{"role": "user", "content":
                f"Найди результаты вчерашних теннисных матчей ({yesterday}) и сравни с прогнозами:\n"
                f"{matches_text}\n\n"
                "Для каждого матча напиши: счёт, кто выиграл, прогноз верный или нет.\n"
                "В конце: общая статистика X из Y верных. Ответ на русском, кратко."
            }],
        )

        text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text += block.text

        await wait_msg.delete()
        if len(text) > 4000:
            text = text[:3997] + "..."
        await update.message.reply_text(
            f"📋 <b>Проверка прогнозов за {yesterday}</b>\n\n{text}",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Results error: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")


async def cmd_follow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Follow a player. Usage: /follow Zverev"""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        followed = db.get_follows(user_id)
        if followed:
            players = ", ".join(sorted(followed))
            await update.message.reply_text(
                f"⭐ <b>Твои избранные игроки:</b>\n{players}\n\n"
                "Добавить: <code>/follow Zverev</code>\n"
                "Удалить: <code>/unfollow Zverev</code>",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(
                "У тебя пока нет избранных игроков.\n"
                "Добавь: <code>/follow Zverev</code>",
                parse_mode=ParseMode.HTML,
            )
        return

    player = " ".join(args)
    db.add_follow(user_id, player)
    follows = db.get_follows(user_id)
    await update.message.reply_text(
        f"⭐ <b>{player}</b> добавлен в избранные!\n\n"
        f"Всего избранных: {len(follows)}\n"
        "Используй /today — матчи избранных будут отмечены.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_unfollow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unfollow a player. Usage: /unfollow Zverev"""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        await update.message.reply_text("Используй: <code>/unfollow Zverev</code>", parse_mode=ParseMode.HTML)
        return

    player = " ".join(args)
    if player in db.get_follows(user_id):
        db.remove_follow(user_id, player)
        await update.message.reply_text(f"✅ {player} удалён из избранных.")
    else:
        await update.message.reply_text(f"❌ {player} не найден в избранных.")


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
        BotCommand("analyze", "📊 Полный анализ + PDF"),
        BotCommand("quick", "⚡ Быстрый анализ"),
        BotCommand("today", "📅 Матчи сегодня"),
        BotCommand("results", "📋 Проверка прогнозов"),
        BotCommand("follow", "⭐ Избранные игроки"),
        BotCommand("subscribe", "⭐ VIP подписка"),
        BotCommand("mystats", "📊 Мой лимит"),
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
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(CommandHandler("results", cmd_results))
    app.add_handler(CommandHandler("follow", cmd_follow))
    app.add_handler(CommandHandler("unfollow", cmd_unfollow))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Bot is running! Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
