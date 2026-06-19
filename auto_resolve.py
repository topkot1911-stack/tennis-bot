"""
Auto-resolve — автоматическое проставление outcome для прогнозов в БД.

Логика:
1. Берёт все прогнозы с outcome=NULL за последние 7 дней
2. Для каждого качает результаты матчей за ту дату через result_fetcher
3. Матчит по нормализованным именам игроков
4. Если нашли — set_outcome(pid, fav_won)

Запускается:
- При старте бота (один раз — для миграции)
- По расписанию каждый день в 03:00 UTC (cron)
- Вручную через /resolveday команду (для админа)
"""

import logging
from datetime import date, timedelta
from typing import Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

import database as db
from name_normalizer import normalize_player_name
from result_fetcher import fetch_results_for_sport

logger = logging.getLogger(__name__)


def auto_resolve_predictions(days_back: int = 7) -> Tuple[int, int]:
    """
    Пытается разрешить все нерезолвенные прогнозы за последние N дней.

    Returns:
        (resolved_count, total_unresolved): сколько закрыли / сколько всего было
    """
    cutoff = (date.today() - timedelta(days=days_back)).isoformat()

    # 1. Берём все нерезолвенные прогнозы
    with psycopg2.connect(db.DATABASE_URL, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, date, p1, p2, fav, sport
                FROM predictions
                WHERE outcome IS NULL AND date >= %s
                ORDER BY date DESC
            """, (cutoff,))
            unresolved = [dict(r) for r in cur.fetchall()]

    if not unresolved:
        logger.info("Auto-resolve: нет нерезолвенных прогнозов")
        return (0, 0)

    logger.info(f"Auto-resolve: найдено {len(unresolved)} нерезолвенных прогнозов")

    # 2. Группируем по (sport, date) — чтобы не качать один URL много раз
    by_date_sport = {}
    for pred in unresolved:
        key = (pred["sport"], str(pred["date"]))
        by_date_sport.setdefault(key, []).append(pred)

    resolved_count = 0

    for (sport, target_date), preds_group in by_date_sport.items():
        logger.info(f"Auto-resolve: {sport} {target_date} — {len(preds_group)} прогнозов")

        results = fetch_results_for_sport(sport, target_date)
        if not results:
            logger.warning(f"Нет результатов для {sport} {target_date}")
            continue

        # Нормализуем имена в результатах один раз
        for r in results:
            r["_p1_norm"] = normalize_player_name(r["p1"], sport)
            r["_p2_norm"] = normalize_player_name(r["p2"], sport)
            r["_winner_norm"] = normalize_player_name(r["winner"], sport)

        # 3. Матчим каждый прогноз
        for pred in preds_group:
            pred_p1 = pred["p1"]  # в БД они УЖЕ нормализованы (новый код это делает)
            pred_p2 = pred["p2"]
            pred_fav = pred["fav"]

            matched = None
            for r in results:
                # Проверяем что оба игрока совпадают (в любом порядке)
                pred_pair = {pred_p1.lower(), pred_p2.lower()}
                result_pair = {r["_p1_norm"].lower(), r["_p2_norm"].lower()}
                if pred_pair == result_pair:
                    matched = r
                    break
                # Fallback: проверяем по подстроке (на случай если нормализация
                # промахнулась)
                p1_lower = pred_p1.lower()
                p2_lower = pred_p2.lower()
                r_p1_lower = r["_p1_norm"].lower()
                r_p2_lower = r["_p2_norm"].lower()
                if ((p1_lower in r_p1_lower or r_p1_lower in p1_lower) and
                    (p2_lower in r_p2_lower or r_p2_lower in p2_lower)) or \
                   ((p1_lower in r_p2_lower or r_p2_lower in p1_lower) and
                    (p2_lower in r_p1_lower or r_p1_lower in p2_lower)):
                    matched = r
                    break

            if not matched:
                logger.info(f"⏭  Не нашли матч для id={pred['id']}: "
                            f"{pred_p1} vs {pred_p2}")
                continue

            # 4. Определяем выиграл ли фаворит
            winner_lower = matched["_winner_norm"].lower()
            fav_lower = pred_fav.lower()
            fav_won = (winner_lower == fav_lower or
                       fav_lower in winner_lower or winner_lower in fav_lower)

            success = db.set_outcome(pred["id"], fav_won)
            if success:
                resolved_count += 1
                logger.info(f"✅ id={pred['id']}: {pred_p1} vs {pred_p2} → "
                            f"{matched['winner']} won (fav_won={fav_won})")

    logger.info(f"Auto-resolve итого: {resolved_count} / {len(unresolved)} закрыто")
    return (resolved_count, len(unresolved))


def resolve_one_day(target_date: str) -> Tuple[int, int]:
    """Wrapper для разрешения прогнозов конкретной даты (для /resolveday)."""
    # Используем общую функцию но смотрим ровно на 1 день
    today = date.today()
    target = date.fromisoformat(target_date)
    days_back = (today - target).days + 1
    if days_back < 1:
        days_back = 1
    return auto_resolve_predictions(days_back=days_back)
