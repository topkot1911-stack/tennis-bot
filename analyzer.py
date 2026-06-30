"""Tennis match analyzer using Claude API + mathematical model."""

import json
import math
import logging
import re
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, SYSTEM_PROMPT, CS2_SYSTEM_PROMPT, DOTA2_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# SELF-CHECK / NORMALIZATION (applied to every analyze_* result)
# ═══════════════════════════════════════════════════════════

_MOTIV_KEYWORDS = ("мотивац", "motivat", "психолог", "psychol")
_PCT_RE = re.compile(r"([+-]?)\s*(\d+(?:[.,]\d+)?)\s*%")


def _parse_shift_pct(shift_text: str):
    """Extract a signed percentage value from a factor 'shift' string. Returns 0 on failure."""
    if not isinstance(shift_text, str):
        return 0.0
    m = _PCT_RE.search(shift_text)
    if not m:
        return 0.0
    sign = -1.0 if m.group(1) == "-" else 1.0
    try:
        return sign * float(m.group(2).replace(",", "."))
    except ValueError:
        return 0.0


def _dict_to_text(value, max_items=4):
    """Plain-text rendering of any dict/list — same idea as pdf_generator._to_text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_dict_to_text(x, max_items) for x in value[:max_items]]
        return "; ".join(p for p in parts if p)
    if isinstance(value, dict):
        if "total" in value or "overall" in value:
            head = str(value.get("total") or value.get("overall") or "")
            extras = []
            if isinstance(value.get("surfaces"), dict):
                for k, v in list(value["surfaces"].items())[:3]:
                    extras.append(f"{k}: {_dict_to_text(v)}")
            rm = value.get("recent_matches") or value.get("recent")
            if isinstance(rm, list) and rm:
                last = rm[0]
                if isinstance(last, dict):
                    extras.append(f"{last.get('event','')} {last.get('result','')}".strip())
            return head + (" | " + " · ".join(extras) if extras else "")
        parts = [f"{k}: {_dict_to_text(v)}" for k, v in list(value.items())[:max_items]]
        return " | ".join(parts)
    return str(value)


def _detect_resumed_match(data: dict) -> bool:
    """Detect tennis match that's resumed mid-way (only 3rd set remaining etc.)."""
    blobs = " ".join(str(data.get(k, "")) for k in ("round", "stage", "date", "court", "tournament"))
    blobs = blobs.lower()
    triggers = ["resumed", "продолжен", "доигровк", "3-й сет", "3rd set", "third set", "финальный сет"]
    return any(t in blobs for t in triggers)


def _validate_scenarios(scenarios, bo: int) -> list:
    """
    Bo3 allows only {2-0, 2-1, 0-2, 1-2}; Bo5 only {3-0, 3-1, 3-2, 0-3, 1-3, 2-3}.
    Replace invalid scores with a "?" placeholder and add note. Cleans the bot's
    occasional "3-0" / "1-3" garbage in Bo3 matches.
    """
    if not isinstance(scenarios, list):
        return scenarios
    valid_bo3 = {"2-0", "2-1", "0-2", "1-2"}
    valid_bo5 = {"3-0", "3-1", "3-2", "0-3", "1-3", "2-3"}
    valid = valid_bo3 if bo == 3 else valid_bo5
    score_re = re.compile(r"\b(\d-\d)\b")
    fixed = []
    for sc in scenarios:
        if not isinstance(sc, dict):
            fixed.append(sc); continue
        title = str(sc.get("title", "")) or ""
        text = str(sc.get("text", "")) or ""
        # If title contains an invalid Bo3/Bo5 score, replace it
        m = score_re.search(title)
        if m and m.group(1) not in valid:
            new = m.group(1)
            # Naive flip: 3-X → 2-X' if Bo3
            if bo == 3:
                a, b = new.split("-")
                a, b = int(a), int(b)
                a = min(a, 2); b = min(b, 2)
                if a == b: a, b = 2, 1  # avoid 2-2 nonsense
                fixed_score = f"{a}-{b}"
            else:
                fixed_score = "3-2"
            sc = dict(sc)
            sc["title"] = title.replace(m.group(1), fixed_score)
            sc["_score_fixed"] = True
        # Also strip stupid clauses like "(но в BO3 это невозможно)"
        text = re.sub(r"\([^)]*невозможн[^)]*\)\s*\.?", "", text, flags=re.IGNORECASE).strip()
        if text != sc.get("text"):
            sc = dict(sc)
            sc["text"] = text
        fixed.append(sc)
    return fixed


def _normalize_scenario_probs(scenarios) -> list:
    """Scale scenario probability tags to sum to 100% (±2% tolerance)."""
    if not isinstance(scenarios, list):
        return scenarios
    pct_re = re.compile(r"~?\s*(\d+(?:[.,]\d+)?)\s*%")
    probs = []
    for sc in scenarios:
        if not isinstance(sc, dict):
            probs.append(None); continue
        m = pct_re.search(str(sc.get("title", "")))
        if not m:
            m = pct_re.search(str(sc.get("text", "")))
        probs.append(float(m.group(1).replace(",", ".")) if m else None)
    valid = [p for p in probs if p is not None]
    if not valid:
        return scenarios
    total = sum(valid)
    if total <= 0:
        return scenarios
    # Tighter tolerance: anything outside 99..101 gets rescaled. Previously
    # 90% sums silently leaked into PDFs (G2/Legacy, de Minaur/Majchrzak).
    if 99 <= total <= 101:
        return scenarios
    logger.info("Scenario probs sum=%.1f, rescaling to 100%%", total)
    scale = 100.0 / total
    out = []
    for sc, p in zip(scenarios, probs):
        if p is None:
            out.append(sc); continue
        new_p = round(p * scale)
        sc = dict(sc)
        sc["title"] = pct_re.sub(f"~{new_p}%", str(sc.get("title", "")), count=1)
        out.append(sc)
    return out


def _normalize_factor_sum(factors, cap: float) -> list:
    """If Σ|shift| exceeds cap (e.g. 22%), scale all factors down proportionally."""
    if not isinstance(factors, list):
        return factors
    total = sum(abs(_parse_shift_pct(f.get("shift", ""))) for f in factors if isinstance(f, dict))
    if total <= cap or total == 0:
        return factors
    scale = cap / total
    out = []
    for f in factors:
        if not isinstance(f, dict):
            out.append(f); continue
        val = _parse_shift_pct(f.get("shift", "")) * scale
        sign = "+" if val >= 0 else "−"
        # Preserve trailing words (player/team name)
        text = str(f.get("shift", ""))
        rest = re.sub(_PCT_RE, "", text, count=1).strip()
        f = dict(f)
        f["shift"] = f"{sign}{abs(val):.1f}% {rest}".strip()
        out.append(f)
    return out


def _tournament_known(name: str) -> bool:
    """Hard list of recognisable tournaments — used to flag «Unknown» as low-data."""
    if not name:
        return False
    n = name.lower()
    if any(w in n for w in ("неизвест", "unknown", "tbd", "n/a")):
        return False
    if len(n.strip()) < 3:
        return False
    return True


# ═══════════════════════════════════════════════════════════
# WHITELISTS (P2 — anti-hallucination)
# ═══════════════════════════════════════════════════════════

# Current rosters of CS2 teams the bot tends to hallucinate about.
# Format: lowercase team name → set of player nicks (case-insensitive match).
# Keep ONLY the most-frequent-error teams; rosters change so prefer few rows.
CS2_ROSTERS = {
    "vitality":     {"zywoo", "apex", "ropz", "flamez", "mezii"},
    "g2 esports":   {"niko", "hunter-", "m0nesy", "hooxi", "jks"},
    "g2":           {"niko", "hunter-", "m0nesy", "hooxi", "jks"},
    "navi":         {"w0nderful", "aleksib", "makazze", "im", "b1t"},
    "natus vincere":{"w0nderful", "aleksib", "makazze", "im", "b1t"},
    "mouz":         {"brollan", "torzsi", "jimpphat", "xertion", "spinx"},
    "furia":        {"kscerato", "yuurih", "fallen", "yekindar", "molodoy"},
    "spirit":       {"donk", "magixx", "chopper", "sh1ro", "zont1x"},
    "team spirit":  {"donk", "magixx", "chopper", "sh1ro", "zont1x"},
    "falcons":      {"niko", "kyousuke", "teses", "kyxsan", "m0nesy"},
    "team falcons": {"niko", "kyousuke", "teses", "kyxsan", "m0nesy"},
    "faze":         {"karrigan", "rain", "frozen", "broky", "jcobbb"},
}

# Players the bot has historically transliterated wrong. Apply globally.
TRANSLIT_MAP = {
    "Majchrzak":            "Майхжак",
    "Mpetshi Perricard":    "Мпетши Перрикар",
    "Mpetshi-Perricard":    "Мпетши-Перрикар",
    "Borges":               "Боргес",
    "Bublik":               "Бублик",
    "Auger-Aliassime":      "Оже-Альяссим",
    "de Minaur":            "де Минор",
    "Lehecka":              "Лехечка",
    "Shimabukuro":          "Симабукуро",
    "Cilic":                "Чилич",
    "Cobolli":              "Коболли",
    "Tsitsipas":            "Циципас",
    "Davidovich":           "Давидович",
    "Hijikata":             "Хидзиката",
    "Bellucci":             "Беллуччи",
    "Kyrgios":              "Кириос",
    "Shelton":              "Шелтон",
    "Fritz":                "Фриц",
    "Medvedev":             "Медведев",
    # обратные кривые формы, которые точно надо подменить:
    "Маихрзак":             "Майхжак",
    "Майхрзак":             "Майхжак",
    "Маjchrzak":            "Майхжак",
    "Перриcard":            "Перрикар",
    "Богард":               "Боргес",
    "Боржеш":               "Боргес",
    "Sinneру":              "Синнеру",
    "Sinner":               "Синнер",
}

# ATP tour tier per tournament. Used to override the bot's frequent
# "ATP 250 → ТБШ" mislabelling (ТБШ = Grand Slam — wrong for 250s).
TENNIS_TIER_MAP = {
    # Grand Slams
    "australian open":   ("Grand Slam", "ТБШ"),
    "roland garros":     ("Grand Slam", "ТБШ"),
    "french open":       ("Grand Slam", "ТБШ"),
    "wimbledon":         ("Grand Slam", "ТБШ"),
    "us open":           ("Grand Slam", "ТБШ"),
    # Masters 1000
    "indian wells":      ("ATP Masters 1000", "Masters"),
    "miami open":        ("ATP Masters 1000", "Masters"),
    "monte-carlo":       ("ATP Masters 1000", "Masters"),
    "madrid open":       ("ATP Masters 1000", "Masters"),
    "rome":              ("ATP Masters 1000", "Masters"),
    "canadian open":     ("ATP Masters 1000", "Masters"),
    "cincinnati":        ("ATP Masters 1000", "Masters"),
    "shanghai":          ("ATP Masters 1000", "Masters"),
    "paris masters":     ("ATP Masters 1000", "Masters"),
    # ATP 500
    "halle":             ("ATP 500", "ATP 500"),
    "queen's":           ("ATP 500", "ATP 500"),
    "queens":            ("ATP 500", "ATP 500"),
    "hsbc championships":("ATP 500", "ATP 500"),
    "terra wortmann":    ("ATP 500", "ATP 500"),
    "barcelona open":    ("ATP 500", "ATP 500"),
    "rio open":          ("ATP 500", "ATP 500"),
    "rotterdam":         ("ATP 500", "ATP 500"),
    # ATP 250 — самые частые жертвы «ТБШ»-ошибки
    "boss open":         ("ATP 250", "ATP 250"),
    "stuttgart":         ("ATP 250", "ATP 250"),
    "libema":            ("ATP 250", "ATP 250"),
    "libéma":            ("ATP 250", "ATP 250"),
    "'s-hertogenbosch":  ("ATP 250", "ATP 250"),
    "s-hertogenbosch":   ("ATP 250", "ATP 250"),
    "hertogenbosch":     ("ATP 250", "ATP 250"),
    "rosmalen":          ("ATP 250", "ATP 250"),
    "eastbourne":        ("ATP 250", "ATP 250"),
    "mallorca":          ("ATP 250", "ATP 250"),
    "newport":           ("ATP 250", "ATP 250"),
}


def _check_cs2_roster(data: dict) -> list:
    """Return list of warning strings for players mentioned in team1/team2.star_player
    that aren't actually on that team's current roster."""
    warnings = []
    for slot in ("team1", "team2"):
        team = data.get(slot)
        if not isinstance(team, dict):
            continue
        team_name = str(team.get("name", "")).lower().strip()
        if team_name not in CS2_ROSTERS:
            continue  # team not whitelisted, skip
        allowed = CS2_ROSTERS[team_name]
        star = str(team.get("star_player", "")).lower().strip()
        if star and star.replace("[", "").replace("]", "") not in allowed:
            warnings.append(
                f"Roster check: {team.get('name')} star_player='{team.get('star_player')}' "
                f"is NOT on current roster ({sorted(allowed)})"
            )
            # Auto-fix: blank the field rather than show wrong info
            team["star_player"] = ""
    return warnings


def _apply_translit(text: str) -> str:
    """Apply TRANSLIT_MAP substitutions everywhere — Latin→Russian or fix bad Russian."""
    if not isinstance(text, str) or not text:
        return text
    for src, dst in TRANSLIT_MAP.items():
        if src in text:
            text = text.replace(src, dst)
    return text


def _translit_walk(value):
    """Recursively apply translit to all strings in a dict/list structure."""
    if isinstance(value, str):
        return _apply_translit(value)
    if isinstance(value, list):
        return [_translit_walk(v) for v in value]
    if isinstance(value, dict):
        return {k: _translit_walk(v) for k, v in value.items()}
    return value


def _tennis_tier_for(tournament_name: str):
    """Return ('Grand Slam' / 'ATP 500' / etc, short_label) or (None, None)."""
    if not tournament_name:
        return None, None
    n = tournament_name.lower()
    for key, (long_label, short_label) in TENNIS_TIER_MAP.items():
        if key in n:
            return long_label, short_label
    return None, None


def _fix_tier_mislabels(data: dict, sport: str) -> dict:
    """For tennis: if tournament is not a Grand Slam but text mentions ТБШ / Grand Slam,
    replace with the correct tier label. Same for 'Masters 1000' / 'ATP 500'."""
    if sport != "tennis":
        return data
    tour_name = str(data.get("tournament", ""))
    long_label, short_label = _tennis_tier_for(tour_name)
    if not long_label or "Grand Slam" in long_label:
        return data  # tournament unknown or already a Grand Slam
    # Patterns to replace in any text field
    bad = [r"\bТБШ\b", r"\bGrand Slam\b", r"\bMasters\s*1000\b"]
    for field in ("verdict", "style_analysis", "conditions"):
        v = data.get(field, "")
        if isinstance(v, str) and v:
            for pat in bad:
                v = re.sub(pat, short_label, v, flags=re.IGNORECASE)
            data[field] = v
    # Also fix profile lists
    for p_key in ("player1", "player2"):
        p = data.get(p_key)
        if isinstance(p, dict) and isinstance(p.get("profile"), list):
            new_prof = []
            for line in p["profile"]:
                if isinstance(line, str):
                    for pat in bad:
                        line = re.sub(pat, short_label, line, flags=re.IGNORECASE)
                new_prof.append(line)
            p["profile"] = new_prof
    # Add tournament_tier field for downstream use
    data["tournament_tier"] = long_label
    return data


def _fix_stage_inconsistency(data: dict, sport: str) -> dict:
    """
    Catches stage mislabelling like 'Quarterfinals / Upper Bracket' on a Swiss
    Stage 3 Major (e.g. IEM Cologne). Replaces with the correct stage label
    when the tournament name implies Swiss format.
    """
    if sport not in ("cs2", "dota2"):
        return data
    stage = str(data.get("stage", "")).strip()
    tournament = str(data.get("tournament", "")).lower()
    # Major or IEM Swiss stage tournaments — typical pattern
    is_swiss = any(w in tournament for w in (
        "major", "iem", "blast", "swiss",
    ))
    bad_stage_keywords = ("quarterfinals", "quarter-final", "qf", "upper bracket",
                          "playoff", "semifinal", "final")
    if is_swiss and stage and any(w in stage.lower() for w in bad_stage_keywords) \
       and "swiss" not in stage.lower():
        # Demote to neutral wording — actual round determination needs richer signal
        data["_stage_was"] = stage
        data["stage"] = "Swiss Stage (round unspecified)"
        logger.warning("Validator: stage '%s' looks like playoff label on a Swiss "
                       "tournament '%s'; replaced", stage, tournament)
    return data


def _fav_dog_names(data: dict, sport: str):
    """Return (fav_short, dog_short, fav_obj, dog_obj) based on current favorite index."""
    fav_idx = data.get("favorite", 1)
    if sport in ("cs2", "dota2"):
        t1, t2 = data.get("team1"), data.get("team2")
    else:
        t1, t2 = data.get("player1"), data.get("player2")
    fav_obj = t1 if fav_idx == 1 else t2
    dog_obj = t2 if fav_idx == 1 else t1

    def _short(obj):
        if not isinstance(obj, dict):
            return ""
        return str(obj.get("short") or obj.get("name") or "").strip()

    return _short(fav_obj), _short(dog_obj), fav_obj, dog_obj


def _net_factor_shift_for_fav(factors, fav_name: str, dog_name: str) -> float:
    """
    Sum factor shifts and return the net pull toward `fav_name`.
      +N → factors agree fav should win
      -N → factors contradict (they actually favour the underdog)

    Matches by:
      1. Full name (e.g. "Alexander Zverev" in "+3% Alexander Zverev")
      2. Last name fallback (e.g. "Zverev" in "+3% Zverev")
      3. First initial + last name (e.g. "А. Зверев" in "+3% Зверев")

    This catches the common Claude pattern where header uses full name
    "Taylor Фриц" but factor shifts use only "Фриц".
    """
    if not isinstance(factors, list):
        return 0.0

    def _name_variants(full_name: str) -> list:
        """Generate [full, last_word, first_initial+last] variants for matching."""
        if not full_name:
            return []
        full_low = full_name.lower().strip()
        variants = [full_low]
        words = full_low.replace(".", " ").split()
        if len(words) >= 2:
            # Last word (фамилия) — главный fallback
            last_word = words[-1]
            if len(last_word) >= 3:  # avoid matching "a", "de", etc.
                variants.append(last_word)
        return variants

    fav_variants = _name_variants(fav_name)
    dog_variants = _name_variants(dog_name)

    net = 0.0
    for f in factors:
        if not isinstance(f, dict):
            continue
        shift_text = str(f.get("shift", ""))
        val = _parse_shift_pct(shift_text)
        text_low = shift_text.lower()

        fav_match = any(v and v in text_low for v in fav_variants)
        dog_match = any(v and v in text_low for v in dog_variants)

        # If both match (rare — e.g. one player has another's name as substring),
        # prefer the LONGER match (full name beats last-name).
        if fav_match and dog_match:
            fav_best = max((len(v) for v in fav_variants if v in text_low), default=0)
            dog_best = max((len(v) for v in dog_variants if v in text_low), default=0)
            if fav_best >= dog_best:
                net += val
            else:
                net -= val
        elif fav_match:
            net += val
        elif dog_match:
            net -= val
        # else: neutral / "обоим" / unclear

    return net


def _validate_and_normalize(data: dict, sport: str = "tennis") -> dict:
    """
    Post-process Claude's raw JSON before sending it to the PDF/text formatter.
    Enforces methodology rules so contradictions never reach the user.
    """
    if not isinstance(data, dict):
        return data
    if "_raw_text" in data:
        # raw fallback — don't touch, just normalize H2H if present
        return data

    # ── 0) Grass-calibration (7 правок от Accuracy Audit 18.06.2026) ──
    # Применяем ДО основной валидации — чтобы дальнейшие проверки видели
    # уже скорректированную probability.
    try:
        from grass_calibration import apply_grass_calibration
        data, _grass_fixes = apply_grass_calibration(data, sport)
    except ImportError:
        logger.debug("grass_calibration module not available, skipping")
    except Exception as e:
        logger.warning(f"Grass calibration failed: {e}")

    # ── 1) H2H / map_veto / player_status normalization ────────────────
    if "h2h" in data and not isinstance(data["h2h"], str):
        data["h2h"] = _dict_to_text(data["h2h"])
    if "map_veto" in data and isinstance(data["map_veto"], dict):
        # keep as dict (pdf_generator iterates keys), but normalize each value
        for k, v in list(data["map_veto"].items()):
            if not isinstance(v, (str, list)):
                data["map_veto"][k] = _dict_to_text(v)
    if "player_status" in data and isinstance(data["player_status"], dict):
        for k, v in list(data["player_status"].items()):
            if not isinstance(v, str):
                data["player_status"][k] = _dict_to_text(v)

    # ── 2) Probability sanity ─────────────────────────────────────────
    try:
        p = float(data.get("probability", 0.5))
    except (TypeError, ValueError):
        p = 0.5
    p = max(0.05, min(0.95, p))

    # ── 2b) Flip favorite when probability < 0.5 ────────────────────
    # If Claude labelled team1/player1 as favorite=1 but supplied
    # probability < 0.5, the actual favourite is the OTHER side. This is
    # the classic "Legacy фаворит 48%" contradiction seen in the wild.
    # We invert: favorite ↔ other side, probability ↔ 1 - probability.
    if p < 0.5:
        cur_fav = data.get("favorite", 1)
        data["favorite"] = 2 if cur_fav == 1 else 1
        p = 1.0 - p
        logger.info("Validator: flipped favorite (%s → %s) and probability (%.3f → %.3f)",
                    cur_fav, data["favorite"], 1 - p, p)
    data["probability"] = round(p, 3)

    # ── 2c) Factor-consistency check: factors sum must support assigned probability ──
    # If net of all factor shifts strongly pulls toward the underdog (>3%),
    # the bot's narrative contradicts its probability — flip everything so
    # the narrative wins. This catches «G2 62%, but all factors favor Legacy».
    _raw_factors = data.get("factors") or []
    _fav_n, _dog_n, _, _ = _fav_dog_names(data, sport)
    _net = _net_factor_shift_for_fav(_raw_factors, _fav_n, _dog_n)
    if _net < -3 and _raw_factors:
        cur_fav = data.get("favorite", 1)
        data["favorite"] = 2 if cur_fav == 1 else 1
        # Keep probability magnitude — factors were right, just attached to wrong side.
        # If p < 0.5, also invert it so the new fav has > 50 %.
        if data["probability"] < 0.5:
            data["probability"] = round(1 - data["probability"], 3)
        p = data["probability"]
        logger.warning(
            "Validator: factor sum %.1f%% contradicts probability — flipped "
            "favorite (%s → %s); new fav P = %.3f",
            _net, cur_fav, data["favorite"], p,
        )

    # ── 3) Motivation cap (±5 %) ──────────────────────────────────────
    factors = data.get("factors") or []
    motivational = []
    other_total = 0.0
    for f in factors:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name", "")).lower()
        shift_val = _parse_shift_pct(f.get("shift", ""))
        if any(k in name for k in _MOTIV_KEYWORDS):
            motivational.append((f, shift_val))
        else:
            other_total += shift_val

    motiv_sum = sum(v for _, v in motivational)
    if abs(motiv_sum) > 5 and motivational:
        scale = 5 / abs(motiv_sum)
        for f, _ in motivational:
            old = f.get("shift", "")
            new_val = _parse_shift_pct(old) * scale
            sign = "+" if new_val >= 0 else "−"
            f["shift"] = f"{sign}{abs(new_val):.1f}% (capped)"
        logger.info("Motivation factor scaled %.2f → %.2f", motiv_sum, motiv_sum * scale)

    # ── 4) Low-data detection ─────────────────────────────────────────
    low_data = False
    if sport == "cs2":
        r1 = (data.get("team1") or {}).get("hltv_rank")
        r2 = (data.get("team2") or {}).get("hltv_rank")
        if not r1 and not r2:
            low_data = True
    elif sport.startswith("dota"):
        r1 = (data.get("team1") or {}).get("liquipedia_rank")
        r2 = (data.get("team2") or {}).get("liquipedia_rank")
        if (r1 in (None, "", "?", "#?")) and (r2 in (None, "", "?", "#?")):
            low_data = True
        # Tier-3 quals — also low-data
        tname = str(data.get("tournament", "")).lower()
        if "open qualifier" in tname or "tier 3" in tname or "tier-3" in tname:
            low_data = True
    else:  # tennis
        r1 = (data.get("player1") or {}).get("rank")
        r2 = (data.get("player2") or {}).get("rank")
        if not r1 and not r2:
            low_data = True

    # ── 4b) Tournament detection — «Unknown» must trigger low-data ───────
    if not _tournament_known(data.get("tournament", "")):
        low_data = True
        data["_tournament_unknown"] = True

    if low_data:
        data["confidence"] = "Низкая (мало данных — Tier-3, неизвестный турнир или нет рейтингов)"
        # If Claude pretended to be confident in spite of weak data, pull p toward 50/50
        p = data["probability"]
        data["probability"] = round(0.5 + (p - 0.5) * 0.5, 3)

    # ── 4c) Sum-of-factors cap (esports ≤22%, tennis ≤20%) ────────────
    cap = 22 if sport in ("cs2", "dota2") else 20
    if factors:
        data["factors"] = _normalize_factor_sum(factors, cap)
        factors = data["factors"]

    # ── 4d) Scenario format & sum validators ─────────────────────────
    bo = data.get("bo", 3)
    fmt = str(data.get("format", "")).lower()
    if fmt == "bo5" or bo == 5:
        bo_n = 5
    elif fmt == "bo1":
        bo_n = 1
    else:
        bo_n = 3
    if "scenarios" in data and isinstance(data["scenarios"], list):
        data["scenarios"] = _validate_scenarios(data["scenarios"], bo_n)
        data["scenarios"] = _normalize_scenario_probs(data["scenarios"])

    # ── 4e) Resumed match — slash E(total) / duration proportionally ──
    if _detect_resumed_match(data) and isinstance(data.get("distribution"), dict):
        dist = data["distribution"]
        # Assume only ~1 set remaining
        for key in ("e_total",):
            if key in dist and isinstance(dist[key], (int, float)):
                dist[key] = round(dist[key] * 0.40, 1)
        for key in ("e_duration",):
            if key in dist and isinstance(dist[key], (int, float)):
                dist[key] = int(dist[key] * 0.40)
        for key in ("e_fav", "e_dog"):
            if key in dist and isinstance(dist[key], (int, float)):
                dist[key] = round(dist[key] * 0.40, 1)
        data["_resumed"] = True

    # ── 5) Verdict ≡ bar — make sure name AND percentage match the bar ─
    verdict = data.get("verdict", "")
    if isinstance(verdict, str) and verdict:
        actual_pct = round(data["probability"] * 100)
        fav_idx = data.get("favorite", 1)
        # Determine the actual favorite and underdog names
        if sport in ("cs2", "dota2"):
            fav_obj = data.get("team1") if fav_idx == 1 else data.get("team2")
            dog_obj = data.get("team2") if fav_idx == 1 else data.get("team1")
        else:
            fav_obj = data.get("player1") if fav_idx == 1 else data.get("player2")
            dog_obj = data.get("player2") if fav_idx == 1 else data.get("player1")
        fav_name = ""
        dog_name = ""
        for obj, slot in ((fav_obj, "fav"), (dog_obj, "dog")):
            if isinstance(obj, dict):
                n = obj.get("short") or obj.get("name") or ""
                if slot == "fav": fav_name = str(n)
                else: dog_name = str(n)

        # If a flip happened (probability got swapped), the verdict still
        # mentions the old wrong favorite. Replace the underdog's name with
        # the favorite's name when it appears as the "favourite" / "fav 48%".
        if fav_name and dog_name and dog_name in verdict:
            # Match patterns like "Legacy 48%", "Legacy — фаворит", "Legacy фаворит"
            patt = re.compile(
                rf"\b{re.escape(dog_name)}\b\s*"
                rf"(?:[—\-–]?\s*(?:небольшой\s+)?фаворит|"
                rf"\d{{1,3}}\s*%\s*\(?\s*(?:фактически|фаворит)?)",
                re.IGNORECASE,
            )
            verdict = patt.sub(lambda m: m.group(0).replace(dog_name, fav_name, 1), verdict)

        # Strip any "Фактически XX-YY" tail (was a source of contradictions)
        verdict = re.sub(r"\bФактически\s+\d+\s*[-‒–—]\s*\d+\s*\.?", "", verdict).strip()
        # Replace the first stray "NN%" that isn't equal to actual_pct
        def _fix(m):
            v = int(m.group(1))
            return f"{actual_pct}%" if abs(v - actual_pct) > 2 else m.group(0)
        verdict = re.sub(r"(\d{1,3})\s*%", _fix, verdict, count=1)
        data["verdict"] = verdict

    # ── 5b) Confidence badge — strip mention of underdog as favorite ──
    conf = data.get("confidence", "")
    if isinstance(conf, str) and conf and fav_name and dog_name and dog_name in conf:
        # Patterns like "Legacy явные фавориты", "Legacy чемпионы"
        patt2 = re.compile(
            rf"\b{re.escape(dog_name)}\b\s*(?:\w+\s+)?(?:фаворит|favourit|чемпион|preferred|fav)",
            re.IGNORECASE,
        )
        new_conf = patt2.sub(lambda m: m.group(0).replace(dog_name, fav_name, 1), conf)
        if new_conf != conf:
            data["confidence"] = new_conf

    # ── 5c) Stage sanity (Swiss tournament shouldn't say "Quarterfinals") ──
    data = _fix_stage_inconsistency(data, sport)

    # ── 5d) Tier-label sanity (ATP 250 ≠ ТБШ) — tennis only ─────────
    data = _fix_tier_mislabels(data, sport)

    # ── 5e) CS2 roster whitelist — flag/blank wrong star_player ───────
    if sport == "cs2":
        for w in _check_cs2_roster(data):
            logger.warning(w)

    # ── 5f) Translit pass — global Russian-name correction ────────────
    # Apply only to user-visible string fields to avoid touching keys
    for field in ("verdict", "style_analysis", "conditions", "tournament", "stage", "h2h"):
        if field in data and isinstance(data[field], str):
            data[field] = _apply_translit(data[field])
    for p_key in ("player1", "player2", "team1", "team2"):
        if isinstance(data.get(p_key), dict):
            for sub in ("name", "name_en", "star_player", "profile"):
                if sub in data[p_key]:
                    data[p_key][sub] = _translit_walk(data[p_key][sub])
    if isinstance(data.get("factors"), list):
        new_factors = []
        for f in data["factors"]:
            if isinstance(f, dict):
                f = {k: _translit_walk(v) for k, v in f.items()}
            new_factors.append(f)
        data["factors"] = new_factors
    if isinstance(data.get("scenarios"), list):
        data["scenarios"] = _translit_walk(data["scenarios"])

    # ── 6) Factor-count diagnostics (don't fail, just log) ───────────
    n_factors = len([f for f in factors if isinstance(f, dict)])
    if n_factors < 8:
        logger.warning("Only %d factors returned for %s match; methodology asks for ≥8.",
                       n_factors, sport)

    return data


# ═══════════════════════════════════════════════════════════
# MATHEMATICAL MODEL (Bo3 / Bo5)
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# TOTAL GAMES + HANDICAP — точная модель распределения
# ═══════════════════════════════════════════════════════════

def _derive_p_set(p_match: float, bo: int) -> float:
    """
    Численно выводит вероятность выигрыша сета (p_set) из вероятности
    выигрыша матча (p_match) методом бисекции.

    Bo3:  P(win) = p² × (3 - 2p)
    Bo5:  P(win) = p³ × (1 + 3(1-p) + 6(1-p)²)
    """
    p_match = max(0.5, min(0.99, p_match))
    lo, hi = 0.5, 0.99
    for _ in range(50):
        mid = (lo + hi) / 2
        if bo == 5:
            p_win = mid**3 * (1 + 3 * (1 - mid) + 6 * (1 - mid)**2)
        else:
            p_win = mid**2 * (3 - 2 * mid)
        if p_win < p_match:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _set_score_distribution(p_set: float) -> dict:
    """
    Эмпирическое распределение счёта сета — сколько геймов проигравший
    взял в выигранном победителем сете.

    Ключи 0-6 (6-0, 6-1, ..., 7-6).
    Значения — вероятности (сумма = 1).
    """
    # Калибровано на ATP main tour grass-statistics
    if p_set >= 0.85:
        return {0: 0.10, 1: 0.20, 2: 0.25, 3: 0.22, 4: 0.16, 5: 0.05, 6: 0.02}
    elif p_set >= 0.75:
        return {0: 0.06, 1: 0.15, 2: 0.22, 3: 0.25, 4: 0.18, 5: 0.09, 6: 0.05}
    elif p_set >= 0.65:
        return {0: 0.03, 1: 0.10, 2: 0.15, 3: 0.22, 4: 0.25, 5: 0.13, 6: 0.12}
    elif p_set >= 0.55:
        return {0: 0.02, 1: 0.06, 2: 0.10, 3: 0.18, 4: 0.27, 5: 0.18, 6: 0.19}
    else:  # ~0.5
        return {0: 0.01, 1: 0.04, 2: 0.08, 3: 0.13, 4: 0.28, 5: 0.21, 6: 0.25}


def _expected_games_per_set(p_set: float) -> tuple:
    """E(games) и Var(games) на один сет когда вероятность выиграть = p_set."""
    dist = _set_score_distribution(p_set)
    # Геймов в сете: 6+k если 6-k, либо 13 если 7-6 (k=6)
    e_g = 0.0
    for k, prob in dist.items():
        games_in_set = 13 if k == 6 else 6 + k
        e_g += games_in_set * prob
    var_g = 0.0
    for k, prob in dist.items():
        games_in_set = 13 if k == 6 else 6 + k
        var_g += prob * (games_in_set - e_g)**2
    return e_g, var_g


def _set_advantage(p_set: float) -> float:
    """
    Среднее преимущество в геймах когда сет выигран.
    (Победитель сета побеждает с разрывом X геймов в среднем)

    p_set=0.85 → adv ~3.65 (доминирование)
    p_set=0.70 → adv ~3.00 (комфорт)
    p_set=0.55 → adv ~2.27 (близко)
    """
    dist = _set_score_distribution(p_set)
    e_loser = 0.0
    e_winner = 0.0
    for k, prob in dist.items():
        if k < 6:  # 6-k счёт
            e_loser += k * prob
            e_winner += 6 * prob
        else:  # 7-6 TB
            e_loser += 6 * prob
            e_winner += 7 * prob
    return e_winner - e_loser


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """CDF нормального распределения."""
    import math
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    z = (x - mu) / sigma
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def compute_totals_and_handicap(p_match: float, bo: int, distribution: dict) -> dict:
    """
    Точная модель тоталов и фор.

    Возвращает:
      - totals: список словарей {line, p_over, p_under}
      - handicaps: список словарей {line, p_cover}
    """
    p_set_fav = _derive_p_set(p_match, bo)
    p_set_dog = 1 - p_set_fav

    # Ожидаемое число геймов и вариация на один сет
    e_g_fav, var_g_fav = _expected_games_per_set(p_set_fav)
    e_g_dog, var_g_dog = _expected_games_per_set(p_set_dog)

    # Средний gain on set (для каждой стороны): когда сет выигран p_set_fav силой
    e_avg_per_set = (p_set_fav * e_g_fav + p_set_dog * e_g_dog)
    var_avg_per_set = (p_set_fav * var_g_fav + p_set_dog * var_g_dog +
                       p_set_fav * (e_g_fav - e_avg_per_set)**2 +
                       p_set_dog * (e_g_dog - e_avg_per_set)**2)

    # Ожидаемое число сетов сыгранных
    if bo == 5:
        # Сценарии: 3-0(3 сетов), 3-1(4), 3-2(5), 0-3(3), 1-3(4), 2-3(5)
        scenarios = [
            (3, distribution.get('3-0', 0) + distribution.get('0-3', 0)),
            (4, distribution.get('3-1', 0) + distribution.get('1-3', 0)),
            (5, distribution.get('3-2', 0) + distribution.get('2-3', 0)),
        ]
    else:  # bo=3
        scenarios = [
            (2, distribution.get('2-0', 0) + distribution.get('0-2', 0)),
            (3, distribution.get('2-1', 0) + distribution.get('1-2', 0)),
        ]
    total_prob = sum(p for _, p in scenarios) or 1
    scenarios = [(n, p / total_prob) for n, p in scenarios]

    # Распределение полного тотала: смесь нормальных для разного числа сетов
    # Каждый сценарий: N(n_sets × e_avg_per_set, n_sets × var_avg_per_set)
    import math
    mixed_mu = sum(n * e_avg_per_set * p for n, p in scenarios)
    # Для смешанного распределения: var = Σ p_i × (var_i + (mu_i - mu)²)
    mixed_var = 0.0
    for n, p in scenarios:
        mu_i = n * e_avg_per_set
        var_i = n * var_avg_per_set
        mixed_var += p * (var_i + (mu_i - mixed_mu)**2)
    mixed_sigma = math.sqrt(max(mixed_var, 1.0))

    # ── ТОТАЛЫ ──
    # Берём 3 линии: близкая, основная, дальняя
    main_line = round(mixed_mu * 2) / 2  # ближайшие .5
    # Подгоняем линию под популярные значения (xx.5)
    if main_line == int(main_line):
        main_line += 0.5
    lines_to_check = [
        main_line - 3.5,
        main_line - 0.5,
        main_line + 2.5,
    ]

    totals = []
    for line in lines_to_check:
        p_over = 1 - _normal_cdf(line, mixed_mu, mixed_sigma)
        p_under = 1 - p_over
        totals.append({
            "line": line,
            "p_over": round(p_over * 100),
            "p_under": round(p_under * 100),
        })

    # ── ФОРА ПО СЕТАМ ──
    # В Bo5: фора -1.5 (фаворит > 1 сет преимущество)
    #        фора -2.5 (фав > 2 сета — значит 3-0)
    # В Bo3: фора -1.5 (фаворит 2-0)
    handicaps_sets = []
    if bo == 5:
        # -1.5 sets: fav wins by at least 2 sets → 3-0 or 3-1
        p_h15 = distribution.get('3-0', 0) + distribution.get('3-1', 0)
        # -2.5 sets: fav wins by 3 sets → 3-0
        p_h25 = distribution.get('3-0', 0)
        handicaps_sets.append({"line": -1.5, "type": "sets", "p_cover": round(p_h15 * 100)})
        handicaps_sets.append({"line": -2.5, "type": "sets", "p_cover": round(p_h25 * 100)})
    else:
        # -1.5 sets in Bo3: fav wins 2-0
        p_h15 = distribution.get('2-0', 0)
        handicaps_sets.append({"line": -1.5, "type": "sets", "p_cover": round(p_h15 * 100)})

    # ── ФОРА ПО ГЕЙМАМ ──
    # Распределение РАЗНИЦЫ геймов (fav_games - dog_games)
    # ПРАВИЛЬНАЯ модель:
    # - Когда fav выигрывает сет (вероятность p_set_fav): разница +adv_fav
    # - Когда dog выигрывает сет (вероятность p_set_dog): разница -adv_dog
    # E(разница в сете) = p_set_fav × adv_fav - p_set_dog × adv_dog
    # Var(разница в сете) = E[X²] - E[X]²
    adv_fav = _set_advantage(p_set_fav)
    # Когда дог выигрывает сет — он underdog побеждает тесно, преимущество меньше
    # Берём близкую к 0.55 модель (тесный сет)
    adv_dog_when_wins = _set_advantage(max(0.55, p_set_dog))

    e_diff_per_set = p_set_fav * adv_fav - p_set_dog * adv_dog_when_wins
    # E[X²] = p_fav × adv_fav² + p_dog × adv_dog²
    e_diff_sq_per_set = (p_set_fav * adv_fav**2 +
                         p_set_dog * adv_dog_when_wins**2)
    var_diff_per_set = e_diff_sq_per_set - e_diff_per_set**2

    # Полная разница матча: смесь по числу сетов
    e_diff = 0.0
    var_diff = 0.0
    for n_sets, prob in scenarios:
        e_diff_i = n_sets * e_diff_per_set
        var_diff_i = n_sets * var_diff_per_set
        e_diff += prob * e_diff_i
    # Дисперсия смеси
    for n_sets, prob in scenarios:
        e_diff_i = n_sets * e_diff_per_set
        var_diff_i = n_sets * var_diff_per_set
        var_diff += prob * (var_diff_i + (e_diff_i - e_diff)**2)
    sigma_diff = math.sqrt(max(var_diff, 1.0))

    handicaps_games = []
    # Берём диапазон линий и выбираем 2 самых информативных (40-85% покрытия)
    candidate_lines = [-1.5, -2.5, -3.5, -4.5, -5.5, -6.5, -7.5, -8.5, -10.5]
    raw = []
    for h_line in candidate_lines:
        p_cover = 1 - _normal_cdf(-h_line, e_diff, sigma_diff)
        raw.append({
            "line": h_line,
            "type": "games",
            "p_cover": round(p_cover * 100),
        })
    # Отбираем те где покрытие 40-85% (самые интересные для ставки)
    informative = [h for h in raw if 40 <= h["p_cover"] <= 85]
    # Если ни одной — возвращаем все ≤85%
    if not informative:
        informative = [h for h in raw if h["p_cover"] <= 85]
    handicaps_games = informative[:2]  # топ-2

    return {
        "totals": totals,
        "handicaps_sets": handicaps_sets,
        "handicaps_games": handicaps_games,
        "e_total_precise": round(mixed_mu, 1),
        "sigma_total": round(mixed_sigma, 2),
    }


_GRAND_SLAM_KEYWORDS = (
    "wimbledon", "уимблдон", "уимблдона",
    "roland garros", "ролан гаррос", "french open", "ролан",
    "australian open", "австралиан опен", "ао", "australian",
    "us open", "ю эс опен", "юэсопен", "юс опен",
    "grand slam", "гранд слэм", "тбш",
)


def _is_grand_slam(tournament: str) -> bool:
    """Detect Grand Slam tournament — force Bo5 format."""
    if not tournament:
        return False
    t = tournament.lower()
    return any(k in t for k in _GRAND_SLAM_KEYWORDS)


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
            # ── Force Bo5 for Grand Slam tournaments ──
            if _is_grand_slam(data.get("tournament", "")):
                bo = 5
                data["bo"] = 5
            if bo == 5:
                data["distribution"] = bo5_distribution(p)
            else:
                data["distribution"] = bo3_distribution(p)
            return _validate_and_normalize(data, "tennis")

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
    # ── Force Bo5 for Grand Slam tournaments ──
    if _is_grand_slam(data.get("tournament", "")):
        bo = 5
        data["bo"] = 5

    if bo == 5:
        data["distribution"] = bo5_distribution(p)
    else:
        data["distribution"] = bo3_distribution(p)

    return _validate_and_normalize(data, "tennis")


def format_summary(data: dict) -> str:
    """
    Компактный формат «ставка-карточка» для Telegram.

    Структура:
      🎾 Заголовок турнир/раунд
      Победитель: X — Y%
      Тотал геймов: вариант 1 — %, вариант 2 — %, вариант 3 — %
      Фора: вариант 1 — %, вариант 2 — %
      Подробно — в прикреплённом PDF
      ⚠️ Дисклеймер
    """
    # Fallback: if JSON parsing failed, return raw Claude text
    if "_raw_text" in data:
        raw = data["_raw_text"]
        if len(raw) > 3900:
            raw = raw[:3897] + "..."
        return f"🎾 <b>Анализ матча</b>\n\n{raw}\n\n<i>⚠️ Исследовательский анализ</i>"

    p = data.get("probability", 0.5)
    bo = data.get("bo", 5)
    dist = data.get("distribution", {}) or {}
    fav_idx = data.get("favorite", 1)

    p1 = data.get("player1", {}) or {}
    p2 = data.get("player2", {}) or {}

    fav = p1 if fav_idx == 1 else p2
    dog = p2 if fav_idx == 1 else p1

    fav_pct = round(p * 100)
    dog_pct = 100 - fav_pct

    # Build compact message
    lines = []

    # — Header —
    tournament = data.get("tournament", "Tennis")
    round_ = data.get("round", "")
    seed_f = f" [{fav.get('seed', '')}]" if fav.get("seed") else ""
    seed_d = f" [{dog.get('seed', '')}]" if dog.get("seed") else ""
    lines.append(f"🎾 <b>{tournament}</b>{' · ' + round_ if round_ else ''}")
    lines.append(f"<b>{fav.get('name','?')}{seed_f}</b> vs <b>{dog.get('name','?')}{seed_d}</b>")
    lines.append("")

    # — ПОБЕДИТЕЛЬ —
    lines.append(f"🏆 <b>ПОБЕДИТЕЛЬ:</b> {fav.get('name','?')} — <b>{fav_pct}%</b>")
    lines.append("")

    # — Точная модель тоталов и форы —
    try:
        market = compute_totals_and_handicap(p, bo, dist)
    except Exception as _e:
        logger.warning(f"Market model failed: {_e}")
        market = None

    # — ТОТАЛ ГЕЙМОВ —
    if market and market.get("totals"):
        lines.append(f"📊 <b>ТОТАЛ ГЕЙМОВ:</b>")
        for t in market["totals"]:
            line = t["line"]
            p_over = t["p_over"]
            p_under = t["p_under"]
            # Показываем сторону которая чаще выпадает
            if p_over >= p_under:
                lines.append(f"  • Больше {line:.1f} — <b>{p_over}%</b>")
            else:
                lines.append(f"  • Меньше {line:.1f} — <b>{p_under}%</b>")
        lines.append(f"  <i>E(total) = {market.get('e_total_precise', '?')} ± {market.get('sigma_total', '?')}</i>")
        lines.append("")

    # — ФОРА —
    fav_last = fav.get('name', '?').split()[-1] if fav.get('name') else '?'
    if market:
        lines.append(f"⚖️ <b>ФОРА ({fav_last}):</b>")
        # По сетам
        for h in market.get("handicaps_sets", []):
            lines.append(f"  • {h['line']:+.1f} сетов — <b>{h['p_cover']}%</b>")
        # По геймам — топ-2 разумных
        games_filtered = [
            h for h in market.get("handicaps_games", [])
            if 5 <= h["p_cover"] <= 95
        ][:2]
        for h in games_filtered:
            lines.append(f"  • {h['line']:+.1f} геймов — <b>{h['p_cover']}%</b>")
        lines.append("")

    # — Подсказка про PDF —
    lines.append("📄 <b>Подробный разбор — в прикреплённом PDF</b>")
    lines.append("<i>(игроки, мотивация, форма, травмы, факторы, сценарии)</i>")
    lines.append("")

    # — Уверенность прогноза —
    confidence = data.get("confidence", "")
    if confidence:
        # Если строка длинная — берём только первое слово/фразу до запятой
        conf_short = str(confidence).split(",")[0].split(".")[0].strip()
        # Эмодзи в зависимости от уровня уверенности
        conf_lower = conf_short.lower()
        if "очень высок" in conf_lower or "максимал" in conf_lower:
            emoji = "🟢🟢"
        elif "высок" in conf_lower:
            emoji = "🟢"
        elif "средн" in conf_lower or "умеренн" in conf_lower:
            emoji = "🟡"
        elif "низк" in conf_lower:
            emoji = "🔴"
        else:
            emoji = "📌"
        lines.append(f"{emoji} <b>Уверенность прогноза:</b> {conf_short.upper()}")
        lines.append("")

    # — Дисклеймер —
    lines.append("⚠️ <i>Исследовательский анализ, не рекомендация по ставкам</i>")

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
            return _validate_and_normalize(data, "cs2")
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

    return _validate_and_normalize(data, "cs2")


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
            return _validate_and_normalize(data, "dota2")
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
    return _validate_and_normalize(data, "dota2")


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
