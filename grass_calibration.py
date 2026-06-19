"""
Калибровка вероятностей по результатам Accuracy Audit 18.06.2026.

Применяет 7 правок методологии которые исправляют систематические перекосы:

1. Cap топ-сидов R1 ATP 500 на грассе — не выше 80% (была переуверенность)
2. Q-бонус +5% андердогу на траве (квалификант разогрет)
3. Penalty -5% за пост-турнирную усталость (играл финал/SF на прошлой неделе)
4. Surface-debut flag — звёзды-дебютанты на грассе кэп 58%
5. Age 30+ на траве = +2% (опыт > молодость на быстрых покрытиях)
6. Miracle-momentum +5% после спасения MP в прошлом матче
7. Defending-champion bonus = 0% (миф разрушен Бубликом в Halle)

Эти правки применяются в `_validate_and_normalize()` в analyzer.py
ПОСЛЕ валидации и нормализации, но ДО финального присвоения P.

Все правки логируются — чтобы в /stats можно было увидеть какие сработали.
"""

import logging
import re
from typing import Dict, Tuple, List

logger = logging.getLogger(__name__)

# ────────────────── КОНСТАНТЫ ──────────────────

GRASS_KEYWORDS = ["grass", "трава", "терра", "halle", "queen", "queens",
                  "wimbledon", "уимблдон", "hertogenbosch", "libema",
                  "eastbourne", "newport"]

ATP_500_TOURNAMENTS = ["halle", "queens", "queen", "barcelona",
                       "rotterdam", "dubai", "rio", "vienna"]

R1_PATTERNS = [r"\br1\b", r"\bround\s*1\b", r"round of 32", r"r32",
               "первый круг", "1/16"]


# ────────────────── ХЕЛПЕРЫ ──────────────────

def _is_grass(data: dict) -> bool:
    """Определяет — играется ли матч на траве."""
    surface = str(data.get("surface", "")).lower()
    tour = str(data.get("tournament", "")).lower()
    return any(k in surface or k in tour for k in GRASS_KEYWORDS)


def _is_atp_500(data: dict) -> bool:
    tour = str(data.get("tournament", "")).lower()
    tour_level = str(data.get("tour", "")).lower()
    if "500" in tour_level or "atp 500" in tour_level:
        return True
    return any(k in tour for k in ATP_500_TOURNAMENTS)


def _is_r1(data: dict) -> bool:
    rnd = str(data.get("round", "")).lower()
    return any(re.search(p, rnd) for p in R1_PATTERNS)


def _is_qualifier(player: dict) -> bool:
    """Игрок прошёл квалификацию?"""
    profile = " ".join(str(p) for p in (player.get("profile") or [])).lower()
    seed = str(player.get("seed", "")).lower()
    return ("[q]" in seed or "(q)" in seed or "qualifier" in profile or
            "квалификант" in profile or "q-" in seed or seed == "q")


def _is_defending_champion(player: dict, tournament: str) -> bool:
    profile = " ".join(str(p) for p in (player.get("profile") or [])).lower()
    return ("defending champion" in profile or "защищает титул" in profile or
            "действующий чемпион" in profile)


def _player_age(player: dict) -> int:
    """Извлекает возраст из profile. Возвращает 0 если не нашёл."""
    profile = " ".join(str(p) for p in (player.get("profile") or []))
    # Ищем паттерны "30 лет", "age 32", "32 года"
    m = re.search(r"(\d{2})\s*(?:лет|год|года|age)", profile.lower())
    if m:
        try:
            age = int(m.group(1))
            if 16 <= age <= 50:
                return age
        except ValueError:
            pass
    return 0


def _is_surface_debut(player: dict) -> bool:
    """Дебют на грассе — мало grass-матчей."""
    profile = " ".join(str(p) for p in (player.get("profile") or [])).lower()
    return ("grass-debut" in profile or "first grass" in profile or
            "дебют на грассе" in profile or "впервые на траве" in profile)


def _had_miracle_save(player: dict) -> bool:
    """Игрок отыграл match point в предыдущем матче?"""
    profile = " ".join(str(p) for p in (player.get("profile") or [])).lower()
    return ("saved mp" in profile or "saved match point" in profile or
            "отыграл mp" in profile or "отыграл матч-поинт" in profile or
            "spared 2 mp" in profile or "отыграл 2 mp" in profile)


def _played_final_recently(player: dict) -> bool:
    """Играл финал или SF на прошлой неделе?"""
    profile = " ".join(str(p) for p in (player.get("profile") or [])).lower()
    return ("финалист" in profile and "stuttgart" in profile or
            "финал прошлой" in profile or "после финала" in profile or
            "недавний финалист" in profile or "stuttgart f " in profile or
            "stuttgart sf" in profile)


# ────────────────── ОСНОВНАЯ ФУНКЦИЯ ──────────────────

def apply_grass_calibration(data: dict, sport: str = "tennis") -> Tuple[dict, List[str]]:
    """
    Применяет grass-specific калибровку.

    Returns:
        (modified_data, applied_fixes): данные с поправками + список применённых
    """
    if sport != "tennis":
        return data, []

    if not _is_grass(data):
        return data, []  # Только для матчей на траве

    p1 = data.get("player1", {}) or {}
    p2 = data.get("player2", {}) or {}
    fav_idx = data.get("favorite", 1)
    fav = p1 if fav_idx == 1 else p2
    dog = p2 if fav_idx == 1 else p1
    p = data.get("probability", 0.5)
    if not isinstance(p, (int, float)):
        p = 0.5

    applied = []
    original_p = p

    # ── 1) Cap топ-сидов R1 ATP 500 grass на 80% ──
    if _is_atp_500(data) and _is_r1(data):
        fav_seed = fav.get("seed")
        try:
            seed_num = int(fav_seed) if fav_seed else 99
        except (ValueError, TypeError):
            seed_num = 99
        if seed_num <= 8 and p > 0.80:
            new_p = 0.80
            applied.append(f"cap_r1_atp500_grass: {p:.2f} → {new_p:.2f} "
                           f"(топ-сид [{fav_seed}] R1 grass)")
            p = new_p

    # ── 2) Q-бонус +5% андердогу ──
    if _is_qualifier(dog) and p > 0.50:
        new_p = max(0.50, p - 0.05)  # снижаем P фаворита на 5%
        applied.append(f"q_bonus_underdog: {p:.2f} → {new_p:.2f} "
                       f"(андердог-Q разогрет)")
        p = new_p

    # ── 3) Penalty -5% за пост-турнирную усталость ──
    if _played_final_recently(fav):
        new_p = max(0.50, p - 0.05)
        applied.append(f"penalty_post_final: {p:.2f} → {new_p:.2f} "
                       f"(фаворит играл финал/SF недавно)")
        p = new_p

    # ── 4) Surface-debut flag — звезда-дебютант на грассе ──
    if _is_surface_debut(fav):
        new_p = min(p, 0.58)
        if new_p < p:
            applied.append(f"surface_debut: {p:.2f} → {new_p:.2f} "
                           f"(фаворит дебютирует на грассе)")
            p = new_p

    # ── 5) Age 30+ на траве — бонус опыту ──
    fav_age = _player_age(fav)
    dog_age = _player_age(dog)
    if dog_age >= 30 and fav_age > 0 and fav_age < dog_age and p > 0.55:
        new_p = max(0.50, p - 0.02)
        applied.append(f"age_grass_bonus: {p:.2f} → {new_p:.2f} "
                       f"(андердог 30+, опыт на грассе)")
        p = new_p

    # ── 6) Miracle-momentum +5% если игрок отыграл MP ──
    if _had_miracle_save(fav):
        new_p = min(0.92, p + 0.05)
        applied.append(f"miracle_momentum_fav: {p:.2f} → {new_p:.2f} "
                       f"(фаворит отыграл MP в прошлом матче)")
        p = new_p
    elif _had_miracle_save(dog):
        new_p = max(0.50, p - 0.05)
        applied.append(f"miracle_momentum_dog: {p:.2f} → {new_p:.2f} "
                       f"(андердог отыграл MP — психо-edge)")
        p = new_p

    # ── 7) Defending-champion bonus = 0% ──
    tournament = data.get("tournament", "")
    if _is_defending_champion(fav, tournament):
        # Если в factors есть defending-bonus — обнуляем
        factors = data.get("factors", []) or []
        for f in factors:
            name = str(f.get("name", "")).lower()
            shift = str(f.get("shift", ""))
            if ("defend" in name or "champion" in name or
                    "защ" in name or "чемпион" in name):
                # Извлечь старую величину сдвига
                m = re.search(r"[+-]?(\d+(?:\.\d+)?)", shift)
                if m:
                    old_shift = float(m.group(1))
                    new_p = max(0.50, p - old_shift / 100)
                    if new_p < p:
                        applied.append(
                            f"defending_champ_zero: {p:.2f} → {new_p:.2f} "
                            f"(убран бонус +{old_shift}% защитнику титула)")
                        p = new_p
                        f["shift"] = "+0% (Bublik-fix)"
                break

    # Финальный clamp
    p = max(0.05, min(0.95, p))

    if applied:
        data["probability"] = round(p, 3)
        data["_grass_calibration"] = applied
        logger.info(f"Grass calibration: {len(applied)} правок применено, "
                    f"P: {original_p:.2f} → {p:.2f}")

    return data, applied
