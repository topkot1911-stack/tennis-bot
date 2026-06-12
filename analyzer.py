"""Tennis match analyzer using Claude API + mathematical model."""

import json
import math
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, SYSTEM_PROMPT, CS2_SYSTEM_PROMPT, DOTA2_SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════
# MATHEMATICAL MODEL (Bo3 / Bo5)
# ═══════════════════════════════════════════════════════════

def bo5_distribution(p_win: float) -> dict:
    """Calculate Bo5 set distribution from match win probability."""
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        val = mid**3 * (10 - 15 * mid + 6 * mid**2)
        if val < p_win:
            lo = mid
        else:
            hi = mid
    S = (lo + hi) / 2

    dist = {
        "S": round(S, 4),
        "3-0": round(S**3, 4),
        "3-1": round(3 * S**3 * (1 - S), 4),
        "3-2": round(6 * S**3 * (1 - S)**2, 4),
        "0-3": round((1 - S)**3, 4),
        "1-3": round(3 * (1 - S)**3 * S, 4),
        "2-3": round(6 * (1 - S)**3 * S**2, 4),
    }

    # Expected totals
    if p_win >= 0.82:
        g3, g4, g5, fs = 28, 35.5, 42, 0.575
    elif p_win >= 0.72:
        g3, g4, g5, fs = 29.5, 36.5, 43, 0.56
    elif p_win >= 0.62:
        g3, g4, g5, fs = 30.5, 37.5, 44.5, 0.545
    else:
        g3, g4, g5, fs = 31.5, 38.5, 45.5, 0.53

    e_tot = (dist["3-0"] + dist["0-3"]) * g3 + \
            (dist["3-1"] + dist["1-3"]) * g4 + \
            (dist["3-2"] + dist["2-3"]) * g5

    dist["e_total"] = round(e_tot, 1)
    dist["e_fav"] = round(e_tot * fs, 1)
    dist["e_dog"] = round(e_tot * (1 - fs), 1)
    dist["handicap"] = round(e_tot * fs - e_tot * (1 - fs), 1)

    # Duration
    dist["e_duration"] = round(
        (dist["3-0"] + dist["0-3"]) * 105 +
        (dist["3-1"] + dist["1-3"]) * 150 +
        (dist["3-2"] + dist["2-3"]) * 195
    )

    # Tiebreak probability
    ptb = 1 - (1 - 0.15) ** (
        (dist["3-1"] + dist["1-3"]) * 4 +
        (dist["3-2"] + dist["2-3"]) * 5 +
        (dist["3-0"] + dist["0-3"]) * 3
    )
    dist["p_tiebreak"] = round(min(ptb, 0.75), 2)

    return dist


def bo3_distribution(p_win: float) -> dict:
    """Calculate Bo3 set distribution from match win probability."""
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        val = mid**2 * (3 - 2 * mid)
        if val < p_win:
            lo = mid
        else:
            hi = mid
    S = (lo + hi) / 2

    dist = {
        "S": round(S, 4),
        "2-0": round(S**2, 4),
        "2-1": round(2 * S**2 * (1 - S), 4),
        "0-2": round((1 - S)**2, 4),
        "1-2": round(2 * (1 - S)**2 * S, 4),
    }

    # Expected totals for WTA
    if p_win >= 0.75:
        g2, g3, fs = 17, 26, 0.57
    elif p_win >= 0.65:
        g2, g3, fs = 18, 27, 0.55
    elif p_win >= 0.55:
        g2, g3, fs = 19, 28, 0.53
    else:
        g2, g3, fs = 19.5, 28.5, 0.52

    e_tot = (dist["2-0"] + dist["0-2"]) * g2 + \
            (dist["2-1"] + dist["1-2"]) * g3

    dist["e_total"] = round(e_tot, 1)
    dist["e_fav"] = round(e_tot * fs, 1)
    dist["e_dog"] = round(e_tot * (1 - fs), 1)
    dist["handicap"] = round(e_tot * fs - e_tot * (1 - fs), 1)

    dist["e_duration"] = round(
        (dist["2-0"] + dist["0-2"]) * 65 +
        (dist["2-1"] + dist["1-2"]) * 95
    )

    ptb = 1 - (1 - 0.18) ** (
        (dist["2-1"] + dist["1-2"]) * 3 +
        (dist["2-0"] + dist["0-2"]) * 2
    )
    dist["p_tiebreak"] = round(min(ptb, 0.70), 2)

    return dist


# ═══════════════════════════════════════════════════════════
# CLAUDE API ANALYSIS
# ═══════════════════════════════════════════════════════════

async def analyze_match(query: str, lang_suffix: str = "") -> dict:
    """
    Send match query to Claude API with web search enabled.
    Returns parsed JSON dict with all analysis data.
    """
    from datetime import date
    today = date.today().strftime("%d %B %Y")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_prompt = (
        f"Сегодня {today}. Проанализируй ПРЕДСТОЯЩИЙ теннисный матч: {query}\n\n"
        "ОБЯЗАТЕЛЬНО используй веб-поиск чтобы найти АКТУАЛЬНЫЕ данные на сегодня: "
        "текущие рейтинги 2026 года, результаты на текущем турнире, форму, H2H, "
        "путь на турнире, травмы, погоду. "
        "НЕ используй устаревшие данные из памяти — ТОЛЬКО свежие из поиска. "
        "Рассчитай вероятность по факторной модели. "
        "Верни результат СТРОГО в формате JSON как описано в системном промпте."
        + lang_suffix
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Extract JSON — with web search, Claude returns multiple content blocks
    # Collect ALL text from all text blocks
    all_text = ""
    for block in response.content:
        if hasattr(block, "text") and block.text:
            all_text += "\n" + block.text

    if not all_text.strip():
        raise ValueError("Claude returned no text response")

    text = all_text.strip()

    # Strategy 1: find ```json ... ``` block
    if "```json" in text:
        json_part = text.split("```json")[1].split("```")[0].strip()
        try:
            data = json.loads(json_part)
        except json.JSONDecodeError:
            pass
        else:
            # Success with markdown JSON block
            p = data.get("probability", 0.5)
            bo = data.get("bo", 5)
            if bo == 5:
                data["distribution"] = bo5_distribution(p)
            else:
                data["distribution"] = bo3_distribution(p)
            return data

    # Strategy 2: find the largest {...} block in text
    data = None
    best_len = 0
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            start = i
            for j in range(i, len(text)):
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:j+1]
                        if len(candidate) > best_len:
                            try:
                                parsed = json.loads(candidate)
                                if isinstance(parsed, dict) and ("player1" in parsed or "probability" in parsed):
                                    data = parsed
                                    best_len = len(candidate)
                            except json.JSONDecodeError:
                                pass
                        break
        i += 1

    if data is None:
        # Fallback: return raw text as a simple dict
        return {"_raw_text": text, "probability": 0.5, "bo": 5,
                "player1": {"name": "?", "name_en": "Player1"}, "player2": {"name": "?", "name_en": "Player2"},
                "favorite": 1, "distribution": bo5_distribution(0.5)}

    # Enrich with mathematical model
    p = data.get("probability", 0.5)
    bo = data.get("bo", 5)

    if bo == 5:
        data["distribution"] = bo5_distribution(p)
    else:
        data["distribution"] = bo3_distribution(p)

    return data


def format_summary(data: dict) -> str:
    """Format analysis data into a Telegram message (with HTML formatting)."""
    # Fallback: if JSON parsing failed, return raw Claude text
    if "_raw_text" in data:
        raw = data["_raw_text"]
        if len(raw) > 3900:
            raw = raw[:3897] + "..."
        return f"🎾 <b>Анализ матча</b>\n\n{raw}\n\n<i>⚠️ Исследовательский анализ</i>"

    p = data.get("probability", 0.5)
    bo = data.get("bo", 5)
    dist = data.get("distribution", {})
    fav_idx = data.get("favorite", 1)

    p1 = data.get("player1", {})
    p2 = data.get("player2", {})

    fav = p1 if fav_idx == 1 else p2
    dog = p2 if fav_idx == 1 else p1

    fav_pct = round(p * 100)
    dog_pct = 100 - fav_pct

    # Build Telegram HTML message
    lines = []
    lines.append(f"<b>🎾 {data.get('tournament', 'Tennis')} | {data.get('round', '')}</b>")
    lines.append(f"📍 {data.get('court', '')} | {data.get('date', '')}")
    lines.append(f"🌡 {data.get('weather', '')}")
    lines.append("")

    seed1 = f" [{p1.get('seed', '')}]" if p1.get("seed") else ""
    seed2 = f" [{p2.get('seed', '')}]" if p2.get("seed") else ""
    lines.append(f"<b>{p1['name']}{seed1}</b>  vs  <b>{p2['name']}{seed2}</b>")
    lines.append(f"{p1.get('nationality','')} ATP {p1.get('rank','')} | "
                 f"{p2.get('nationality','')} ATP {p2.get('rank','')}")
    lines.append("")

    # Probability bar (text-based)
    bar_len = 20
    fav_blocks = round(bar_len * p)
    dog_blocks = bar_len - fav_blocks
    bar = "🟩" * fav_blocks + "🟥" * dog_blocks
    lines.append(f"{bar}")
    lines.append(f"<b>{fav['name']} {fav_pct}%</b> — {dog['name']} {dog_pct}%")
    lines.append("")

    # Key stats
    lines.append(f"📊 <b>Ключевые показатели:</b>")
    lines.append(f"  E(total): {dist.get('e_total', '?')} геймов")
    lines.append(f"  Фора: -{dist.get('handicap', '?')}")
    lines.append(f"  Тай-брейк: {round(dist.get('p_tiebreak', 0) * 100)}%")
    lines.append(f"  Длительность: ~{dist.get('e_duration', '?')} мин")
    lines.append("")

    # Set distribution
    lines.append(f"📈 <b>Распределение по сетам:</b>")
    if bo == 5:
        lines.append(f"  {fav['name']}: 3-0 {round(dist.get('3-0',0)*100)}% | "
                     f"3-1 {round(dist.get('3-1',0)*100)}% | "
                     f"3-2 {round(dist.get('3-2',0)*100)}%")
        lines.append(f"  {dog['name']}: 0-3 {round(dist.get('0-3',0)*100)}% | "
                     f"1-3 {round(dist.get('1-3',0)*100)}% | "
                     f"2-3 {round(dist.get('2-3',0)*100)}%")
    else:
        lines.append(f"  {fav['name']}: 2-0 {round(dist.get('2-0',0)*100)}% | "
                     f"2-1 {round(dist.get('2-1',0)*100)}%")
        lines.append(f"  {dog['name']}: 0-2 {round(dist.get('0-2',0)*100)}% | "
                     f"1-2 {round(dist.get('1-2',0)*100)}%")
    lines.append("")

    # Factors (compact)
    factors = data.get("factors", [])
    if factors:
        lines.append(f"⚖️ <b>Факторы:</b>")
        for f in factors[:6]:  # Show top 6
            lines.append(f"  {f.get('num','')}. {f.get('name','')}: {f.get('shift','')}")
        lines.append("")

    # Verdict
    verdict = data.get("verdict", "")
    if verdict:
        lines.append(f"🏆 <b>Вердикт:</b>")
        # Trim to ~300 chars for Telegram
        if len(verdict) > 350:
            verdict = verdict[:347] + "..."
        lines.append(verdict)
        lines.append("")

    confidence = data.get("confidence", "")
    if confidence:
        lines.append(f"📌 Уверенность: <b>{confidence}</b>")

    lines.append("")
    lines.append("<i>⚠️ Исследовательский анализ, не рекомендация по ставкам</i>")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# CS2 ANALYSIS
# ═══════════════════════════════════════════════════════════

async def analyze_cs2(query: str, lang_suffix: str = "") -> dict:
    """Analyze a CS2 match using Claude API with web search."""
    from datetime import date
    today = date.today().strftime("%d %B %Y")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    user_prompt = (
        f"Сегодня {today}. Проанализируй ПРЕДСТОЯЩИЙ матч CS2: {query}\n\n"
        "ОБЯЗАТЕЛЬНО используй веб-поиск на HLTV.org и Liquipedia чтобы найти:\n"
        "- Рейтинги HLTV обеих команд\n"
        "- Составы и ростер-изменения\n"
        "- Win% за 3 месяца (LAN + online)\n"
        "- Map pool: win% на каждой карте, пермабаны\n"
        "- H2H последние встречи\n"
        "- Форму звёздных игроков\n"
        "- Турнир, стадию, формат\n"
        "НЕ используй устаревшие данные из памяти — ТОЛЬКО свежие из поиска.\n"
        "Верни результат СТРОГО в формате JSON как описано в системном промпте."
        + lang_suffix
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        system=CS2_SYSTEM_PROMPT,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Extract JSON (same parser as tennis)
    all_text = ""
    for block in response.content:
        if hasattr(block, "text") and block.text:
            all_text += "\n" + block.text

    if not all_text.strip():
        raise ValueError("Claude returned no text response")

    text = all_text.strip()

    if "```json" in text:
        json_part = text.split("```json")[1].split("```")[0].strip()
        try:
            data = json.loads(json_part)
            # Add Bo3 distribution
            p = data.get("probability", 0.5)
            fmt = data.get("format", "Bo3")
            if fmt == "Bo5":
                data["distribution"] = bo5_distribution(p)
            elif fmt == "Bo1":
                data["distribution"] = {"p_win": p}
            else:
                data["distribution"] = bo3_distribution(p)
            return data
        except json.JSONDecodeError:
            pass

    # Fallback: find largest JSON
    data = None
    best_len = 0
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            start = i
            for j in range(i, len(text)):
                if text[j] == '{': depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:j+1]
                        if len(candidate) > best_len:
                            try:
                                parsed = json.loads(candidate)
                                if isinstance(parsed, dict) and ("team1" in parsed or "probability" in parsed):
                                    data = parsed
                                    best_len = len(candidate)
                            except json.JSONDecodeError:
                                pass
                        break
        i += 1

    if data is None:
        return {"_raw_text": text, "probability": 0.5, "format": "Bo3",
                "team1": {"name": "?", "short": "T1"}, "team2": {"name": "?", "short": "T2"},
                "favorite": 1, "distribution": bo3_distribution(0.5)}

    p = data.get("probability", 0.5)
    fmt = data.get("format", "Bo3")
    if fmt == "Bo5":
        data["distribution"] = bo5_distribution(p)
    elif fmt == "Bo1":
        data["distribution"] = {"p_win": p}
    else:
        data["distribution"] = bo3_distribution(p)

    return data


def format_cs2_summary(data: dict) -> str:
    """Format CS2 analysis for Telegram."""
    if "_raw_text" in data:
        raw = data["_raw_text"]
        if len(raw) > 3900: raw = raw[:3897] + "..."
        return f"🎮 <b>CS2 Analysis</b>\n\n{raw}\n\n<i>⚠️ Research analysis</i>"
    p = data.get("probability", 0.5)
    fav_idx = data.get("favorite", 1)
    t1 = data.get("team1", {})
    t2 = data.get("team2", {})
    fav = t1 if fav_idx == 1 else t2
    dog = t2 if fav_idx == 1 else t1
    fav_pct = round(p * 100)
    dist = data.get("distribution", {})

    lines = []
    lines.append(f"🎮 <b>CS2 | {data.get('tournament', '')} | {data.get('stage', '')}</b>")
    lines.append(f"📅 {data.get('date', '')} | {data.get('format', 'Bo3')}")
    lines.append("")
    lines.append(f"<b>{t1.get('name', '')} [{t1.get('short', '')}]</b>  vs  <b>{t2.get('name', '')} [{t2.get('short', '')}]</b>")
    lines.append(f"HLTV #{t1.get('hltv_rank', '?')} | HLTV #{t2.get('hltv_rank', '?')}")
    lines.append("")

    bar_len = 20
    fav_blocks = round(bar_len * p)
    bar = "🟩" * fav_blocks + "🟥" * (bar_len - fav_blocks)
    lines.append(bar)
    lines.append(f"<b>{fav.get('short', fav.get('name', ''))} {fav_pct}%</b> — {dog.get('short', dog.get('name', ''))} {100 - fav_pct}%")
    lines.append("")

    # Player status
    ps = data.get("player_status", {})
    if ps:
        lines.append("🏥 <b>Состояние игроков:</b>")
        if ps.get("team1"):
            lines.append(f"  {t1.get('short', t1.get('name', 'T1'))}: {str(ps['team1'])[:100]}")
        if ps.get("team2"):
            lines.append(f"  {t2.get('short', t2.get('name', 'T2'))}: {str(ps['team2'])[:100]}")
        lines.append("")

    # Map veto
    veto = data.get("map_veto", {})
    maps = veto.get("expected_maps", [])
    if maps:
        lines.append("🗺 <b>Ожидаемые карты:</b>")
        for m in maps:
            lines.append(f"  {m}")
        lines.append("")

    # Factors
    factors = data.get("factors", [])
    if factors:
        lines.append("⚖️ <b>Факторы:</b>")
        for f in factors[:6]:
            lines.append(f"  {f.get('num', '')}. {f.get('name', '')}: {f.get('shift', '')}")
        lines.append("")

    # Verdict
    verdict = data.get("verdict", "")
    if verdict:
        lines.append(f"🏆 <b>Вердикт:</b>")
        if len(verdict) > 350:
            verdict = verdict[:347] + "..."
        lines.append(verdict)
        lines.append("")

    confidence = data.get("confidence", "")
    if confidence:
        lines.append(f"📌 Уверенность: <b>{confidence}</b>")

    lines.append("")
    lines.append("<i>⚠️ Исследовательский анализ, не рекомендация по ставкам</i>")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# DOTA 2 ANALYSIS
# ═══════════════════════════════════════════════════════════

async def analyze_dota2(query: str, lang_suffix: str = "") -> dict:
    """Analyze a Dota 2 match using Claude API with web search."""
    from datetime import date
    today = date.today().strftime("%d %B %Y")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user_prompt = (
        f"Сегодня {today}. Проанализируй ПРЕДСТОЯЩИЙ матч Dota 2: {query}\n\n"
        "ОБЯЗАТЕЛЬНО используй веб-поиск на Liquipedia и dotabuff:\n"
        "- Рейтинги обеих команд, составы, ростер-изменения\n"
        "- Win% за 3 месяца, текущий патч и мета\n"
        "- Сигнатурные герои, H2H, турнир, формат\n"
        "НЕ используй устаревшие данные — ТОЛЬКО свежие из поиска.\n"
        "Верни результат СТРОГО в формате JSON."
        + lang_suffix
    )
    response = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=16000, system=DOTA2_SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        messages=[{"role": "user", "content": user_prompt}],
    )
    all_text = ""
    for block in response.content:
        if hasattr(block, "text") and block.text:
            all_text += "\n" + block.text
    if not all_text.strip():
        raise ValueError("Claude returned no text response")
    text = all_text.strip()
    if "```json" in text:
        json_part = text.split("```json")[1].split("```")[0].strip()
        try:
            data = json.loads(json_part)
            p = data.get("probability", 0.5)
            fmt = data.get("format", "Bo3")
            if fmt == "Bo5": data["distribution"] = bo5_distribution(p)
            elif fmt in ("Bo1","Bo2"): data["distribution"] = {"p_win": p}
            else: data["distribution"] = bo3_distribution(p)
            return data
        except json.JSONDecodeError:
            pass
    data = None; best_len = 0; i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0; start = i
            for j in range(i, len(text)):
                if text[j] == '{': depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:j+1]
                        if len(candidate) > best_len:
                            try:
                                parsed = json.loads(candidate)
                                if isinstance(parsed, dict) and ("team1" in parsed or "probability" in parsed):
                                    data = parsed; best_len = len(candidate)
                            except json.JSONDecodeError: pass
                        break
        i += 1
    if data is None:
        return {"_raw_text": text, "probability": 0.5, "format": "Bo3",
                "team1": {"name": "?", "short": "T1"}, "team2": {"name": "?", "short": "T2"},
                "favorite": 1, "distribution": bo3_distribution(0.5)}
    p = data.get("probability", 0.5); fmt = data.get("format", "Bo3")
    if fmt == "Bo5": data["distribution"] = bo5_distribution(p)
    elif fmt in ("Bo1","Bo2"): data["distribution"] = {"p_win": p}
    else: data["distribution"] = bo3_distribution(p)
    return data


def format_dota2_summary(data: dict) -> str:
    """Format Dota 2 analysis for Telegram."""
    if "_raw_text" in data:
        raw = data["_raw_text"]
        if len(raw) > 3900: raw = raw[:3897] + "..."
        return f"⚔️ <b>Dota 2 Analysis</b>\n\n{raw}\n\n<i>⚠️ Research analysis</i>"
    p = data.get("probability", 0.5)
    fav_idx = data.get("favorite", 1)
    t1 = data.get("team1", {}); t2 = data.get("team2", {})
    fav = t1 if fav_idx == 1 else t2
    dog = t2 if fav_idx == 1 else t1
    fav_pct = round(p * 100)
    lines = []
    lines.append(f"⚔️ <b>Dota 2 | {data.get('tournament', '')} | {data.get('stage', '')}</b>")
    lines.append(f"📅 {data.get('date', '')} | {data.get('format', 'Bo3')}")
    lines.append("")
    lines.append(f"<b>{t1.get('name', '')}</b>  vs  <b>{t2.get('name', '')}</b>")
    lines.append("")
    bar_len = 20; fav_blocks = round(bar_len * p)
    lines.append("🟩" * fav_blocks + "🟥" * (bar_len - fav_blocks))
    lines.append(f"<b>{fav.get('short', fav.get('name', ''))} {fav_pct}%</b> — {dog.get('short', dog.get('name', ''))} {100 - fav_pct}%")
    lines.append("")
    meta = data.get("meta_analysis", {})
    if meta:
        lines.append("🎯 <b>Мета/драфт:</b>")
        if meta.get("current_patch"): lines.append(f"  Патч: {meta['current_patch']}")
        if meta.get("team1_fit"): lines.append(f"  {t1.get('short', '')}: {meta['team1_fit']}")
        if meta.get("team2_fit"): lines.append(f"  {t2.get('short', '')}: {meta['team2_fit']}")
        lines.append("")
    ps = data.get("player_status", {})
    if ps:
        lines.append("🏥 <b>Состояние игроков:</b>")
        if ps.get("team1"): lines.append(f"  {t1.get('short', t1.get('name', 'T1'))}: {str(ps['team1'])[:100]}")
        if ps.get("team2"): lines.append(f"  {t2.get('short', t2.get('name', 'T2'))}: {str(ps['team2'])[:100]}")
        lines.append("")
    factors = data.get("factors", [])
    if factors:
        lines.append("⚖️ <b>Факторы:</b>")
        for f in factors[:6]:
            lines.append(f"  {f.get('num','')}. {f.get('name','')}: {f.get('shift','')}")
        lines.append("")
    verdict = data.get("verdict", "")
    if verdict:
        lines.append(f"🏆 <b>Вердикт:</b>")
        if len(verdict) > 350: verdict = verdict[:347] + "..."
        lines.append(verdict)
        lines.append("")
    confidence = data.get("confidence", "")
    if confidence:
        lines.append(f"📌 Уверенность: <b>{confidence}</b>")
    lines.append("")
    lines.append("<i>⚠️ Исследовательский анализ, не рекомендация по ставкам</i>")
    return "\n".join(lines)
