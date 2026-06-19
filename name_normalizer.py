"""
Нормализация имён игроков и команд для устранения дублей в БД.

Превращает:
    "Дениил Медведев" → "Медведев"
    "Daniil Медведев" → "Медведев"
    "А. Зверев"       → "Зверев"
    "Александр Зверев"→ "Зверев"
    "Дэниил Медведев" → "Медведев"

Использовать перед save_prediction чтобы:
- Один игрок не записывался как 3 разных
- Дедупликация работала корректно
- /stats показывал правдивые цифры
"""

import re
from typing import Optional

# Карта перевода фамилий — последнее слово приводится к каноничному русскому виду
# Расширяй по мере необходимости — добавляй новых игроков сюда
CANONICAL_LASTNAMES = {
    # Russian/EN dual spellings
    "медведев": "Медведев",
    "medvedev": "Медведев",
    "зверев": "Зверев",
    "zverev": "Зверев",
    "рублёв": "Рублёв",
    "рублев": "Рублёв",
    "rublev": "Рублёв",
    "хачанов": "Хачанов",
    "khachanov": "Хачанов",
    "хуркач": "Хуркач",
    "гуркач": "Хуркач",
    "hurkacz": "Хуркач",
    "альтмайер": "Альтмайер",
    "altmaier": "Альтмайер",
    "шелтон": "Шелтон",
    "shelton": "Шелтон",
    "сонего": "Сонего",
    "sonego": "Сонего",
    "пол": "Пол",
    "paul": "Пол",
    "свайда": "Свайда",
    "svajda": "Свайда",
    "коболли": "Коболли",
    "cobolli": "Коболли",
    "тиафо": "Тиафо",
    "tiafoe": "Тиафо",
    "бублик": "Бублик",
    "bublik": "Бублик",
    "беллуччи": "Беллуччи",
    "bellucci": "Беллуччи",
    "коллиньон": "Коллиньон",
    "collignon": "Коллиньон",
    "попырин": "Попырин",
    "popyrin": "Попырин",
    "куинн": "Куинн",
    "quinn": "Куинн",
    "этчеверри": "Этчеверри",
    "etcheverry": "Этчеверри",
    "коприва": "Коприва",
    "копршива": "Коприва",
    "kopriva": "Коприва",
    "ковачевич": "Ковачевич",
    "kovacevic": "Ковачевич",
    "черундоло": "Черундоло",
    "серундоло": "Черундоло",
    "cerundolo": "Черундоло",
    "давидович": "Давидович Фокина",
    "фокина": "Давидович Фокина",
    "norrie": "Норри",
    "норри": "Норри",
    "лехецка": "Лехечка",
    "лехечка": "Лехечка",
    "lehecka": "Лехечка",
    "майчжак": "Майхжак",
    "мальчжак": "Майхжак",
    "majchrzak": "Майхжак",
    "де": "де Минаур",  # для "де Минаур" — handle special case
    "минаур": "де Минаур",
    "минайо": "де Минаур",
    "minaur": "де Минаур",
    "диалло": "Диалло",
    "diallo": "Диалло",
    "табило": "Табило",
    "tabilo": "Табило",
    "хиджиката": "Хидзиката",
    "хидзиката": "Хидзиката",
    "hijikata": "Хидзиката",
    "бусе": "Бусе",
    "buse": "Бусе",
    "гирон": "Гирон",
    "giron": "Гирон",
    "мпеши": "Мпеши Перрикар",
    "перрикар": "Мпеши Перрикар",
    "perricard": "Мпеши Перрикар",
    "муте": "Муте",
    "moutet": "Муте",
    "шаповалов": "Шаповалов",
    "shapovalov": "Шаповалов",
    "atmane": "Атман",
    "атман": "Атман",
    "атмане": "Атман",
    "fonseca": "Фонсека",
    "фонсека": "Фонсека",
    "ханфманн": "Ханфманн",
    "hanfmann": "Ханфманн",
    "fritz": "Фритц",
    "фритц": "Фритц",
    "марожан": "Марожан",
    "marozsan": "Марожан",
    "marozsán": "Марожан",
    "kecmanovic": "Кечманович",
    "кечманович": "Кечманович",
    "humbert": "Юмбер",
    "юмбер": "Юмбер",
    "умбер": "Юмбер",
    "cilic": "Чилич",
    "чилич": "Чилич",
    "nakashima": "Накашима",
    "накашима": "Накашима",
    "fucsovics": "Фуцсович",
    "фуцсович": "Фуцсович",
    "rinderknech": "Риндернекх",
    "риндернекх": "Риндернекх",
    "medjedovic": "Медьедович",
    "медьедович": "Медьедович",
    "vandezandschulp": "ван де Зандсюлп",
    "ванзандшулп": "ван де Зандсюлп",
    "зандсюлп": "ван де Зандсюлп",
    "wendelken": "Вендлкен",
    "вендлкен": "Вендлкен",
    "mannarino": "Маннарино",
    "маннарино": "Маннарино",
    "mensik": "Менсик",
    "менсик": "Менсик",
    "brooksby": "Бруксби",
    "бруксби": "Бруксби",
    "fery": "Фери",
    "фери": "Фери",
    "shimabukuro": "Шимабукуро",
    "shima": "Шимабукуро",
    "симабукуро": "Шимабукуро",
    "шимабукуро": "Шимабукуро",
    "griekspoor": "Грикспур",
    "грикспур": "Грикспур",
    "aliassime": "Оже-Альяссим",
    "auger-aliassime": "Оже-Альяссим",
    "ажер": "Оже-Альяссим",
    "оже": "Оже-Альяссим",
    "tien": "Тьен",
    "тьен": "Тьен",
    "borges": "Боржес",
    "боржес": "Боржес",
    "боргес": "Боржес",
    "lehecka": "Лехечка",
}

# CS2 / Dota 2 — команды используются как есть, только приводим к каноническому виду
CANONICAL_TEAMS = {
    "vitality": "Vitality",
    "team vitality": "Vitality",
    "navi": "NAVI",
    "natus vincere": "NAVI",
    "natusvincere": "NAVI",
    "betboom": "BetBoom",
    "betboom team": "BetBoom",
    "g2": "G2",
    "g2 esports": "G2",
    "mouz": "MOUZ",
    "furia": "FURIA",
    "9z": "9z",
    "aurora": "Aurora",
    "aurora gaming": "Aurora",
    "falcons": "Falcons",
    "team falcons": "Falcons",
    "monte": "Monte",
    "mongolz": "MongolZ",
    "the mongolz": "MongolZ",
    "themongolz": "MongolZ",
    "fut": "FUT",
    "fut esports": "FUT",
    "b8": "B8",
    "spirit": "Spirit",
    "team spirit": "Spirit",
    "legacy": "Legacy",
    "parivision": "PARIVISION",
}


def normalize_player_name(raw: Optional[str], sport: str = "tennis") -> str:
    """
    Приводит имя игрока / команды к каноническому виду.

    Returns:
        Канонической имя или исходное (если не нашли в словаре).
        НИКОГДА не возвращает None или "?" — на вход "?" → возвращает "?"
    """
    if not raw or raw == "?":
        return raw or "?"

    cleaned = raw.strip()

    # CS2/Dota — поиск по полному названию команды
    if sport in ("cs2", "dota2"):
        key = cleaned.lower().replace(".", "").replace("-", " ").strip()
        if key in CANONICAL_TEAMS:
            return CANONICAL_TEAMS[key]
        # try short version
        first_word = key.split()[0] if key else ""
        if first_word in CANONICAL_TEAMS:
            return CANONICAL_TEAMS[first_word]
        return cleaned  # ничего не нашли — возвращаем как есть

    # Tennis — берём ПОСЛЕДНЕЕ слово (фамилию) и ищем в словаре
    # Сначала убираем инициалы "А. Зверев" → "Зверев"
    words = cleaned.replace(".", " ").split()
    if not words:
        return cleaned

    # Ищем по всем словам — берём первое попавшееся в словаре
    for w in reversed(words):  # начинаем с конца — фамилия обычно последняя
        key = w.lower().strip()
        if key in CANONICAL_LASTNAMES:
            return CANONICAL_LASTNAMES[key]

    # Если не нашли — возвращаем последнее слово как есть
    return words[-1] if words else cleaned


def is_valid_prediction(p1: str, p2: str, fav: str, prob: float) -> bool:
    """Проверка что прогноз валидный — не мусор."""
    if not p1 or not p2 or not fav:
        return False
    if p1 == "?" or p2 == "?" or fav == "?":
        return False
    if not isinstance(prob, (int, float)) or prob <= 0 or prob >= 1:
        # prob == 0.5 ровно — подозрительно (default value)
        return False
    if prob == 0.5:
        return False
    if len(p1.strip()) < 2 or len(p2.strip()) < 2:
        return False
    return True
