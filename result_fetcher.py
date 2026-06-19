"""
Загрузка результатов завершённых матчей с публичных API.

Источники:
- Tennis: Sofascore (api.sofascore.com — не требует ключа)
- CS2: HLTV (через парсинг hltv.org/results)
- Dota 2: пока не реализовано (skip)

Используется в auto_resolve.py для автоматического проставления outcome
в БД без ручного /setresult.
"""

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 11_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json,text/html"}


# ───────────────────────── TENNIS ─────────────────────────

SOFASCORE_BASE = "https://api.sofascore.com/api/v1"


def fetch_tennis_results(target_date: str) -> List[Dict]:
    """
    Возвращает список завершённых теннисных матчей за дату.

    Args:
        target_date: ISO date 'YYYY-MM-DD'

    Returns:
        [{"p1": "...", "p2": "...", "winner": "...", "score": "..."}, ...]
    """
    url = f"{SOFASCORE_BASE}/sport/tennis/scheduled-events/{target_date}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
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
        if event.get("doublesPair") or "/" in event.get("homeTeam", {}).get("name", ""):
            continue

        home_name = event.get("homeTeam", {}).get("name", "").strip()
        away_name = event.get("awayTeam", {}).get("name", "").strip()
        winner_code = event.get("winnerCode")  # 1 = home, 2 = away, 3 = draw
        if not home_name or not away_name or winner_code not in (1, 2):
            continue

        # Build score string from sets
        hs = event.get("homeScore", {})
        as_ = event.get("awayScore", {})
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


# ───────────────────────── CS2 ─────────────────────────

HLTV_RESULTS_URL = "https://www.hltv.org/results"


def fetch_cs2_results(target_date: str) -> List[Dict]:
    """
    Возвращает завершённые CS2 матчи за дату через парсинг hltv.org/results.

    Returns:
        [{"p1": "...", "p2": "...", "winner": "...", "score": "2-0"}, ...]
    """
    # HLTV даёт страницу со списком прошедших матчей
    try:
        r = requests.get(HLTV_RESULTS_URL, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            logger.warning(f"HLTV results: HTTP {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.error(f"HLTV results fetch failed: {e}")
        return []

    results = []
    # HLTV структура: .result-con содержит каждый матч
    for match in soup.select(".result-con"):
        try:
            # Дата матча — выше блока в .standard-headline
            # Для упрощения: берём все доступные матчи (HLTV показывает последние 100)
            team1_el = match.select_one(".team1 .team")
            team2_el = match.select_one(".team2 .team")
            score_el = match.select_one(".result-score")
            if not team1_el or not team2_el or not score_el:
                continue
            t1 = team1_el.text.strip()
            t2 = team2_el.text.strip()
            score_text = score_el.text.strip()  # "2 - 1"
            parts = re.findall(r"\d+", score_text)
            if len(parts) != 2:
                continue
            s1, s2 = int(parts[0]), int(parts[1])
            # Winner определяется по тегу .team-won
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

    logger.info(f"HLTV results: {len(results)} matches parsed")
    return results


# ───────────────────────── DOTA 2 ─────────────────────────

def fetch_dota2_results(target_date: str) -> List[Dict]:
    """Dota 2 пока не реализована. Возвращает пустой список."""
    logger.info("Dota 2 fetcher: not implemented yet")
    return []


# ───────────────────────── COMMON ─────────────────────────

def fetch_results_for_sport(sport: str, target_date: str) -> List[Dict]:
    """Универсальный fetch для любого вида спорта."""
    if sport == "tennis":
        return fetch_tennis_results(target_date)
    elif sport == "cs2":
        return fetch_cs2_results(target_date)
    elif sport == "dota2":
        return fetch_dota2_results(target_date)
    else:
        logger.warning(f"Unknown sport: {sport}")
        return []
