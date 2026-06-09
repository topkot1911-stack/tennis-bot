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

from telegram import Update, BotCommand, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

from datetime import date

from config import TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, PDF_DIR
from analyzer import analyze_match, format_summary, analyze_cs2, format_cs2_summary, analyze_dota2, format_dota2_summary
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

def _lang_suffix(user_id):
    """Return language instruction for Claude prompts."""
    lang = db.get_language(user_id)
    if lang == "en":
        return "\n\nIMPORTANT: Respond ENTIRELY in English. All text, profiles, factors, verdict — in English."
    return "\n\nВАЖНО: Отвечай ПОЛНОСТЬЮ на русском языке."


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch bot language."""
    user_id = update.effective_user.id
    args = context.args

    if not args:
        current = db.get_language(user_id)
        await update.message.reply_text(
            f"🌍 <b>Language / Язык</b>\n\n"
            f"Current: <b>{'🇷🇺 Русский' if current == 'ru' else '🇬🇧 English'}</b>\n\n"
            f"Switch / Переключить:\n"
            f"  <code>/lang en</code> — English\n"
            f"  <code>/lang ru</code> — Русский",
            parse_mode=ParseMode.HTML,
        )
        return

    lang = args[0].lower()
    if lang in ("en", "eng", "english"):
        db.set_language(user_id, "en")
        await update.message.reply_text("🇬🇧 Language set to <b>English</b>. All analyses will be in English now.", parse_mode=ParseMode.HTML)
    elif lang in ("ru", "rus", "russian", "русский"):
        db.set_language(user_id, "ru")
        await update.message.reply_text("🇷🇺 Язык установлен: <b>Русский</b>. Все анализы будут на русском.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Use: <code>/lang en</code> or <code>/lang ru</code>", parse_mode=ParseMode.HTML)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start — show language selection buttons."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
        ]
    ])
    await update.message.reply_text(
        "🎾 <b>Welcome / Добро пожаловать!</b>\n\n"
        "Choose your language / Выберите язык:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle language selection from inline buttons."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = "en" if query.data == "lang_en" else "ru"
    db.set_language(user_id, lang)

    is_owner = user_id == OWNER_ID
    is_vip = db.is_vip(user_id)

    if lang == "en":
        limit_text = "♾ Unlimited access" if is_owner or is_vip else f"📊 {DAILY_FREE_LIMIT} free analyses per day"
        text = (
            "🎾 <b>Sports Analyst Bot</b>\n\n"
            "I analyze tennis, CS2 & Dota 2 matches with probability calculations, "
            "factor analysis and PDF reports.\n\n"
            "<b>Commands:</b>\n"
            "🎾 /analyze — Tennis analysis + PDF\n"
            "🎮 /cs2 — CS2 analysis\n"
            "⚔️ /dota2 — Dota 2 analysis\n"
            "⚡ /quick — Quick text analysis\n"
            "📅 /today — Today's matches\n"
            "🌍 /lang — Switch language\n"
            "❓ /help — All commands\n\n"
            f"{limit_text}\n\n"
            "<i>Methodology v3 | Claude AI + math model</i>"
        )
    else:
        limit_text = "♾ Безлимитный доступ" if is_owner or is_vip else f"📊 {DAILY_FREE_LIMIT} анализа в день бесплатно"
        text = (
            "🎾 <b>Sports Analyst Bot</b>\n\n"
            "Я анализирую матчи тенниса, CS2 и Dota 2 с расчётом вероятностей, "
            "факторным анализом и PDF-отчётами.\n\n"
            "<b>Команды:</b>\n"
            "🎾 /analyze — Теннис анализ + PDF\n"
            "🎮 /cs2 — CS2 анализ\n"
            "⚔️ /dota2 — Dota 2 анализ\n"
            "⚡ /quick — Быстрый анализ\n"
            "📅 /today — Матчи сегодня\n"
            "🌍 /lang — Сменить язык\n"
            "❓ /help — Все команды\n\n"
            f"{limit_text}\n\n"
            "<i>Методология v3 | Claude AI + мат. модель</i>"
        )

    await query.edit_message_text(text, parse_mode=ParseMode.HTML)


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
        data = await analyze_match(query, _lang_suffix(user_id))

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

        # Send summary
        await wait_msg.delete()
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

        # Generate and send PDF (skip if raw text fallback)
        if "_raw_text" not in data:
            await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
            pdf_path = generate_pdf(data)
            with open(pdf_path, "rb") as pdf_file:
                fav = data.get("player1", {}) if data.get("favorite", 1) == 1 else data.get("player2", {})
                dog = data.get("player2", {}) if data.get("favorite", 1) == 1 else data.get("player1", {})
                caption = (
                    f"📄 {fav.get('name', '')} vs {dog.get('name', '')}\n"
                    f"🏆 {fav.get('name', '')} {round(data.get('probability', 0.5) * 100)}%"
                )
                await update.message.reply_document(
                    document=pdf_file, filename=os.path.basename(pdf_path), caption=caption,
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

        data = await analyze_match(query, _lang_suffix(user_id))
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
    """Handle /today — fetch today's matches: tennis + CS2 + Dota 2."""
    from datetime import date as dt
    import anthropic
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    user_id = update.effective_user.id
    lang = db.get_language(user_id)

    await update.message.reply_chat_action(ChatAction.TYPING)
    wait_text = "📅 Searching for today's matches..." if lang == "en" else "📅 Ищу матчи на сегодня (теннис + CS2 + Dota 2)..."
    wait_msg = await update.message.reply_text(wait_text)

    try:
        today = dt.today().strftime("%d %B %Y")
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        if lang == "en":
            prompt = (
                f"Today is {today}. Search the web and find ALL notable matches happening TODAY across:\n\n"
                "1. TENNIS — ATP/WTA tournaments (Grand Slams, Masters, 500/250)\n"
                "2. CS2 — any ongoing tournaments (ESL, BLAST, PGL, IEM etc.)\n"
                "3. DOTA 2 — any ongoing tournaments (DPC, ESL, BetBoom etc.)\n\n"
                "For each match list: teams/players, tournament, round/stage, time if available.\n"
                "Group by sport. If no matches found for a sport — write 'No matches today'.\n"
                "At the end add:\n"
                "'For analysis: /analyze Player1 vs Player2 (tennis) | /cs2 Team1 vs Team2 | /dota2 Team1 vs Team2'\n"
                "Respond in English. NOT JSON — plain structured text."
            )
        else:
            prompt = (
                f"Сегодня {today}. Найди через веб-поиск ВСЕ значимые матчи СЕГОДНЯ по трём дисциплинам:\n\n"
                "1. ТЕННИС — ATP/WTA турниры (Grand Slam, Masters, 500/250)\n"
                "2. CS2 — текущие турниры (ESL, BLAST, PGL, IEM и т.д.)\n"
                "3. DOTA 2 — текущие турниры (DPC, ESL, BetBoom и т.д.)\n\n"
                "Для каждого матча укажи: команды/игроки, турнир, стадия, время.\n"
                "Группируй по виду спорта. Если матчей нет — напиши 'Нет матчей сегодня'.\n"
                "В конце добавь:\n"
                "'Для анализа: /analyze Игрок1 vs Игрок2 (теннис) | /cs2 Команда1 vs Команда2 | /dota2 Команда1 vs Команда2'\n"
                "Ответ на русском. НЕ JSON — структурированный текст."
            )

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=6000,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": prompt}],
        )

        text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text += block.text

        if not text.strip():
            text = "No matches found." if lang == "en" else "Не удалось найти расписание."

        await wait_msg.delete()

        header = f"📅 <b>Today's matches ({today})</b>" if lang == "en" else f"📅 <b>Матчи сегодня ({today})</b>"

        # Split into chunks if too long for Telegram
        full_text = f"{header}\n\n{text}"
        if len(full_text) > 4000:
            full_text = full_text[:3997] + "..."

        await update.message.reply_text(full_text, parse_mode=ParseMode.HTML)

        # Generate PDF with today's schedule
        try:
            await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
            from pdf_generator import generate_today_pdf
            pdf_path = generate_today_pdf(text, today, lang)
            with open(pdf_path, "rb") as f:
                cap = f"📅 Schedule {today}" if lang == "en" else f"📅 Расписание {today}"
                await update.message.reply_document(document=f, filename=os.path.basename(pdf_path), caption=cap)
            try: os.remove(pdf_path)
            except OSError: pass
        except Exception as pdf_err:
            logger.error(f"Today PDF error: {pdf_err}")

    except Exception as e:
        logger.error(f"Today error: {e}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Error: {str(e)[:200]}")


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


async def cmd_cs2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cs2 command — CS2 match analysis."""
    user_id = update.effective_user.id
    query = " ".join(context.args) if context.args else ""

    if not query:
        await update.message.reply_text(
            "🎮 <b>CS2 Analyst</b>\n\n"
            "Пример: <code>/cs2 NAVI vs Spirit, PGL Major QF</code>\n"
            "Или: <code>/cs2 G2 vs FaZe</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    allowed, remaining = _check_limit(user_id)
    if not allowed:
        await update.message.reply_text(
            f"⛔ Лимит исчерпан. /subscribe для безлимита.",
        )
        return

    if user_id in _active_users:
        await update.message.reply_text("⏳ Подожди, предыдущий запрос выполняется.")
        return

    _active_users.add(user_id)
    _use_request(user_id)
    try:
        await update.message.reply_chat_action(ChatAction.TYPING)
        wait_msg = await update.message.reply_text(
            f"🎮 Анализирую CS2: <b>{query}</b>\n\n"
            "Ищу данные на HLTV.org, map pool, H2H...",
            parse_mode=ParseMode.HTML,
        )

        data = await analyze_cs2(query, _lang_suffix(user_id))
        summary = format_cs2_summary(data)

        # Save prediction
        try:
            t1 = data.get("team1", {})
            t2 = data.get("team2", {})
            fav_idx = data.get("favorite", 1)
            favd = t1 if fav_idx == 1 else t2
            db.save_prediction(
                p1=t1.get("name", "?"), p2=t2.get("name", "?"),
                prob=data.get("probability", 0.5), fav=favd.get("name", "?"),
                tournament=data.get("tournament", "CS2"),
                confidence=data.get("confidence", "?"),
            )
        except Exception:
            pass

        await wait_msg.delete()
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

        if "_raw_text" not in data:
            await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
            pdf_path = generate_pdf(data)
            with open(pdf_path, "rb") as pdf_file:
                t1n = data.get("team1", {}).get("short", data.get("team1", {}).get("name", "T1"))
                t2n = data.get("team2", {}).get("short", data.get("team2", {}).get("name", "T2"))
                await update.message.reply_document(
                    document=pdf_file, filename=os.path.basename(pdf_path),
                    caption=f"🎮 CS2 | {t1n} vs {t2n}",
                )
            try: os.remove(pdf_path)
            except OSError: pass

    except Exception as e:
        logger.error(f"CS2 analysis error: {e}\n{traceback.format_exc()}")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(
            f"❌ Ошибка: <code>{str(e)[:200]}</code>\n\n"
            "Попробуй: <code>/cs2 NAVI vs Spirit, PGL Major</code>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        _active_users.discard(user_id)


async def cmd_dota2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /dota2 command — Dota 2 match analysis."""
    user_id = update.effective_user.id
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "⚔️ <b>Dota 2 Analyst</b>\n\n"
            "Пример: <code>/dota2 Spirit vs Falcons, TI QF</code>\n"
            "Или: <code>/dota2 Tundra vs Gaimin</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    allowed, _ = _check_limit(user_id)
    if not allowed:
        await update.message.reply_text(f"⛔ Лимит исчерпан. /subscribe для безлимита.")
        return
    if user_id in _active_users:
        await update.message.reply_text("⏳ Подожди.")
        return
    _active_users.add(user_id)
    _use_request(user_id)
    try:
        await update.message.reply_chat_action(ChatAction.TYPING)
        wait_msg = await update.message.reply_text(
            f"⚔️ Анализирую Dota 2: <b>{query}</b>\n\nИщу данные на Liquipedia, dotabuff...",
            parse_mode=ParseMode.HTML,
        )
        data = await analyze_dota2(query, _lang_suffix(user_id))
        summary = format_dota2_summary(data)
        try:
            t1 = data.get("team1", {}); t2 = data.get("team2", {})
            fav_idx = data.get("favorite", 1)
            favd = t1 if fav_idx == 1 else t2
            db.save_prediction(p1=t1.get("name","?"), p2=t2.get("name","?"),
                prob=data.get("probability",0.5), fav=favd.get("name","?"),
                tournament=data.get("tournament","Dota2"), confidence=data.get("confidence","?"))
        except Exception: pass
        await wait_msg.delete()
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)
        if "_raw_text" not in data:
            await update.message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
            pdf_path = generate_pdf(data)
            with open(pdf_path, "rb") as pdf_file:
                t1n = data.get("team1", {}).get("short", data.get("team1", {}).get("name", "T1"))
                t2n = data.get("team2", {}).get("short", data.get("team2", {}).get("name", "T2"))
                await update.message.reply_document(
                    document=pdf_file, filename=os.path.basename(pdf_path),
                    caption=f"⚔️ Dota 2 | {t1n} vs {t2n}",
                )
            try: os.remove(pdf_path)
            except OSError: pass
    except Exception as e:
        logger.error(f"Dota2 error: {e}\n{traceback.format_exc()}")
        try: await wait_msg.delete()
        except Exception: pass
        await update.message.reply_text(
            f"❌ Ошибка: <code>{str(e)[:200]}</code>\n\nПопробуй: <code>/dota2 Spirit vs Falcons</code>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        _active_users.discard(user_id)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: export all predictions as CSV file."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Только владелец может экспортировать данные.")
        return

    csv_data = db.export_csv()
    if not csv_data:
        await update.message.reply_text("📋 Нет сохранённых прогнозов.")
        return

    # Send as file
    import io
    f = io.BytesIO(csv_data.encode("utf-8"))
    f.name = "predictions_export.csv"
    stats = db.get_prediction_stats()
    await update.message.reply_document(
        document=f,
        filename="predictions_export.csv",
        caption=f"📊 Экспорт: {stats['total']} прогнозов\n"
                f"Скинь этот файл в Cowork для анализа точности и улучшения модели.",
    )


async def cmd_accuracy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show prediction accuracy statistics."""
    stats = db.get_prediction_stats()

    if stats["total"] == 0:
        await update.message.reply_text("📊 Пока нет прогнозов для статистики.")
        return

    lines = [f"📊 <b>Статистика прогнозов</b>\n", f"Всего прогнозов: {stats['total']}\n"]
    lines.append("<b>По дням:</b>")
    for day in stats["by_date"][:10]:
        lines.append(f"  {day['date']}: {day['c']} анализов")
    lines.append(f"\nДля проверки точности: /results")
    lines.append(f"Для экспорта: /export (владелец)")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


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
        BotCommand("cs2", "🎮 CS2 анализ"),
        BotCommand("dota2", "⚔️ Dota 2 анализ"),
        BotCommand("lang", "🌍 Language / Язык"),
        BotCommand("subscribe", "⭐ VIP подписка"),
        BotCommand("accuracy", "📊 Статистика прогнозов"),
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
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern="^lang_"))
    app.add_handler(CommandHandler("cs2", cmd_cs2))
    app.add_handler(CommandHandler("dota2", cmd_dota2))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("accuracy", cmd_accuracy))
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
