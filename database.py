"""
Database layer — Postgres-backed persistent storage.

ИЗМЕНЕНИЯ от старой версии (SQLite на /tmp/):
  ✅ Postgres вместо SQLite — данные не теряются при деплое
  ✅ Валидация при save_prediction — не сохраняем мусор (p1="?" и т.д.)
  ✅ Дедупликация — один матч в день сохраняется ОДИН РАЗ (повтор обновляет)
  ✅ Нормализация имён игроков — устраняет дубли типа "А. Зверев"/"Зверев"

API (тот же что был) — менять `bot.py` НЕ НУЖНО:
  save_prediction(p1, p2, prob, fav, tournament, confidence, sport="tennis")
  set_outcome(pid, fav_won)
  find_prediction(name, target_date=None)
  get_accuracy_stats(days=30)
  get_predictions(target_date=None)
  add_vip / remove_vip / is_vip / get_all_vips
  add_follow / remove_follow / get_follows
  get_usage / increment_usage
  set_language / get_language
  get_all_predictions / get_prediction_stats / export_csv / export_full_json
"""

import os
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import sql

from name_normalizer import normalize_player_name, is_valid_prediction

logger = logging.getLogger(__name__)

# Railway автоматически даёт DATABASE_URL когда Postgres прицеплен к сервису
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не установлен. "
        "Прикрепи Postgres-сервис в Railway к этому сервису через 'Service Variables → Add Reference'."
    )


def _conn():
    """Возвращает соединение с автоматическим словарным курсором."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def init_db():
    """Создаёт таблицы если их нет. Идемпотентно — можно вызывать каждый рестарт."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    p1 TEXT NOT NULL,
                    p2 TEXT NOT NULL,
                    prob REAL NOT NULL,
                    fav TEXT NOT NULL,
                    tournament TEXT,
                    confidence TEXT,
                    sport TEXT DEFAULT 'tennis',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    outcome INTEGER,
                    resolved_at TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions(date);
                CREATE INDEX IF NOT EXISTS idx_predictions_resolved ON predictions(outcome) WHERE outcome IS NULL;
                CREATE INDEX IF NOT EXISTS idx_predictions_players ON predictions(date, p1, p2);

                CREATE TABLE IF NOT EXISTS vip_users (
                    user_id BIGINT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS follows (
                    user_id BIGINT,
                    player TEXT,
                    PRIMARY KEY (user_id, player)
                );

                CREATE TABLE IF NOT EXISTS usage (
                    user_id BIGINT,
                    date DATE,
                    count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                );

                CREATE TABLE IF NOT EXISTS user_lang (
                    user_id BIGINT PRIMARY KEY,
                    lang TEXT DEFAULT 'ru'
                );
            """)
        conn.commit()


# ──────────────────────── PREDICTIONS ────────────────────────

def save_prediction(p1, p2, prob, fav, tournament, confidence, sport="tennis"):
    """
    Сохраняет прогноз с:
      - Валидацией (отбрасывает мусор с "?")
      - Нормализацией имён (один игрок = одно имя в БД)
      - Дедупликацией (тот же матч в тот же день обновляет prob, а не создаёт новый)

    Returns:
        id вставленной/обновлённой записи, или None если прогноз отбракован
    """
    # 1. ВАЛИДАЦИЯ
    if not is_valid_prediction(p1, p2, fav, prob):
        logger.warning(
            f"❌ Отброшен невалидный прогноз: p1={p1!r}, p2={p2!r}, "
            f"fav={fav!r}, prob={prob!r}"
        )
        return None

    # 2. НОРМАЛИЗАЦИЯ имён
    p1_norm = normalize_player_name(p1, sport)
    p2_norm = normalize_player_name(p2, sport)
    fav_norm = normalize_player_name(fav, sport)

    today = date.today().isoformat()

    with _conn() as conn:
        with conn.cursor() as cur:
            # 3. ДЕДУПЛИКАЦИЯ — проверяем есть ли уже такой матч сегодня
            cur.execute("""
                SELECT id FROM predictions
                WHERE date = %s
                  AND ((p1 = %s AND p2 = %s) OR (p1 = %s AND p2 = %s))
                ORDER BY id DESC
                LIMIT 1
            """, (today, p1_norm, p2_norm, p2_norm, p1_norm))
            existing = cur.fetchone()

            if existing:
                # Уже есть — ОБНОВЛЯЕМ (последний прогноз перезаписывает)
                cur.execute("""
                    UPDATE predictions
                    SET prob = %s, fav = %s, tournament = %s, confidence = %s,
                        created_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (prob, fav_norm, tournament, confidence, existing["id"]))
                conn.commit()
                logger.info(f"♻️  Обновлён прогноз id={existing['id']}: {p1_norm} vs {p2_norm}")
                return existing["id"]
            else:
                # Нет — ВСТАВЛЯЕМ новый
                cur.execute("""
                    INSERT INTO predictions
                        (date, p1, p2, prob, fav, tournament, confidence, sport)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (today, p1_norm, p2_norm, prob, fav_norm,
                      tournament, confidence, sport))
                pid = cur.fetchone()["id"]
                conn.commit()
                logger.info(f"✅ Создан прогноз id={pid}: {p1_norm} vs {p2_norm} "
                            f"({prob*100:.0f}% {fav_norm})")
                return pid


def set_outcome(prediction_id: int, fav_won: bool) -> bool:
    """Помечает прогноз как разрешённый. Возвращает True если строка обновилась."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE predictions
                SET outcome = %s, resolved_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (1 if fav_won else 0, prediction_id))
            updated = cur.rowcount
        conn.commit()
    return updated > 0


def find_prediction(p1_or_p2: str, target_date: str = None):
    """Найти прогноз по имени игрока. По умолчанию ищет сегодняшние."""
    if target_date is None:
        target_date = date.today().isoformat()
    # Нормализуем поисковый запрос — если ищем "медведев" найдёт "Медведев"
    needle = normalize_player_name(p1_or_p2)

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM predictions
                WHERE date = %s AND (p1 ILIKE %s OR p2 ILIKE %s OR fav ILIKE %s)
                ORDER BY id DESC
            """, (target_date, f"%{needle}%", f"%{needle}%", f"%{needle}%"))
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_accuracy_stats(days: int = 30) -> dict:
    """
    Hit-rate + Brier + per-sport breakdown за последние `days`.
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT prob, outcome, sport FROM predictions
                WHERE date >= %s AND outcome IS NOT NULL
            """, (cutoff,))
            rows = cur.fetchall()

    total = len(rows)
    if total == 0:
        return {"total": 0, "hit_rate": None, "brier": None, "by_sport": {}, "days": days}

    correct = 0
    brier_sum = 0.0
    by_sport = {}
    for r in rows:
        prob = float(r["prob"] or 0.5)
        actual = int(r["outcome"])
        if actual == 1:
            correct += 1
        brier_sum += (prob - actual) ** 2
        s = r["sport"] or "tennis"
        slot = by_sport.setdefault(s, {"total": 0, "correct": 0, "brier_sum": 0.0})
        slot["total"] += 1
        slot["correct"] += actual
        slot["brier_sum"] += (prob - actual) ** 2

    for s, slot in by_sport.items():
        slot["hit_rate"] = round(slot["correct"] / slot["total"], 3)
        slot["brier"] = round(slot["brier_sum"] / slot["total"], 3)
        del slot["brier_sum"]

    return {
        "total": total,
        "resolved": total,
        "correct": correct,
        "hit_rate": round(correct / total, 3),
        "brier": round(brier_sum / total, 3),
        "by_sport": by_sport,
        "days": days,
    }


def get_predictions(target_date=None):
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM predictions WHERE date = %s", (target_date,))
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_all_predictions():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM predictions ORDER BY date DESC, id DESC")
            rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_prediction_stats():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM predictions")
            total = cur.fetchone()["c"]
            cur.execute("""
                SELECT date::text AS date, COUNT(*) AS c FROM predictions
                GROUP BY date ORDER BY date DESC LIMIT 14
            """)
            by_date = cur.fetchall()
    return {"total": total, "by_date": [dict(r) for r in by_date]}


# ──────────────────────── VIP ────────────────────────

def add_vip(user_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO vip_users (user_id) VALUES (%s)
                ON CONFLICT (user_id) DO NOTHING
            """, (user_id,))
        conn.commit()


def remove_vip(user_id):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vip_users WHERE user_id = %s", (user_id,))
        conn.commit()


def is_vip(user_id) -> bool:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM vip_users WHERE user_id = %s", (user_id,))
            return cur.fetchone() is not None


def get_all_vips() -> set:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM vip_users")
            return {r["user_id"] for r in cur.fetchall()}


# ──────────────────────── FOLLOWS ────────────────────────

def add_follow(user_id, player):
    player_norm = normalize_player_name(player)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO follows (user_id, player) VALUES (%s, %s)
                ON CONFLICT (user_id, player) DO NOTHING
            """, (user_id, player_norm))
        conn.commit()


def remove_follow(user_id, player):
    player_norm = normalize_player_name(player)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM follows WHERE user_id = %s AND player = %s",
                        (user_id, player_norm))
        conn.commit()


def get_follows(user_id) -> set:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT player FROM follows WHERE user_id = %s", (user_id,))
            return {r["player"] for r in cur.fetchall()}


# ──────────────────────── USAGE ────────────────────────

def get_usage(user_id) -> int:
    today = date.today().isoformat()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count FROM usage WHERE user_id = %s AND date = %s",
                        (user_id, today))
            row = cur.fetchone()
    return row["count"] if row else 0


def increment_usage(user_id):
    today = date.today().isoformat()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO usage (user_id, date, count) VALUES (%s, %s, 1)
                ON CONFLICT (user_id, date) DO UPDATE SET count = usage.count + 1
            """, (user_id, today))
        conn.commit()


# ──────────────────────── LANGUAGE ────────────────────────

def set_language(user_id, lang):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_lang (user_id, lang) VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET lang = EXCLUDED.lang
            """, (user_id, lang))
        conn.commit()


def get_language(user_id) -> str:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT lang FROM user_lang WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    return row["lang"] if row else "ru"


# ──────────────────────── EXPORT ────────────────────────

def export_csv() -> str:
    rows = get_all_predictions()
    if not rows:
        return ""
    lines = ["date,p1,p2,probability,favorite,tournament,confidence,sport,outcome"]
    for r in rows:
        out = "" if r.get("outcome") is None else str(r["outcome"])
        lines.append(
            f"{r['date']},{r['p1']},{r['p2']},{r['prob']},{r['fav']},"
            f"{r.get('tournament','')},{r.get('confidence','')},"
            f"{r.get('sport','tennis')},{out}"
        )
    return "\n".join(lines)


def export_full_json() -> str:
    """Полный экспорт БД для анализа."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM predictions ORDER BY date DESC, id DESC")
            preds = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM vip_users")
            vips = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM follows")
            follows = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT * FROM usage ORDER BY date DESC LIMIT 30")
            usage = [dict(r) for r in cur.fetchall()]

    stats = get_prediction_stats()
    return json.dumps({
        "export_date": date.today().isoformat(),
        "total_predictions": stats["total"],
        "predictions_by_date": stats["by_date"],
        "all_predictions": preds,
        "vip_users": vips,
        "follows": follows,
        "recent_usage": usage,
    }, ensure_ascii=False, indent=2, default=str)


# Инициализация при импорте — Railway-friendly
init_db()
