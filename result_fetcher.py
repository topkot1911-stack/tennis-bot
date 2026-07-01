"""
Загрузка результатов завершённых матчей с публичных источников.

Sofascore и HLTV защищены Cloudflare и блокируют простой requests.
Используем cloudscraper — он притворяется реальным браузером и обходит защиту.

Источники:
- Tennis: Sofascore (api.sofascore.com)
- CS2:   HLTV (hltv.org/results)
- Dota 2: skip (не реализовано)
"""

import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# cloudscraper handles Cloudflare 403 protection automatically.
# Fallback to plain requests if cloudscraper не установлен.
try:
    import cloudscraper
    _scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "mobile": False}
    )
    _USE_CLOUDSCRAPER = True
    logger.info("✅ cloudscraper загружен — Cloudflare bypass активен")
except ImportError:
    import requests as _scraper_fallback
    _scraper = _scraper_fallback
    _USE_CLOUDSCRAPER = False
    logger.warning("⚠️  cloudscraper не установлен — fallback на requests (403 likely)")

from bs4 import BeautifulSoup

# Реалистичные headers — даже cloudscraper выигрывает с правильными referrer/origin
TENNIS_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

HLTV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.hltv.org/",
}


# ───────────────────────── TENNIS ─────────────────────────

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"


def fetch_tennis_results(target_date: str) -> List[Dict]:
    """
    Возвращает завершённые теннисные матчи за дату через Sofascore.

    Args:
        target_date: ISO date 'YYYY-MM-DD'

    Returns:
        [{"p1": "...", "p2": "...", "winner": "...", "score": "..."}, ...]
    """
    url = f"{SOFASCORE_BASE}/sport/tennis/scheduled-events/{target_date}"
    try:
        r = _scraper.get(url, headers=TENNIS_HEADERS, timeout=20)
        if r.status_code != 200:
            logger.warning(f"Sofascore tennis {target_date}: HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        logger.error(f"Sofascore tennis fetch failed for {target_date}: {e}")
        return []

    results = []
    for event in data.get("events", []):
        status = event.get("status", {})
        if status.get("type") != "finished":
            continue
        # Skip doubles
        home_team = event.get("homeTeam", {})
        away_team = event.get("awayTeam", {})
        home_name = (home_team.get("name") or "").strip()
        away_name = (away_team.get("name") or "").strip()
        if "/" in home_name or "/" in away_name:
            continue
        # Skip WTA (женский теннис) — пользователь не анализирует
        category_slug = (event.get("tournament", {})
                              .get("category", {})
                              .get("slug", "")).lower()
        if "wta" in category_slug or "women" in category_slug:
            continue

        winner_code = event.get("winnerCode")
        if not home_name or not away_name or winner_code not in (1, 2):
            continue

        hs = event.get("homeScore", {}) or {}
        as_ = event.get("awayScore", {}) or {}
        sets = []
        for i in range(1, 6):
            key = f"period{i}"
            if hs.get(key) is not None and as_.get(key) is not None:
                sets.append(f"{hs[key]}-{as_[key]}")
        score = " ".join(sets) if sets else "?"

        results.append({
            "p1": home_name,
            "p2": away_name,
            "winner": home_name if winner_code == 1 else away_name,
            "score": score,
            "tournament": event.get("tournament", {}).get("name", "?"),
        })

    logger.info(f"Sofascore tennis {target_date}: {len(results)} finished matches")
    return results


def fetch_tennis_schedule(target_date: str) -> List[Dict]:
    """
    Возвращает ЗАПЛАНИРОВАННЫЕ и НЕ ЗАВЕРШЁННЫЕ теннисные матчи за дату
    (ATP main tour, без WTA, без парного разряда).

    Каждый матч содержит рейтинги игроков (если доступны).

    Returns:
        [{"p1": "...", "p2": "...", "p1_rank": int, "p2_rank": int,
          "tournament": "...", "start_time": "..."}, ...]
    """
    url = f"{SOFASCORE_BASE}/sport/tennis/scheduled-events/{target_date}"
    try:
        r = _scraper.get(url, headers=TENNIS_HEADERS, timeout=20)
        if r.status_code != 200:
            logger.warning(f"Sofascore schedule {target_date}: HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        logger.error(f"Sofascore schedule fetch failed for {target_date}: {e}")
        return []

    matches = []
    for event in data.get("events", []):
        status = event.get("status", {})
        status_type = status.get("type", "")
        # Только не завершённые (не finished, canceled и т.п.)
        if status_type == "finished":
            continue

        home_team = event.get("homeTeam", {}) or {}
        away_team = event.get("awayTeam", {}) or {}
        home_name = (home_team.get("name") or "").strip()
        away_name = (away_team.get("name") or "").strip()

        # Skip doubles
        if not home_name or not away_name or "/" in home_name or "/" in away_name:
            continue

        # Skip WTA
        category_slug = (event.get("tournament", {})
                              .get("category", {})
                              .get("slug", "")).lower()
        if "wta" in category_slug or "women" in category_slug:
            continue
        # Skip ITF/challenger — оставляем только ATP main
        tournament_slug = (event.get("tournament", {}).get("slug", "") or "").lower()
        tournament_name = event.get("tournament", {}).get("name", "?")
        if any(k in tournament_slug for k in ("itf", "challenger")):
            continue

        # Рейтинги из ranking (если есть)
        p1_rank = home_team.get("ranking") or home_team.get("rank") or 0
        p2_rank = away_team.get("ranking") or away_team.get("rank") or 0

        start_ts = event.get("startTimestamp", 0)
        start_time = ""
        if start_ts:
            from datetime import datetime as _dt
            start_time = _dt.fromtimestamp(start_ts).strftime("%H:%M")

        matches.append({
            "p1": home_name,
            "p2": away_name,
            "p1_rank": int(p1_rank) if p1_rank else 0,
            "p2_rank": int(p2_rank) if p2_rank else 0,
            "tournament": tournament_name,
            "start_time": start_time,
            "start_ts": start_ts,
        })

    logger.info(f"Sofascore schedule {target_date}: {len(matches)} scheduled ATP matches")
    return matches


def quick_tennis_probability(rank_fav: int, rank_dog: int,
                              surface: str = "grass") -> float:
    """
    Быстрая математическая оценка вероятности победы фаворита.
    Основано на ATP рейтинге + поверхность (без ML).

    Формула:
      p = 0.5 + 0.15 × ln(rank_dog / rank_fav)
      корректировка ±3% по покрытию

    Кэп 55-92%.
    """
    import math
    if not rank_fav or not rank_dog or rank_fav <= 0 or rank_dog <= 0:
        return 0.55  # неизвестно — минимальная уверенность
    if rank_fav >= rank_dog:
        # рейтинги в обратном порядке — swapped
        rank_fav, rank_dog = rank_dog, rank_fav

    log_ratio = math.log(rank_dog / rank_fav)
    p = 0.5 + 0.15 * log_ratio

    # Grass слегка усиливает фаворитов у которых big-serv
    # (для простоты — небольшой бонус на grass)
    if surface == "grass":
        p += 0.01

    return round(max(0.5, min(0.92, p)), 3)


# ───────────────────────── CS2 ─────────────────────────

HLTV_RESULTS_URL = "https://www.hltv.org/results"


def fetch_cs2_results(target_date: str) -> List[Dict]:
    """
    Завершённые CS2 матчи через парсинг hltv.org/results.

    NOTE: HLTV показывает страницу со «свежими» результатами (сегодня + вчера).
    Для старых дат — нужен offset параметр ?offset=N.
    """
    # Вычисляем offset — сколько дней назад
    try:
        target_dt = date.fromisoformat(target_date)
    except ValueError:
        return []
    days_ago = (date.today() - target_dt).days
    # Каждая страница ~100 матчей, обычно за день 50-100 матчей в мировом CS
    # offset = days_ago * 100 — приближение
    offset = max(0, days_ago * 100)

    url = HLTV_RESULTS_URL if offset == 0 else f"{HLTV_RESULTS_URL}?offset={offset}"

    try:
        r = _scraper.get(url, headers=HLTV_HEADERS, timeout=20)
        if r.status_code != 200:
            logger.warning(f"HLTV results: HTTP {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.error(f"HLTV results fetch failed: {e}")
        return []

    results = []
    for match in soup.select(".result-con"):
        try:
            team1_el = match.select_one(".team1 .team")
            team2_el = match.select_one(".team2 .team")
            score_el = match.select_one(".result-score")
            if not team1_el or not team2_el or not score_el:
                continue
            t1 = team1_el.text.strip()
            t2 = team2_el.text.strip()
            score_text = score_el.text.strip()
            parts = re.findall(r"\d+", score_text)
            if len(parts) != 2:
                continue
            s1, s2 = int(parts[0]), int(parts[1])
            t1_won_class = "team-won" in (team1_el.get("class") or [])
            t2_won_class = "team-won" in (team2_el.get("class") or [])
            if t1_won_class:
                winner = t1
            elif t2_won_class:
                winner = t2
            elif s1 > s2:
                winner = t1
            elif s2 > s1:
                winner = t2
            else:
                continue
            results.append({
                "p1": t1, "p2": t2, "winner": winner,
                "score": f"{s1}-{s2}",
                "tournament": "?",
            })
        except Exception as e:
            logger.debug(f"HLTV match parse error: {e}")
            continue

    logger.info(f"HLTV results offset={offset}: {len(results)} matches")
    return results


# ───────────────────────── DOTA 2 ─────────────────────────

def fetch_dota2_results(target_date: str) -> List[Dict]:
    logger.info("Dota 2 fetcher: not implemented yet")
    return []


# ───────────────────────── COMMON ─────────────────────────

def fetch_results_for_sport(sport: str, target_date: str) -> List[Dict]:
    if sport == "tennis":
        return fetch_tennis_results(target_date)
    elif sport == "cs2":
        return fetch_cs2_results(target_date)
    elif sport == "dota2":
        return fetch_dota2_results(target_date)
    else:
        logger.warning(f"Unknown sport: {sport}")
        return []
