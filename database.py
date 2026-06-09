"""SQLite database for persistent storage — predictions, VIP, follows, usage."""

import sqlite3
import json
import os
from datetime import date, timedelta

DB_PATH = os.getenv("DB_PATH", "/tmp/tennis-bot.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            p1 TEXT, p2 TEXT, prob REAL, fav TEXT,
            tournament TEXT, confidence TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS vip_users (
            user_id INTEGER PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS follows (
            user_id INTEGER,
            player TEXT,
            PRIMARY KEY (user_id, player)
        );
        CREATE TABLE IF NOT EXISTS usage (
            user_id INTEGER,
            date TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        );
    """)
    conn.commit()
    conn.close()


# ── Predictions ──

def save_prediction(p1, p2, prob, fav, tournament, confidence):
    conn = _conn()
    conn.execute(
        "INSERT INTO predictions (date, p1, p2, prob, fav, tournament, confidence) VALUES (?,?,?,?,?,?,?)",
        (date.today().isoformat(), p1, p2, prob, fav, tournament, confidence)
    )
    conn.commit()
    conn.close()


def get_predictions(target_date=None):
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).isoformat()
    conn = _conn()
    rows = conn.execute("SELECT * FROM predictions WHERE date=?", (target_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── VIP ──

def add_vip(user_id):
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO vip_users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def remove_vip(user_id):
    conn = _conn()
    conn.execute("DELETE FROM vip_users WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def is_vip(user_id):
    conn = _conn()
    row = conn.execute("SELECT 1 FROM vip_users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def get_all_vips():
    conn = _conn()
    rows = conn.execute("SELECT user_id FROM vip_users").fetchall()
    conn.close()
    return {r["user_id"] for r in rows}


# ── Follows ──

def add_follow(user_id, player):
    conn = _conn()
    conn.execute("INSERT OR REPLACE INTO follows (user_id, player) VALUES (?,?)", (user_id, player))
    conn.commit()
    conn.close()


def remove_follow(user_id, player):
    conn = _conn()
    conn.execute("DELETE FROM follows WHERE user_id=? AND player=?", (user_id, player))
    conn.commit()
    conn.close()


def get_follows(user_id):
    conn = _conn()
    rows = conn.execute("SELECT player FROM follows WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    return {r["player"] for r in rows}


# ── Usage ──

def get_usage(user_id):
    today = date.today().isoformat()
    conn = _conn()
    row = conn.execute("SELECT count FROM usage WHERE user_id=? AND date=?", (user_id, today)).fetchone()
    conn.close()
    return row["count"] if row else 0


def increment_usage(user_id):
    today = date.today().isoformat()
    conn = _conn()
    conn.execute("""
        INSERT INTO usage (user_id, date, count) VALUES (?, ?, 1)
        ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1
    """, (user_id, today))
    conn.commit()
    conn.close()


# ── Export & Stats ──

def get_all_predictions():
    conn = _conn()
    rows = conn.execute("SELECT * FROM predictions ORDER BY date DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_prediction_stats():
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) as c FROM predictions").fetchone()["c"]
    by_date = conn.execute(
        "SELECT date, COUNT(*) as c FROM predictions GROUP BY date ORDER BY date DESC LIMIT 14"
    ).fetchall()
    conn.close()
    return {"total": total, "by_date": [dict(r) for r in by_date]}


def export_csv():
    """Export all predictions as CSV string."""
    rows = get_all_predictions()
    if not rows:
        return ""
    lines = ["date,p1,p2,probability,favorite,tournament,confidence"]
    for r in rows:
        lines.append(f"{r['date']},{r['p1']},{r['p2']},{r['prob']},{r['fav']},{r['tournament']},{r['confidence']}")
    return "\n".join(lines)


# ── Language ──

def set_language(user_id, lang):
    conn = _conn()
    conn.execute("CREATE TABLE IF NOT EXISTS user_lang (user_id INTEGER PRIMARY KEY, lang TEXT)")
    conn.execute("INSERT OR REPLACE INTO user_lang (user_id, lang) VALUES (?,?)", (user_id, lang))
    conn.commit()
    conn.close()


def get_language(user_id):
    conn = _conn()
    conn.execute("CREATE TABLE IF NOT EXISTS user_lang (user_id INTEGER PRIMARY KEY, lang TEXT)")
    row = conn.execute("SELECT lang FROM user_lang WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["lang"] if row else "ru"


def export_full_json():
    """Export all data as JSON string for Cowork analysis."""
    import json as _json
    conn = _conn()
    preds = [dict(r) for r in conn.execute("SELECT * FROM predictions ORDER BY date DESC").fetchall()]
    vips = [dict(r) for r in conn.execute("SELECT * FROM vip_users").fetchall()]
    follows = [dict(r) for r in conn.execute("SELECT * FROM follows").fetchall()]
    usage = [dict(r) for r in conn.execute("SELECT * FROM usage ORDER BY date DESC LIMIT 30").fetchall()]
    conn.close()

    stats = get_prediction_stats()
    by_date = {}
    for p in preds:
        d = p.get("date", "?")
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(p)

    return _json.dumps({
        "export_date": date.today().isoformat(),
        "total_predictions": stats["total"],
        "predictions_by_date": stats["by_date"],
        "all_predictions": preds,
        "vip_users": vips,
        "follows": follows,
        "recent_usage": usage,
    }, ensure_ascii=False, indent=2)


# Initialize on import
init_db()
