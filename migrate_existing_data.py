"""
Одноразовый скрипт миграции существующих 32 прогнозов из SQLite (json export) в Postgres.

ЗАПУСКАТЬ ОДИН РАЗ после деплоя нового кода. Шаги:
1. Сделать deploy с новым database.py (Postgres) — без миграции
2. Зайти в Railway Console
3. Запустить: `python migrate_existing_data.py`
4. Удалить этот файл из репо (или просто оставить — он идемпотентный)

Что делает:
- Применяет валидацию (отбрасывает 6 мусорных записей с "?")
- Нормализует имена игроков
- Применяет дедупликацию (4 дубля схлопываются в 1 запись)
- Импортирует чистые данные в Postgres

Ожидаемый результат: из 32 SQLite-записей → ~22 уникальных в Postgres
"""

import os
import json
from datetime import date

import psycopg2
from psycopg2.extras import RealDictCursor

from name_normalizer import normalize_player_name, is_valid_prediction

# Исходные данные — экспорт из SQLite из /tmp/tennis-bot.db
SOURCE_DATA = [
    {"id": 1, "date": "2026-06-15", "p1": "?", "p2": "?", "prob": 0.5, "fav": "?", "tournament": "?", "confidence": "?", "sport": "tennis"},
    {"id": 2, "date": "2026-06-15", "p1": "Андрей Рублёв", "p2": "Хуберт Гуркач", "prob": 0.72, "fav": "Андрей Рублёв", "tournament": "Gerry Weber Open (ATP 250 Halle)", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 3, "date": "2026-06-15", "p1": "А. Зверев", "p2": "В. Копршива", "prob": 0.92, "fav": "А. Зверев", "tournament": "Halle Open (Terra Wortmann Open) 2026", "confidence": "ВЫСОКАЯ", "sport": "tennis"},
    {"id": 4, "date": "2026-06-15", "p1": "?", "p2": "?", "prob": 0.5, "fav": "?", "tournament": "?", "confidence": "?", "sport": "tennis"},
    {"id": 5, "date": "2026-06-15", "p1": "Томми Пол", "p2": "Захари Свайда", "prob": 0.62, "fav": "Томми Пол", "tournament": "Queen's Club Championships 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 6, "date": "2026-06-15", "p1": "?", "p2": "?", "prob": 0.5, "fav": "?", "tournament": "?", "confidence": "?", "sport": "tennis"},
    {"id": 7, "date": "2026-06-15", "p1": "Томми Пол", "p2": "Закари Свайда", "prob": 0.65, "fav": "Томми Пол", "tournament": "HSBC Championships 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 8, "date": "2026-06-15", "p1": "Ф. Коболли", "p2": "Ф. Тиафо", "prob": 0.52, "fav": "Ф. Тиафо", "tournament": "Halle", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 9, "date": "2026-06-15", "p1": "BetBoom", "p2": "FUT", "prob": 0.58, "fav": "FUT", "tournament": "IEM Cologne Major 2026", "confidence": "УМЕРЕННАЯ", "sport": "cs2"},
    {"id": 10, "date": "2026-06-15", "p1": "Natus Vincere", "p2": "G2 Esports", "prob": 0.64, "fav": "Natus Vincere", "tournament": "IEM Cologne Major 2026", "confidence": "УМЕРЕННАЯ", "sport": "cs2"},
    {"id": 11, "date": "2026-06-15", "p1": "Мпеши Перрикар", "p2": "Корентен Муте", "prob": 0.63, "fav": "Мпеши Перрикар", "tournament": "Queen's", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 12, "date": "2026-06-16", "p1": "Алекс де Миноур", "p2": "Габриэль Диалло", "prob": 0.61, "fav": "Алекс де Миноур", "tournament": "Queen's Club Championship 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 13, "date": "2026-06-16", "p1": "Ринки Хиджиката", "p2": "Алехандро Табило", "prob": 0.62, "fav": "Алехандро Табило", "tournament": "Queen's Club Championships 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 14, "date": "2026-06-16", "p1": "Ignacio Buse", "p2": "Marcos Giron", "prob": 0.54, "fav": "Ignacio Buse", "tournament": "Queen's Club Championships 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 15, "date": "2026-06-16", "p1": "Александр Зверев", "p2": "Вит Коприва", "prob": 0.88, "fav": "Александр Зверев", "tournament": "Terra Wortmann Open (Halle 2026)", "confidence": "ОЧЕНЬ ВЫСОКАЯ", "sport": "tennis"},
    {"id": 16, "date": "2026-06-16", "p1": "Маттиа Белуччи", "p2": "Александр Бублик", "prob": 0.73, "fav": "Александр Бублик", "tournament": "Terra Wortmann Open 2026", "confidence": "ВЫСОКАЯ", "sport": "tennis"},
    {"id": 17, "date": "2026-06-16", "p1": "Алексей Попырин", "p2": "Рафаэль Коллиньон", "prob": 0.58, "fav": "Рафаэль Коллиньон", "tournament": "Halle", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 18, "date": "2026-06-16", "p1": "Карен Хачанов", "p2": "Итан Куинн", "prob": 0.66, "fav": "Карен Хачанов", "tournament": "Halle 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 19, "date": "2026-06-16", "p1": "Дениил Медведев", "p2": "Томас Мартин Этчеверри", "prob": 0.75, "fav": "Дениил Медведев", "tournament": "Halle 2026", "confidence": "ВЫСОКАЯ", "sport": "tennis"},
    {"id": 20, "date": "2026-06-16", "p1": "Андрей Рублёв", "p2": "Хуберт Хуркач", "prob": 0.65, "fav": "Андрей Рублёв", "tournament": "Halle Open 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 21, "date": "2026-06-16", "p1": "А. Ковачевич", "p2": "Ф. Черундоло", "prob": 0.62, "fav": "Ф. Черундоло", "tournament": "не подтверждён", "confidence": "ОЧЕНЬ НИЗКАЯ", "sport": "tennis"},
    {"id": 22, "date": "2026-06-16", "p1": "Франсиско Серундоло", "p2": "Александар Ковачевич", "prob": 0.61, "fav": "Франсиско Серундоло", "tournament": "Queen's Club Championships 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 23, "date": "2026-06-16", "p1": "Алехандро Давидович Фокина", "p2": "Кэмерон Норри", "prob": 0.53, "fav": "Алехандро Давидович Фокина", "tournament": "Queen's Club Championships 2026", "confidence": "НИЗКАЯ", "sport": "tennis"},
    {"id": 24, "date": "2026-06-16", "p1": "Йиржи Лехецка", "p2": "Камиль Мальчжак", "prob": 0.55, "fav": "Йиржи Лехецка", "tournament": "HSBC Championships 2026", "confidence": "НИЗКАЯ", "sport": "tennis"},
    {"id": 25, "date": "2026-06-17", "p1": "?", "p2": "?", "prob": 0.5, "fav": "?", "tournament": "?", "confidence": "?", "sport": "tennis"},
    {"id": 26, "date": "2026-06-17", "p1": "?", "p2": "?", "prob": 0.5, "fav": "?", "tournament": "?", "confidence": "?", "sport": "tennis"},
    {"id": 27, "date": "2026-06-17", "p1": "Бен Шелтон", "p2": "Лоренцо Сонего", "prob": 0.72, "fav": "Бен Шелтон", "tournament": "Halle 2026", "confidence": "ВЫСОКАЯ", "sport": "tennis"},
    {"id": 28, "date": "2026-06-17", "p1": "Алекс де Минаур", "p2": "Денис Шаповалов", "prob": 0.78, "fav": "Алекс де Минаур", "tournament": "Queen's Club Championships 2026", "confidence": "ВЫСОКАЯ", "sport": "tennis"},
    {"id": 29, "date": "2026-06-17", "p1": "?", "p2": "?", "prob": 0.5, "fav": "?", "tournament": "?", "confidence": "?", "sport": "tennis"},
    {"id": 30, "date": "2026-06-17", "p1": "Дэниил Медведев", "p2": "Теренс Атман", "prob": 0.82, "fav": "Дэниил Медведев", "tournament": "Terra Wortmann Open (ATP Halle 2026)", "confidence": "ОЧЕНЬ ВЫСОКАЯ", "sport": "tennis"},
    {"id": 31, "date": "2026-06-17", "p1": "Hubert Hurkacz", "p2": "Daniel Altmaier", "prob": 0.62, "fav": "Hubert Hurkacz", "tournament": "Terra Wortmann Open (ATP Halle) 2026", "confidence": "СРЕДНЯЯ", "sport": "tennis"},
    {"id": 32, "date": "2026-06-17", "p1": "Дэниил Медведев", "p2": "Терен Атман", "prob": 0.78, "fav": "Дэниил Медведев", "tournament": "ATP Terra Wortmann Open Halle 2026", "confidence": "ВЫСОКАЯ", "sport": "tennis"},
]


def main():
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        print("❌ DATABASE_URL не установлен. Прицепи Postgres в Railway.")
        return

    skipped_invalid = 0
    inserted = 0
    deduplicated = 0

    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    for src in SOURCE_DATA:
        # 1. ВАЛИДАЦИЯ
        if not is_valid_prediction(src["p1"], src["p2"], src["fav"], src["prob"]):
            print(f"⏭  Пропущен мусор id={src['id']}: {src['p1']} vs {src['p2']}")
            skipped_invalid += 1
            continue

        # 2. НОРМАЛИЗАЦИЯ
        p1_norm = normalize_player_name(src["p1"], src["sport"])
        p2_norm = normalize_player_name(src["p2"], src["sport"])
        fav_norm = normalize_player_name(src["fav"], src["sport"])

        # 3. ДЕДУПЛИКАЦИЯ
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM predictions
                WHERE date = %s
                  AND ((p1 = %s AND p2 = %s) OR (p1 = %s AND p2 = %s))
                LIMIT 1
            """, (src["date"], p1_norm, p2_norm, p2_norm, p1_norm))
            existing = cur.fetchone()

            if existing:
                print(f"♻️  Дубль id={src['id']}: {p1_norm} vs {p2_norm} — пропущен")
                deduplicated += 1
                continue

            # 4. ВСТАВКА
            cur.execute("""
                INSERT INTO predictions
                    (date, p1, p2, prob, fav, tournament, confidence, sport, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id
            """, (src["date"], p1_norm, p2_norm, src["prob"], fav_norm,
                  src["tournament"], src["confidence"], src["sport"]))
            new_id = cur.fetchone()["id"]
            print(f"✅ id={src['id']} → новая id={new_id}: {p1_norm} vs {p2_norm}")
            inserted += 1

    conn.commit()
    conn.close()

    print()
    print("=" * 60)
    print(f"ИТОГО:")
    print(f"  Исходных записей:    {len(SOURCE_DATA)}")
    print(f"  Пропущено (мусор):   {skipped_invalid}")
    print(f"  Пропущено (дубли):   {deduplicated}")
    print(f"  Вставлено в Postgres: {inserted}")
    print("=" * 60)


if __name__ == "__main__":
    main()
