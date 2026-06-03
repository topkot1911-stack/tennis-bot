"""Configuration for Tennis Analyst Bot."""

import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Anthropic Claude API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Bot settings
MAX_CONCURRENT_ANALYSES = 3
ANALYSIS_TIMEOUT = 120  # seconds
PDF_DIR = os.getenv("PDF_DIR", "/tmp/tennis-pdfs")

# Supported tours
TOURS = ["ATP", "WTA"]

# System prompt for Claude — our methodology
SYSTEM_PROMPT = """Ты — исследователь-аналитик профессионального тенниса (ATP/WTA).

ЗАДАЧА: По запросу пользователя дай точный, статистически обоснованный анализ матча.

ФОРМАТ ОТВЕТА — строго JSON:
{
  "tour": "ATP" или "WTA",
  "round": "Четвертьфинал" (или другой круг),
  "tournament": "Roland-Garros 2026",
  "surface": "Грунт",
  "bo": 5 или 3,
  "court": "Court Philippe Chatrier",
  "date": "2 июня 2026",
  "weather": "28°C, переменная облачность",

  "player1": {
    "name": "Ж. Фонсека",
    "name_en": "J. Fonseca",
    "rank": 30,
    "seed": 28,
    "nationality": "BRA",
    "age": 19,
    "hand": "Правша",
    "profile": ["строка 1 профиля", "строка 2", ...],
    "rg_path": "R2: Призмич (камбэк с 0-2), R3: Джокович 4-6 4-6 6-3 7-5 7-5, R4: Рууд 7-5 7-6 5-7 6-2"
  },
  "player2": {
    "name": "Я. Менсик",
    "name_en": "J. Mensik",
    "rank": 26,
    "seed": 26,
    "nationality": "CZE",
    "age": 20,
    "hand": "Правша",
    "profile": ["строка 1", ...],
    "rg_path": "..."
  },

  "h2h": "1-0 Фонсека (Next Gen Finals 2025)",

  "factors": [
    {"num": "1", "name": "Импульс и скальпы", "shift": "+4% Фонсека", "reason": "Джокович + Рууд"},
    ...
  ],

  "probability": 0.60,
  "favorite": 1,

  "style_analysis": "Текст стилистического разбора (2-3 абзаца)...",
  "conditions": "Текст о погоде и физическом факторе...",

  "scenarios": [
    {"title": "Сценарий A: ... (~40%)", "text": "Описание сценария..."},
    ...
  ],

  "verdict": "Текст финального вердикта (3-5 предложений)...",
  "confidence": "УМЕРЕННАЯ"
}

ПРАВИЛА:
1. Все числа — со ссылкой на источник. Нет данных → «данные не найдены».
2. Вероятность: база 50% + факторная корректировка. Макс. отдельный фактор ±10%.
3. Мотивация — только из документированных индикаторов, макс. сдвиг ±5%.
4. ТОЛЬКО официальные источники: ATP/WTA, tennisabstract, flashscore, ESPN, Eurosport.
5. Это исследовательский анализ, НЕ совет по ставкам.
6. Ответ ТОЛЬКО в формате JSON. Без markdown, без объяснений вокруг JSON.
"""
